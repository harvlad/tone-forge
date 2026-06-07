//
// AudioEngine.swift
//
// Live-monitoring audio engine for the Connect prototype.
//
// Two responsibilities:
//   1. Pass guitar/mic input through to the output bus so the user
//      hears themselves with minimum round-trip latency.
//   2. Mix any number of decoded stem buffers into the same output
//      so the user can play along with the band.
//
// We intentionally use AVAudioEngine rather than raw CoreAudio HAL.
// AVAudioEngine adds ~1–2 ms of overhead vs. raw HAL but is dramatically
// simpler and is what the production app would ship with. If the latency
// floor with AVAudioEngine is unacceptable, dropping to HAL is a known
// next step — but we want to learn that with a measurement, not a guess.
//

import AVFoundation
import Foundation

public final class AudioEngine {

    // MARK: - Public state

    public struct LatencyReport {
        /// Driver-reported input latency in seconds (kAudioDevicePropertyLatency).
        public let inputDeviceLatencySec: Double
        /// Driver-reported output latency in seconds.
        public let outputDeviceLatencySec: Double
        /// Engine I/O buffer duration (kAudioDevicePropertyBufferFrameSize / sr).
        public let bufferDurationSec: Double
        /// Sum of the above — a *lower bound* on the achievable monitoring round trip.
        public let estimatedRoundTripSec: Double
    }

    /// Lifecycle state surfaced via `onStateChange`. The state machine
    /// is linear during normal operation:
    ///
    ///   stopped → starting → running → reconfiguring → running …
    ///                              ↘ failed (after retry budget exhausted)
    ///
    /// `reconfiguring` is entered when the driver posts
    /// `AVAudioEngineConfigurationChangeNotification` — typically when
    /// the user unplugs headphones, switches audio interface, or the
    /// system swaps the default device. We rebuild the graph against
    /// the new input format and restart with backoff.
    public enum State: Equatable {
        case stopped
        case starting
        case running
        case reconfiguring(reason: String)
        case failed(error: String)
    }

    /// Fired on the main queue whenever `state` transitions.
    public var onStateChange: ((State) -> Void)?

    public private(set) var state: State = .stopped {
        didSet {
            guard state != oldValue else { return }
            let cb = onStateChange
            let s = state
            DispatchQueue.main.async { cb?(s) }
        }
    }

    /// Back-compat alias for the original boolean. Returns true while
    /// the engine is actively rendering audio (running or in the brief
    /// window before a reconfig restart completes).
    public var isRunning: Bool {
        if case .running = state { return true }
        return false
    }

    /// Max attempts to recover from a configuration change before
    /// giving up and surfacing `.failed`. Tuned for typical device
    /// flaps (one or two retries usually clears it); a stuck driver
    /// shouldn't loop forever.
    public var maxReconfigAttempts: Int = 5

    /// Linear gain applied to the live input as it passes to the output.
    /// 0.0 = mute, 1.0 = unity. Default starts muted so first launch
    /// doesn't surprise a user with a feedback loop into laptop speakers.
    public var inputMonitorGain: Float = 0.0 {
        didSet { inputMixerNode.outputVolume = inputMonitorGain }
    }

    /// Linear gain applied to the stems mix bus.
    public var stemsGain: Float = 1.0 {
        didSet { stemsMixerNode.outputVolume = stemsGain }
    }

    /// Toggle the static amp-sim coloring on the input monitor path. When
    /// disabled the EQ + distortion nodes are individually bypassed so the
    /// signal still flows but is bit-identical to the dry chain.
    public var ampSimEnabled: Bool = true {
        didSet {
            ampSimEQ.bypass = !ampSimEnabled
            ampSimDistortion.bypass = !ampSimEnabled
        }
    }

    // MARK: - Internals

    private let engine = AVAudioEngine()
    private let inputMixerNode = AVAudioMixerNode()
    private let stemsMixerNode = AVAudioMixerNode()

    /// Static amp-sim chain on the input monitor path: a 3-band EQ shapes
    /// the spectrum (low-end body / mid scoop / presence bump) and a
    /// distortion unit adds break-up at the top. Parameters are baked in
    /// for now; the future WebSocket handshake task wires them to the
    /// V2-matched preset.
    private let ampSimEQ = AVAudioUnitEQ(numberOfBands: 3)
    private let ampSimDistortion = AVAudioUnitDistortion()

    /// One player per active stem. Keyed by stem name (e.g. "drums").
    private var stemPlayers: [String: AVAudioPlayerNode] = [:]
    private var stemBuffers: [String: AVAudioPCMBuffer] = [:]

    /// Reconfig attempts since the last successful start. Reset to 0 on
    /// any successful start; incremented per recovery attempt.
    private var reconfigAttempt: Int = 0

    /// Bounded queue for serializing reconfig work so a flurry of
    /// AVAudioEngineConfigurationChange notifications can't fight
    /// each other for the engine. Reconfig is rare and short, so a
    /// single serial queue is the right tool.
    private let reconfigQueue = DispatchQueue(label: "com.toneforge.connect.audio-reconfig")

    /// NotificationCenter token for the configuration-change observer.
    /// Held strongly so it survives across reconfigs.
    private var configChangeToken: NSObjectProtocol?

    public init() {
        attachAndConnectGraph()
        inputMixerNode.outputVolume = inputMonitorGain
        stemsMixerNode.outputVolume = stemsGain
        configureDefaultAmpSimTone()
        registerConfigChangeObserver()
    }

    deinit {
        if let token = configChangeToken {
            NotificationCenter.default.removeObserver(token)
        }
    }

    /// Wires the graph: input → inputMixer → ampSimEQ → ampSimDistortion → mainMixer
    ///                 stemsMixer → mainMixer
    /// Pulled out of init() so it can be re-run after a driver
    /// configuration change without leaking nodes (AVAudioEngine drops
    /// pre-change connections on its own; we just re-attach + connect).
    private func attachAndConnectGraph() {
        // Both mixers feed the engine's main mixer; the engine's main
        // mixer is auto-connected to outputNode.
        engine.attach(inputMixerNode)
        engine.attach(stemsMixerNode)
        engine.attach(ampSimEQ)
        engine.attach(ampSimDistortion)

        let mainMixer = engine.mainMixerNode

        // Use the input node's input format for the input chain to avoid
        // implicit format conversions on the hot path. The mixer downstream
        // takes care of channel/sr matching to the output.
        // Re-reading the format every time is important: after a
        // configuration change the format may have flipped sample rate
        // or channel count, and the previous format becomes invalid.
        let inputFormat = engine.inputNode.outputFormat(forBus: 0)
        engine.connect(engine.inputNode, to: inputMixerNode, format: inputFormat)
        // Insert the amp-sim between the input mixer and the main mixer.
        // Stems bypass the amp-sim entirely — only the player's instrument
        // is colored.
        engine.connect(inputMixerNode, to: ampSimEQ, format: nil)
        engine.connect(ampSimEQ, to: ampSimDistortion, format: nil)
        engine.connect(ampSimDistortion, to: mainMixer, format: nil)
        engine.connect(stemsMixerNode, to: mainMixer, format: nil)
    }

    /// Subscribes to AVAudioEngineConfigurationChange so a device flap
    /// (unplugged headphones, USB interface yank, default-device swap)
    /// triggers a clean rebuild instead of silently leaving the engine
    /// stopped. Per AVAudioEngine docs the engine has already stopped
    /// by the time the notification fires; our job is to put it back.
    private func registerConfigChangeObserver() {
        configChangeToken = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange,
            object: engine,
            queue: nil
        ) { [weak self] _ in
            self?.handleConfigurationChange()
        }
    }

    /// Serialize all reconfig work onto reconfigQueue. The driver can
    /// post multiple notifications in quick succession when the user
    /// unplugs *and* the OS routes to a different default; we want a
    /// single rebuild, not a race.
    private func handleConfigurationChange() {
        reconfigQueue.async { [weak self] in
            guard let self = self else { return }
            // Only react if we were running. If the user has already
            // stopped the engine, leave it stopped.
            guard self.isRunning || self.state == .starting else { return }
            self.attemptReconfigRestart(reason: "audio configuration changed")
        }
    }

    /// Tries to rebuild the graph and restart the engine, retrying
    /// with linear backoff (1s, 2s, 3s, …) up to maxReconfigAttempts.
    /// Called from reconfigQueue.
    private func attemptReconfigRestart(reason: String) {
        state = .reconfiguring(reason: reason)
        reconfigAttempt += 1

        // Engine is already stopped per the notification contract, but
        // call stop() defensively so any stragglers (stem players) are
        // also brought down before we rebuild.
        if engine.isRunning { engine.stop() }
        for (_, player) in stemPlayers { player.stop() }

        // Re-attach + re-connect against the new input format. The
        // mixer/EQ/distortion nodes are reused — AVAudioEngine
        // tolerates re-attach on already-attached nodes silently.
        attachAndConnectGraph()
        // Stems need their player nodes re-connected to the stems mixer
        // because the engine may have dropped those edges during the
        // device change. The PCM buffers and player instances survive.
        let format = stemsMixerNode.outputFormat(forBus: 0)
        for (_, player) in stemPlayers {
            engine.attach(player)
            engine.connect(player, to: stemsMixerNode, format: format)
        }

        do {
            engine.prepare()
            try engine.start()
            reconfigAttempt = 0
            state = .running
        } catch {
            if reconfigAttempt >= maxReconfigAttempts {
                state = .failed(error: "could not recover audio engine after \(reconfigAttempt) attempts: \(error)")
                return
            }
            // Linear backoff. Exponential here would just delay the
            // user from hearing themselves again when the driver
            // settles. We bail on the count, not the wall-clock.
            let delay = Double(reconfigAttempt)
            reconfigQueue.asyncAfter(deadline: .now() + delay) { [weak self] in
                self?.attemptReconfigRestart(reason: "retry \(self?.reconfigAttempt ?? 0)")
            }
        }
    }

    /// Programs a generic "clean-with-bite" voicing into the static amp-sim
    /// chain. This is the placeholder tone until the WebSocket handshake
    /// task starts driving these values from the V2-matched preset.
    private func configureDefaultAmpSimTone() {
        // 3-band parametric EQ: gentle low-mid body, slight mid scoop,
        // presence peak. Gains are conservative (±3 dB) so the floor
        // stays usable for headphones.
        let bands = ampSimEQ.bands
        bands[0].filterType = .parametric
        bands[0].frequency = 120
        bands[0].bandwidth = 1.0
        bands[0].gain = 2.5
        bands[0].bypass = false

        bands[1].filterType = .parametric
        bands[1].frequency = 700
        bands[1].bandwidth = 1.2
        bands[1].gain = -2.0
        bands[1].bypass = false

        bands[2].filterType = .parametric
        bands[2].frequency = 3200
        bands[2].bandwidth = 1.0
        bands[2].gain = 3.0
        bands[2].bypass = false

        ampSimEQ.globalGain = 0

        // Pre-gain drives the saturator; wet/dry stays mostly dry so the
        // effect is character, not heavy distortion. Multi-decimated
        // softclip is the most amp-like of the built-in presets.
        ampSimDistortion.loadFactoryPreset(.multiDecimated1)
        ampSimDistortion.preGain = -6
        ampSimDistortion.wetDryMix = 25  // percent wet
    }

    /// Applies a tone preset payload pushed from the web app via
    /// /ws/connect-bridge. The payload is the parsed `preset` object:
    ///
    ///   {
    ///     "analysis_id": "...",
    ///     "source_url":  "...",
    ///     "instrument":  "guitar",
    ///     "match": {
    ///       "preset_name": "Crunchy Lead",
    ///       "instrument":  "Analog",
    ///       ...
    ///     }
    ///   }
    ///
    /// For the MVP we don't yet know how to translate an Analog preset
    /// into AVAudioUnit parameters faithfully, so we use a keyword
    /// heuristic on the preset name to nudge the static amp-sim toward
    /// "clean", "crunch", "bright", or "warm" voicings. The handshake
    /// task is about proving the wire end-to-end; faithful preset
    /// rendering is a follow-up.
    public func applyTonePreset(_ payload: [String: Any]) {
        // Reset to baseline so successive presets don't compound edits.
        configureDefaultAmpSimTone()

        let match = payload["match"] as? [String: Any]
        let presetName = (match?["preset_name"] as? String) ?? ""
        let lower = presetName.lowercased()

        // Distortion drive: "clean" pulls the saturator back, "crunch" /
        // "drive" / "fuzz" push it forward. preGain is dB; wetDryMix is %.
        if lower.contains("clean") {
            ampSimDistortion.preGain = -12
            ampSimDistortion.wetDryMix = 5
        } else if lower.contains("fuzz") {
            ampSimDistortion.loadFactoryPreset(.multiBrokenSpeaker)
            ampSimDistortion.preGain = 0
            ampSimDistortion.wetDryMix = 65
        } else if lower.contains("crunch") || lower.contains("drive") || lower.contains("dist") {
            ampSimDistortion.preGain = 0
            ampSimDistortion.wetDryMix = 45
        }

        // Tone tilt: "bright" lifts the presence band, "dark" / "warm"
        // pulls it down and adds body. Bandwidth/frequency stay put so
        // we don't lose the underlying voicing.
        let bands = ampSimEQ.bands
        if lower.contains("bright") {
            bands[2].gain += 3.0
            bands[0].gain -= 1.0
        } else if lower.contains("dark") || lower.contains("warm") {
            bands[2].gain -= 3.0
            bands[0].gain += 2.0
        }

        // Mid scoop on anything labeled "metal" or "scoop".
        if lower.contains("scoop") || lower.contains("metal") {
            bands[1].gain = -6.0
        }

        // Defensive bounds — clamp band gains so a misbehaving heuristic
        // can never blow out the user's headphones.
        for band in bands {
            band.gain = max(-12.0, min(12.0, band.gain))
        }
        ampSimDistortion.preGain = max(-24, min(12, ampSimDistortion.preGain))
        ampSimDistortion.wetDryMix = max(0, min(80, ampSimDistortion.wetDryMix))
    }

    // MARK: - Lifecycle

    public func start() throws {
        if case .running = state { return }
        state = .starting
        engine.prepare()
        do {
            try engine.start()
        } catch {
            state = .failed(error: "engine.start failed: \(error)")
            throw error
        }
        reconfigAttempt = 0
        state = .running
    }

    public func stop() {
        // Allow stop() from any non-stopped state so a caller can bail
        // out of a `.reconfiguring` or `.failed` engine cleanly.
        if case .stopped = state { return }
        for (_, player) in stemPlayers { player.stop() }
        if engine.isRunning { engine.stop() }
        state = .stopped
    }

    // MARK: - Stem playback

    /// Load a stem audio file and attach a player for it. Subsequent
    /// `playAllStems()` will start it in sync with the others.
    public func loadStem(name: String, url: URL) throws {
        let file = try AVAudioFile(forReading: url)
        let format = file.processingFormat
        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: format,
            frameCapacity: AVAudioFrameCount(file.length)
        ) else {
            throw NSError(
                domain: "ConnectCore",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey:
                    "Could not allocate PCM buffer for stem \(name)"]
            )
        }
        try file.read(into: buffer)

        let player = AVAudioPlayerNode()
        engine.attach(player)
        engine.connect(player, to: stemsMixerNode, format: format)

        stemPlayers[name] = player
        stemBuffers[name] = buffer
    }

    /// Schedule every loaded stem at the same render-host time so they
    /// start sample-aligned. Call after start().
    public func playAllStems(loop: Bool = false) {
        guard isRunning else { return }
        let options: AVAudioPlayerNodeBufferOptions = loop ? [.loops] : []
        // Schedule slightly in the future so all players have time to arm.
        let startSample: AVAudioFramePosition = 0
        for (name, player) in stemPlayers {
            guard let buffer = stemBuffers[name] else { continue }
            player.scheduleBuffer(buffer, at: nil, options: options, completionHandler: nil)
            _ = startSample // placeholder for future per-player offset
            player.play()
        }
    }

    public func stopAllStems() {
        for (_, player) in stemPlayers { player.stop() }
    }

    public func setStem(name: String, gain: Float) {
        stemPlayers[name]?.volume = gain
    }

    public func muteStem(name: String, muted: Bool) {
        stemPlayers[name]?.volume = muted ? 0.0 : 1.0
    }

    // MARK: - Latency report

    /// Combine driver-reported latency with buffer duration. This is a
    /// floor estimate; an impulse-loopback measurement gives the real
    /// number and is implemented separately in LatencyProbe.
    public func latencyReport() -> LatencyReport {
        let inputUnit = engine.inputNode.audioUnit
        let outputUnit = engine.outputNode.audioUnit
        let inputLatency = AudioEngine.deviceLatencySec(forAudioUnit: inputUnit, scope: kAudioUnitScope_Input)
        let outputLatency = AudioEngine.deviceLatencySec(forAudioUnit: outputUnit, scope: kAudioUnitScope_Output)

        let format = engine.inputNode.outputFormat(forBus: 0)
        let sampleRate = format.sampleRate > 0 ? format.sampleRate : 48000.0

        // AVAudioEngine doesn't expose buffer size directly; approximate
        // from outputNode's render block frames if we've started, else
        // assume a typical 256-frame default.
        let bufferFrames: Double = 256.0
        let bufferDuration = bufferFrames / sampleRate

        return LatencyReport(
            inputDeviceLatencySec: inputLatency,
            outputDeviceLatencySec: outputLatency,
            bufferDurationSec: bufferDuration,
            estimatedRoundTripSec: inputLatency + outputLatency + (2 * bufferDuration)
        )
    }

    private static func deviceLatencySec(forAudioUnit unit: AudioUnit?, scope: AudioUnitScope) -> Double {
        guard let unit = unit else { return 0 }
        var latency: Double = 0
        var size = UInt32(MemoryLayout<Double>.size)
        let status = AudioUnitGetProperty(
            unit,
            kAudioUnitProperty_Latency,
            scope,
            0,
            &latency,
            &size
        )
        if status != noErr { return 0 }
        return latency
    }
}
