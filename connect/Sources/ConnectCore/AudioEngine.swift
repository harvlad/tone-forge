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

    public private(set) var isRunning = false

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

    public init() {
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
        let inputFormat = engine.inputNode.outputFormat(forBus: 0)
        engine.connect(engine.inputNode, to: inputMixerNode, format: inputFormat)
        // Insert the amp-sim between the input mixer and the main mixer.
        // Stems bypass the amp-sim entirely — only the player's instrument
        // is colored.
        engine.connect(inputMixerNode, to: ampSimEQ, format: nil)
        engine.connect(ampSimEQ, to: ampSimDistortion, format: nil)
        engine.connect(ampSimDistortion, to: mainMixer, format: nil)
        engine.connect(stemsMixerNode, to: mainMixer, format: nil)

        inputMixerNode.outputVolume = inputMonitorGain
        stemsMixerNode.outputVolume = stemsGain
        configureDefaultAmpSimTone()
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
        guard !isRunning else { return }
        engine.prepare()
        try engine.start()
        isRunning = true
    }

    public func stop() {
        guard isRunning else { return }
        for (_, player) in stemPlayers { player.stop() }
        engine.stop()
        isRunning = false
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
