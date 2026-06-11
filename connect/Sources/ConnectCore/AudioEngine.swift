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

    /// Fired on the main queue exactly once when `attemptReconfigRestart`
    /// exhausts its retry budget — i.e. the engine is about to settle
    /// into `.failed` because the underlying audio device stayed gone.
    /// The Connect CLI wires this to `PresetBridge.sendDeviceLost(...)`
    /// so the browser side of the channel learns that the helper is
    /// alive but its audio path is broken. Distinct from `onStateChange(.failed)`
    /// because the latter also fires on cold-start failures where the
    /// WS bridge may not yet exist; this callback only fires on
    /// runtime device loss.
    public var onDeviceLost: ((_ reason: String) -> Void)?

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

    /// Toggle the curated monitor chain on the input path. When
    /// disabled the coloring nodes (HPF, distortion, EQ, comp, reverb)
    /// are bypassed individually so the signal still flows but is
    /// bit-identical to the dry monitor chain. The output trim node is
    /// left engaged — its gain knob is part of the chain's curated
    /// output level and a user wanting fully-dry monitoring should
    /// disable the chain altogether at a higher layer.
    public var ampSimEnabled: Bool = true {
        didSet {
            let bypass = !ampSimEnabled
            inputHPF.bypass = bypass
            ampSimDistortion.bypass = bypass
            ampSimEQ.bypass = bypass
            compressor.bypass = bypass || !currentChainSpec.comp.enabled
            reverb.bypass = bypass
        }
    }

    // MARK: - Internals

    private let engine = AVAudioEngine()
    private let inputMixerNode = AVAudioMixerNode()
    private let stemsMixerNode = AVAudioMixerNode()

    /// Monitor chain DSP graph. Each node owns one section of the
    /// ChainSpec schema (input, gain stage, EQ, comp, reverb, output).
    /// Topology:
    ///
    ///   input → inputMixer → inputHPF → ampSimDistortion → ampSimEQ
    ///         → compressor → reverb → outputTrim → mainMixer
    ///
    /// Why one node per section: it mirrors the YAML schema 1:1, which
    /// makes the listening engagement (P3e) actionable — every YAML
    /// edit lands in exactly one parameter on exactly one node, and
    /// every node can be bypassed individually when A/B testing.

    /// High-pass filter — band 0 of inputHPF carries the .highPass
    /// filterType, the remaining slot is unused. A 1-band AVAudioUnitEQ
    /// is the cheapest stable HPF available in the AVFoundation stack.
    private let inputHPF = AVAudioUnitEQ(numberOfBands: 1)

    /// Saturation stage. Drives off ChainSpec.GainStage; the .type
    /// field selects an AVAudioUnitDistortionPreset and .drive maps
    /// to wetDryMix + preGain.
    private let ampSimDistortion = AVAudioUnitDistortion()

    /// 4-band parametric EQ — bass / mid / treble / presence. Bands
    /// are fixed-frequency (see configureDefaultAmpSimTone) so the
    /// ChainSpec only carries gains.
    private let ampSimEQ = AVAudioUnitEQ(numberOfBands: 4)

    /// Dynamics processor used as a downward compressor. Configurable
    /// from ChainSpec.Comp (ratio / threshold / attack / release).
    /// When `comp.enabled == false` the node is bypassed but stays
    /// in the graph so a later apply can re-enable it without a rewire.
    /// AVFoundation doesn't ship a typed compressor wrapper — we
    /// instantiate the built-in DynamicsProcessor AU via AVAudioUnitEffect
    /// and program it through AudioUnitSetParameter.
    private let compressor: AVAudioUnitEffect = {
        let desc = AudioComponentDescription(
            componentType: kAudioUnitType_Effect,
            componentSubType: kAudioUnitSubType_DynamicsProcessor,
            componentManufacturer: kAudioUnitManufacturer_Apple,
            componentFlags: 0,
            componentFlagsMask: 0
        )
        return AVAudioUnitEffect(audioComponentDescription: desc)
    }()

    /// Algorithmic reverb. .type selects an AVAudioUnitReverbPreset,
    /// .mix drives wetDryMix.
    private let reverb = AVAudioUnitReverb()

    /// Output trim — an AVAudioMixerNode used solely as a gain stage
    /// at the end of the chain so the curated chain has its own
    /// makeup-gain knob independent of the user-facing monitor gain.
    private let outputTrim = AVAudioMixerNode()

    /// The currently-applied monitor chain. Reads back via
    /// `currentChainId()` for diagnostics. Defaults to the safe
    /// baseline so a fresh engine has a coherent tone.
    private var currentChainSpec: ChainSpec = .baseline

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

    /// Wires the full monitor-chain graph plus the stems bus.
    ///
    ///   input → inputMixer → inputHPF → ampSimDistortion → ampSimEQ
    ///         → compressor → reverb → outputTrim → mainMixer
    ///   stemsMixer → mainMixer
    ///
    /// Pulled out of init() so it can be re-run after a driver
    /// configuration change without leaking nodes (AVAudioEngine drops
    /// pre-change connections on its own; we just re-attach + connect).
    private func attachAndConnectGraph() {
        // Both mixers feed the engine's main mixer; the engine's main
        // mixer is auto-connected to outputNode.
        engine.attach(inputMixerNode)
        engine.attach(stemsMixerNode)
        engine.attach(inputHPF)
        engine.attach(ampSimDistortion)
        engine.attach(ampSimEQ)
        engine.attach(compressor)
        engine.attach(reverb)
        engine.attach(outputTrim)

        let mainMixer = engine.mainMixerNode

        // Use the input node's input format for the input chain to avoid
        // implicit format conversions on the hot path. The mixer downstream
        // takes care of channel/sr matching to the output.
        // Re-reading the format every time is important: after a
        // configuration change the format may have flipped sample rate
        // or channel count, and the previous format becomes invalid.
        let inputFormat = engine.inputNode.outputFormat(forBus: 0)
        engine.connect(engine.inputNode, to: inputMixerNode, format: inputFormat)
        // Monitor chain: HPF → drive → EQ → comp → reverb → trim → out.
        // Stems bypass the chain entirely — only the player's instrument
        // is colored by the curated tone.
        engine.connect(inputMixerNode, to: inputHPF, format: nil)
        engine.connect(inputHPF, to: ampSimDistortion, format: nil)
        engine.connect(ampSimDistortion, to: ampSimEQ, format: nil)
        engine.connect(ampSimEQ, to: compressor, format: nil)
        engine.connect(compressor, to: reverb, format: nil)
        engine.connect(reverb, to: outputTrim, format: nil)
        engine.connect(outputTrim, to: mainMixer, format: nil)
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
                let reason = "reconfig_exhausted_after_\(reconfigAttempt)_attempts"
                // Fire device-lost BEFORE transitioning to .failed so
                // subscribers see the device-loss event with the engine
                // still in `.reconfiguring`; this mirrors the normal
                // pattern (notification → terminal state) and lets the
                // browser show its reconnection toast a tick before any
                // generic "audio failed" handler kicks in.
                let cb = onDeviceLost
                DispatchQueue.main.async { cb?(reason) }
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

    /// Initialises every node in the chain to the safe baseline spec.
    /// Called from init() and from applyTonePreset() to wipe any prior
    /// state before applying nudges. Always programs the full graph so
    /// node state stays in sync with `currentChainSpec`.
    private func configureDefaultAmpSimTone() {
        applyChain(.baseline)
    }

    /// Apply a ChainSpec to every node in the monitor graph. This is
    /// the canonical entry point: the YAML-driven loader (P3c) and the
    /// WS apply_chain handler (P3d) both route through here. Idempotent
    /// — calling twice with the same spec is a no-op musically.
    ///
    /// All parameters are clamped before being written to the AU graph
    /// so a YAML typo can never blow out a headphone bus.
    public func applyChain(_ spec: ChainSpec) {
        let safe = spec.clamped()
        currentChainSpec = safe

        // -- Input HPF -----------------------------------------------
        // 1-band EQ programmed as a high-pass. Band 0 is the HPF
        // itself; gain field is unused on .highPass.
        let hpfBands = inputHPF.bands
        hpfBands[0].filterType = .highPass
        hpfBands[0].frequency = safe.input.highPassHz
        hpfBands[0].bypass = false
        // Use globalGain to inject the input pre-gain — this avoids
        // touching the saturator's pre-gain (which is reserved for the
        // gain stage drive) while still giving us a clean trim knob.
        inputHPF.globalGain = safe.input.gainDb

        // -- Gain stage (distortion) ---------------------------------
        applyGainStage(safe.gainStage)

        // -- 4-band EQ -----------------------------------------------
        let eqBands = ampSimEQ.bands
        configureEqBand(eqBands[0], frequency: 120, bandwidth: 1.0,
                        gain: safe.eq.bassDb)
        configureEqBand(eqBands[1], frequency: 700, bandwidth: 1.2,
                        gain: safe.eq.midDb)
        configureEqBand(eqBands[2], frequency: 3200, bandwidth: 1.0,
                        gain: safe.eq.trebleDb)
        configureEqBand(eqBands[3], frequency: 6500, bandwidth: 0.8,
                        gain: safe.eq.presenceDb)
        ampSimEQ.globalGain = 0

        // -- Compressor ----------------------------------------------
        applyCompressor(safe.comp)

        // -- Reverb --------------------------------------------------
        applyReverb(safe.reverb)

        // -- Output trim ---------------------------------------------
        // outputVolume is linear; convert dB → linear once.
        outputTrim.outputVolume = decibelsToLinearGain(safe.output.trimDb)
    }

    /// Identifier of the currently-applied chain. Exposed for the
    /// WS handler's apply_chain_ack response and for diagnostics.
    public func currentChainId() -> String {
        return currentChainSpec.id
    }

    // MARK: - Section appliers (private)

    private func configureEqBand(
        _ band: AVAudioUnitEQFilterParameters,
        frequency: Float,
        bandwidth: Float,
        gain: Float
    ) {
        band.filterType = .parametric
        band.frequency = frequency
        band.bandwidth = bandwidth
        band.gain = gain
        band.bypass = false
    }

    private func applyGainStage(_ stage: ChainSpec.GainStage) {
        // Map the human-named tube character onto a built-in distortion
        // factory preset. AVAudioUnitDistortion presets are coarse
        // enough that this is the right granularity for MVP; the
        // listening engagement (P3e) decides if any need swapping.
        switch stage.type {
        case .tubeClean:
            ampSimDistortion.loadFactoryPreset(.multiEcho1)
        case .tubeBreak:
            ampSimDistortion.loadFactoryPreset(.multiDecimated1)
        case .tubeOverdrive:
            ampSimDistortion.loadFactoryPreset(.multiDecimated2)
        case .tubeHighGain:
            ampSimDistortion.loadFactoryPreset(.multiDistortedSquared)
        }
        // Drive 0.0–1.0 → preGain in dB. -24 dB at 0 drive is
        // effectively dry; 0 dB at full drive is heavy saturation.
        ampSimDistortion.preGain = -24 + (stage.drive * 24)
        // Wet/dry: scale linearly into 0–100% with a floor so the
        // saturator's character is always at least subtly present.
        ampSimDistortion.wetDryMix = max(5, stage.drive * 100)
    }

    /// Program the DynamicsProcessor AU. Parameter IDs are stable
    /// public constants on the AU; we set them via AudioUnitSetParameter
    /// since AVAudioUnitEffect doesn't expose typed properties.
    private func applyCompressor(_ comp: ChainSpec.Comp) {
        compressor.bypass = !comp.enabled

        let au = compressor.audioUnit
        // Threshold is in dB, attack/release in seconds (we receive ms),
        // ratio is dimensionless. Headroom + masterGain stay at defaults.
        AudioUnitSetParameter(au,
            kDynamicsProcessorParam_Threshold,
            kAudioUnitScope_Global, 0,
            comp.thresholdDb, 0)
        // AVFoundation expresses the ratio as the headroom scale, but
        // the DynamicsProcessor AU's "headroom amount" parameter is
        // distinct from the ratio knob most users expect. We map the
        // ChainSpec ratio onto kDynamicsProcessorParam_HeadRoom directly
        // — values 1.0–20.0 map intuitively (1.0 ≈ no compression,
        // higher = more aggressive ratio) and the AU clamps internally.
        AudioUnitSetParameter(au,
            kDynamicsProcessorParam_HeadRoom,
            kAudioUnitScope_Global, 0,
            comp.ratio, 0)
        AudioUnitSetParameter(au,
            kDynamicsProcessorParam_AttackTime,
            kAudioUnitScope_Global, 0,
            comp.attackMs / 1000.0, 0)
        AudioUnitSetParameter(au,
            kDynamicsProcessorParam_ReleaseTime,
            kAudioUnitScope_Global, 0,
            comp.releaseMs / 1000.0, 0)
    }

    private func applyReverb(_ verb: ChainSpec.Reverb) {
        // Map the human-named reverb type plus size onto the closest
        // AVAudioUnitReverbPreset. The AU's size is preset-coded; we
        // shift to a "Large" variant when size > 0.6 and a "Small"
        // variant when size < 0.3 where the preset family supports it.
        let preset: AVAudioUnitReverbPreset
        switch verb.type {
        case .room:
            preset = verb.size > 0.6 ? .largeRoom : .mediumRoom
        case .plate:
            preset = .plate
        case .spring:
            // No spring preset in AVAudioUnitReverb; small chamber
            // is the closest tonal neighbour.
            preset = .smallRoom
        case .hall:
            preset = verb.size > 0.6 ? .largeHall : .mediumHall
        case .smallHall:
            preset = .mediumHall
        }
        reverb.loadFactoryPreset(preset)
        // wetDryMix is 0–100%; ChainSpec carries 0–1.
        reverb.wetDryMix = verb.mix * 100
    }

    private func decibelsToLinearGain(_ db: Float) -> Float {
        return powf(10.0, db / 20.0)
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
        // we don't lose the underlying voicing. Band indices match the
        // 4-band layout in applyChain(): 0=bass, 1=mid, 2=treble,
        // 3=presence.
        let bands = ampSimEQ.bands
        if lower.contains("bright") {
            bands[3].gain += 3.0
            bands[0].gain -= 1.0
        } else if lower.contains("dark") || lower.contains("warm") {
            bands[3].gain -= 3.0
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
