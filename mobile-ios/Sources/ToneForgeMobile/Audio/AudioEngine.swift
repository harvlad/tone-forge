// AudioEngine.swift
//
// Top-level audio engine wrapper. Owns the shared AVAudioEngine, the
// TransportClock, the contribution bus topology, and the ONE shared
// reverb every contribution source rides (DECISIONS.md D-013). All
// audio-graph plumbing (attach + connect) lives here; downstream
// nodes only know about their own AVAudioNode instances.
//
// Graph topology (D-013):
//
//   PadSynth.voiceMixer ──┐
//   WavetableSynthNode ───┼→ voiceBus (0.9 = voiceGainLinear)
//   MicMonitor (P3) ──────┘        │
//   SampleVoicePool → SampleBus ──→ chopBus (0.55 = chopGainLinear)
//   VocoderMonitor (P5) ──────────→ vocoderBus (0.4 = vocoderGainLinear)
//                                     │
//   voiceBus + chopBus + vocoderBus → sharedBus (volume = layerFaderDb)
//   sharedBus → dryMixer(dryGain) ─────────────→ mainMixer → output
//   sharedBus → sharedReverb(wet 100) → wetMixer(wetGain) ─┘
//   StemPlayer.mixer ─────────────────────────→ mainMixer (bypasses
//                                                sharedBus, as v1 —
//                                                muting "Your Layer"
//                                                keeps the song audible)
//
// Every explicit connect uses ``canonicalFormat`` (48 kHz stereo,
// D-017). The mainMixer → output hop is the one place the engine may
// SRC (hardware boundary — unavoidable). Stems are the documented
// exception (D-014): they stay at source rate and get converted by
// the engine at their mainMixer edge.
//
// `buildContributionGraph()` is called from bootAudio BEFORE
// `engine.start()` — attaching + connecting on a running engine trips
// AVAudioEngine's graph validator when an intermediate node's
// upstream isn't populated yet.

import Foundation
#if canImport(AVFoundation)
import AVFoundation
#endif

/// Owns the AVAudioEngine plus the clock and subsystems that all use
/// it. Kept as a reference type because AVAudioEngine is class-based
/// and the SwiftUI layer holds it via @StateObject.
@MainActor
public final class AudioEngine: ObservableObject {

    /// Emitted for UI to reflect play/pause state.
    @Published public private(set) var isRunning: Bool = false

    public let clock = TransportClock()
    public let session: AudioSessionController

    // MARK: - Shared reverb params

    /// Knobs for the single shared reverb (D-013). PadSynth and
    /// SampleBus used to own private dry/wet branches; now one reverb
    /// serves every contribution source, so the Settings sliders
    /// drive this instead of PadSynthParams.
    public struct ReverbParams: Sendable, Equatable {
        /// Direct-signal gain, 0…1 linear.
        public var dryGain: Float
        /// Reverb-tail gain, 0…1 linear.
        public var wetGain: Float
        /// Requested tail length in seconds — mapped onto the nearest
        /// AVAudioUnitReverb factory preset (no continuous parameter).
        public var seconds: Double

        public init(dryGain: Float = 0.9, wetGain: Float = 0.3, seconds: Double = 2.0) {
            self.dryGain = dryGain
            self.wetGain = wetGain
            self.seconds = seconds
        }
    }

    /// Current shared-reverb settings. Mutate via ``setReverbParams``.
    @Published public private(set) var reverbParams = ReverbParams()

    #if canImport(AVFoundation)
    /// The shared AVAudioEngine. Exposed so downstream subsystems
    /// (StemPlayer, PadSynth) can attach their own nodes.
    public let engine = AVAudioEngine()

    /// Canonical processing rate (D-017). Every contribution-path
    /// connect happens at this format; the single resample point is
    /// SampleScheduler's ingest.
    public static let canonicalSampleRate: Double = 48_000

    /// 48 kHz stereo Float32 — the one format the contribution graph
    /// speaks.
    public let canonicalFormat = AVAudioFormat(
        standardFormatWithSampleRate: AudioEngine.canonicalSampleRate,
        channels: 2
    )!

    // Contribution buses (see topology diagram above). Built once by
    // `buildContributionGraph()`.
    private var voiceBus: AVAudioMixerNode?
    private var chopBus: AVAudioMixerNode?
    private var vocoderBus: AVAudioMixerNode?
    private var sharedBus: AVAudioMixerNode?
    private var dryMixer: AVAudioMixerNode?
    private var sharedReverb: AVAudioUnitReverb?
    private var wetMixer: AVAudioMixerNode?

    /// Destination for synth-voice sources (PadSynth, WavetableSynthNode,
    /// MicMonitor). Builds the graph on first access so ordering
    /// against bootAudio is forgiving.
    public var voiceBusInput: AVAudioNode {
        buildContributionGraph()
        return voiceBus!
    }

    /// Destination for the sample/chop path (SampleBus.voiceMixer).
    public var chopBusInput: AVAudioNode {
        buildContributionGraph()
        return chopBus!
    }

    /// Destination for the vocoder live-preview monitor (P5). Silent
    /// until then — the bus exists from P1 so gain persistence and
    /// topology tests cover it early.
    public var vocoderBusInput: AVAudioNode {
        buildContributionGraph()
        return vocoderBus!
    }

    /// Build the contribution bus topology on the idle engine.
    /// Idempotent — safe to call from accessors and bootAudio in any
    /// order.
    public func buildContributionGraph() {
        guard sharedBus == nil else { return }

        let voice = AVAudioMixerNode()
        let chop = AVAudioMixerNode()
        let vocoder = AVAudioMixerNode()
        let shared = AVAudioMixerNode()
        let dry = AVAudioMixerNode()
        let verb = AVAudioUnitReverb()
        let wet = AVAudioMixerNode()

        verb.wetDryMix = 100 // full wet on the wet branch; balance via wetMixer
        verb.loadFactoryPreset(Self.presetForSeconds(reverbParams.seconds))

        for node in [voice, chop, vocoder, shared, dry, wet] {
            engine.attach(node)
        }
        engine.attach(verb)

        let format = canonicalFormat
        // AVAudioMixerNode auto-picks a free input bus per connect, so
        // fanning three buses INTO sharedBus is safe with sequential
        // connects.
        engine.connect(voice, to: shared, format: format)
        engine.connect(chop, to: shared, format: format)
        engine.connect(vocoder, to: shared, format: format)
        // Fan sharedBus OUT to both the dry and reverb branches in a
        // single connect() call. Two sequential connects would silently
        // drop the first edge — AVAudioEngine's connect() replaces any
        // existing connection on the source's output bus 0 (see the
        // trap documented at SampleBus.swift's original fan-out).
        engine.connect(
            shared,
            to: [
                AVAudioConnectionPoint(node: dry, bus: 0),
                AVAudioConnectionPoint(node: verb, bus: 0)
            ],
            fromBus: 0,
            format: format
        )
        engine.connect(verb, to: wet, format: format)
        engine.connect(dry, to: engine.mainMixerNode, format: format)
        engine.connect(wet, to: engine.mainMixerNode, format: format)

        // Loudness-neutral defaults (overwritten by the persisted
        // values when wireSampleSettings' sinks fire on subscribe).
        voice.outputVolume = Float(SampleSettingsStore.defaultVoiceGain)
        chop.outputVolume = Float(SampleSettingsStore.defaultChopGain)
        vocoder.outputVolume = Float(SampleSettingsStore.defaultVocoderGain)
        shared.outputVolume = 1.0
        dry.outputVolume = reverbParams.dryGain
        wet.outputVolume = reverbParams.wetGain

        self.voiceBus = voice
        self.chopBus = chop
        self.vocoderBus = vocoder
        self.sharedBus = shared
        self.dryMixer = dry
        self.sharedReverb = verb
        self.wetMixer = wet
    }
    #endif

    /// Consumers of session events subscribe here so they can pause on
    /// interruption etc. Kicked off in ``start()``.
    private var sessionEventTask: Task<Void, Never>?

    public init(session: AudioSessionController? = nil) {
        // The default arg can't call a `@MainActor` init from an
        // implicit non-actor context, so we build the session lazily
        // here. `self` is already @MainActor, so this is fine.
        self.session = session ?? AudioSessionController()
    }

    // MARK: - Lifecycle

    /// Boot the engine. Activates the session, wires the main mixer to
    /// output, starts observing interruptions, and starts the engine.
    /// Safe to call multiple times.
    public func start() {
        session.activate()
        session.preferLowLatency()

        #if canImport(AVFoundation)
        if !engine.isRunning {
            // Touch the main mixer so it stays connected to output even
            // when no upstream nodes are attached yet.
            _ = engine.mainMixerNode

            do {
                try engine.start()
                isRunning = true
            } catch {
                print("[AudioEngine] start failed: \(error)")
                isRunning = false
                return
            }
        }
        #endif

        startSessionEventObserver()
    }

    /// Stop the engine and tear down the session. Called on scene
    /// disappearance or when the app is backgrounded for long enough
    /// that iOS suspends us.
    public func stop() {
        clock.stop()
        #if canImport(AVFoundation)
        if engine.isRunning {
            engine.stop()
        }
        #endif
        isRunning = false
        session.deactivate()
        sessionEventTask?.cancel()
        sessionEventTask = nil
    }

    // MARK: - Transport surface

    public func play() {
        #if canImport(AVFoundation)
        if !engine.isRunning {
            // Touch the main mixer first — same as start(). An engine
            // with no nodes referenced yet has neither input nor
            // output and raises an unswallowable NSException from
            // start() (hit by headless tests that play() without
            // bootAudio).
            _ = engine.mainMixerNode
            do { try engine.start() } catch {
                print("[AudioEngine] play → start failed: \(error)")
                return
            }
        }
        #endif
        clock.play()
        isRunning = true
    }

    public func pause() {
        clock.pause()
        // We don't stop the AVAudioEngine here — it stays running with
        // silence so touch-to-audio latency doesn't spike on resume.
    }

    public func seek(to seconds: Double) {
        clock.seek(to: seconds)
    }

    // MARK: - Gain surface

    /// Set the main mixer's output volume. 0..1 linear.
    public func setMasterGain(_ gain: Float) {
        #if canImport(AVFoundation)
        engine.mainMixerNode.outputVolume = max(0, min(1, gain))
        #endif
    }

    /// Synth-voice bus level (PadSynth + WavetableSynthNode). 0..1
    /// linear, driven by SampleSettingsStore.voiceGainLinear.
    public func setVoiceGain(_ gain: Float) {
        #if canImport(AVFoundation)
        buildContributionGraph()
        voiceBus?.outputVolume = max(0, min(1, gain))
        #endif
    }

    /// Chop/sample bus level. 0..1 linear, driven by
    /// SampleSettingsStore.chopGainLinear.
    public func setChopGain(_ gain: Float) {
        #if canImport(AVFoundation)
        buildContributionGraph()
        chopBus?.outputVolume = max(0, min(1, gain))
        #endif
    }

    /// Vocoder-preview bus level. 0..1 linear, driven by
    /// SampleSettingsStore.vocoderGainLinear. Silent until P5 attaches
    /// a source.
    public func setVocoderGain(_ gain: Float) {
        #if canImport(AVFoundation)
        buildContributionGraph()
        vocoderBus?.outputVolume = max(0, min(1, gain))
        #endif
    }

    /// Shared-bus ("Your Layer" fader) volume, linear. Allows up to
    /// 2.0 so the fader's advertised +6 dB headroom is real. Muting
    /// this leaves the stems untouched.
    public func setLayerGain(_ gain: Float) {
        #if canImport(AVFoundation)
        buildContributionGraph()
        sharedBus?.outputVolume = max(0, min(2, gain))
        #endif
    }

    // MARK: - Shared reverb

    /// Replace the shared-reverb settings and push them into the
    /// graph nodes.
    public func setReverbParams(_ params: ReverbParams) {
        reverbParams = params
        #if canImport(AVFoundation)
        buildContributionGraph()
        dryMixer?.outputVolume = max(0, min(1, params.dryGain))
        wetMixer?.outputVolume = max(0, min(1, params.wetGain))
        sharedReverb?.loadFactoryPreset(Self.presetForSeconds(params.seconds))
        sharedReverb?.wetDryMix = 100
        #endif
    }

    #if canImport(AVFoundation)
    /// Discrete AVAudioUnitReverb preset picker — the built-in unit
    /// has no continuous tail-length parameter, so we choose the
    /// preset whose tail is closest to the requested value. Was
    /// duplicated in PadSynth + SampleBus pre-D-013; this is now the
    /// only copy.
    public static func presetForSeconds(_ seconds: Double) -> AVAudioUnitReverbPreset {
        switch seconds {
        case ..<0.8:  return .smallRoom
        case ..<1.4:  return .mediumRoom
        case ..<2.2:  return .largeRoom
        case ..<2.8:  return .mediumHall
        case ..<3.6:  return .largeHall
        default:      return .cathedral
        }
    }
    #endif

    // MARK: - Session event handling

    private func startSessionEventObserver() {
        sessionEventTask?.cancel()
        sessionEventTask = Task { [weak self] in
            guard let self else { return }
            for await event in self.session.events {
                self.handle(event)
            }
        }
    }

    private func handle(_ event: AudioSessionController.Event) {
        switch event {
        case .interruptionBegan:
            pause()
        case .interruptionEndedShouldResume:
            play()
        case .interruptionEndedNoResume:
            // User dismissed via Control Center or similar — leave
            // paused, they can hit play manually.
            break
        case .routeChanged(let reason):
            if reason == .oldDeviceUnavailable {
                // Headphones unplugged / BT disconnected: pause per
                // iOS convention.
                pause()
            }
        }
    }
}
