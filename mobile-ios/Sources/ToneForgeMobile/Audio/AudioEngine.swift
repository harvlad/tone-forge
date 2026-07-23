// AudioEngine.swift
//
// Top-level audio engine wrapper. Owns the shared AVAudioEngine, the
// TransportClock, the contribution bus topology, the ONE shared reverb
// every contribution source rides (DECISIONS.md D-013), and the D-022
// master FX chain (EQ + compressor inline, reverb + delay send/return).
// All audio-graph plumbing (attach + connect) lives here; downstream
// nodes only know about their own AVAudioNode instances.
//
// Graph topology (D-013 + D-022 master FX):
//
//   PadSynth.voiceMixer ──┐
//   WavetableSynthNode ───┼→ voiceBus (0.9 = voiceGainLinear)
//   MicMonitor (P3) ──────┘        │
//   SampleVoicePool → SampleBus ──→ chopBus (0.55 = chopGainLinear)
//   VocoderMonitor (P5) ──────────→ vocoderBus (0.4 = vocoderGainLinear)
//                                     │
//   voiceBus + chopBus + vocoderBus → sharedBus (volume = layerFaderDb)
//   sharedBus → dryMixer(dryGain) ─────────────────────→ mainMixer
//   sharedBus → sharedReverb(wet 100) → wetMixer(wetGain) ─┘
//   sharedBus → fxSendMixer ────────────────────────────────┐
//   StemPlayer.timePitch → mainMixer                        │
//   StemPlayer.timePitch → fxSendMixer ─────────────────────┤
//                                                           ↓
//   fxSendMixer → masterReverb → masterDelay → fxReturnMixer → mainMixer
//
//   mainMixer → masterEQ(3-band) → masterComp(DynamicsProcessor) → outputNode
//
// Every explicit connect uses ``canonicalFormat`` (48 kHz stereo,
// D-017). The masterComp → output hop is the one place the engine may
// SRC (hardware boundary — unavoidable). Stems are the documented
// exception (D-014): they stay at source rate and get converted by
// the engine at their mainMixer edge.
//
// `buildContributionGraph()` is called from bootAudio BEFORE
// `engine.start()` — attaching + connecting on a running engine trips
// AVAudioEngine's graph validator when an intermediate node's
// upstream isn't populated yet. `buildMasterFXGraph()` is called after
// contribution graph, inserting the master EQ/comp chain between
// mainMixer and outputNode.
//
// Known v1 limitation: bounce path excludes master FX (records pre-FX).

import Foundation
#if canImport(AVFoundation)
import AVFoundation
import AudioToolbox
#endif
import ToneForgeEngine

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

    /// Current master FX settings (D-022). Mutate via ``setFXSettings``.
    @Published public private(set) var fxSettings = FXSettings.neutral

    /// Performance-FX (DJ FX) live controller. Nodes are bound in
    /// `buildMasterFXGraph`; push gestures via ``setPerfFXState``.
    public let performanceFX = PerformanceFXChain()

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

    // Master FX chain nodes (D-022). Built by `buildMasterFXGraph()`.
    private var fxSendMixer: AVAudioMixerNode?
    private var masterReverb: AVAudioUnitReverb?
    private var masterDelay: AVAudioUnitDelay?
    private var fxReturnMixer: AVAudioMixerNode?
    private var masterEQ: AVAudioUnitEQ?
    private var masterComp: AVAudioUnitEffect?

    // Performance-FX insert (PERFORM_PARITY spec 1). Built in
    // buildMasterFXGraph, inserted between mainMixer and masterEQ:
    // mainMixer → perfInput → perfFilter → perfFlanger → perfThrow →
    // perfGater → masterEQ. Live control lives in `performanceFX`.
    private var perfInputMixer: AVAudioMixerNode?
    private var perfFilter: AVAudioUnitEQ?
    private var perfFlanger: AVAudioUnitDelay?
    private var perfThrow: AVAudioUnitDelay?
    private var perfGaterMixer: AVAudioMixerNode?
    #endif
    /// 60 Hz modulation driver for beat-synced perf FX (gater square
    /// wave, flanger LFO, stopper brake). Runs only while an effect that
    /// varies over time is engaged. Declared outside the AVFoundation
    /// guard so the stop-path can nil it unconditionally.
    private var perfDriver: Timer?
    #if canImport(AVFoundation)

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

    /// Master FX send mixer — StemPlayer connects its timePitch here
    /// (in addition to mainMixer) so stems participate in the master
    /// reverb/delay send. Returns nil if master FX graph not built.
    public var fxSendMixerInput: AVAudioNode? {
        return fxSendMixer
    }

    /// Build the contribution bus topology on the idle engine.
    /// Idempotent — safe to call from accessors and bootAudio in any
    /// order. Also builds the master FX send/return chain (D-022).
    public func buildContributionGraph() {
        guard sharedBus == nil else { return }

        let voice = AVAudioMixerNode()
        let chop = AVAudioMixerNode()
        let vocoder = AVAudioMixerNode()
        let shared = AVAudioMixerNode()
        let dry = AVAudioMixerNode()
        let verb = AVAudioUnitReverb()
        let wet = AVAudioMixerNode()

        // Master FX send/return chain (D-022)
        let fxSend = AVAudioMixerNode()
        let mVerb = AVAudioUnitReverb()
        let mDelay = AVAudioUnitDelay()
        let fxReturn = AVAudioMixerNode()

        verb.wetDryMix = 100 // full wet on the wet branch; balance via wetMixer
        verb.loadFactoryPreset(Self.presetForSeconds(reverbParams.seconds))

        // Master reverb: 100% wet (dry path is parallel); size via preset
        mVerb.wetDryMix = 100
        mVerb.loadFactoryPreset(.largeHall)

        // Master delay: defaults (overwritten by setFXSettings)
        mDelay.delayTime = 0.25
        mDelay.feedback = 30
        mDelay.wetDryMix = 50

        for node in [voice, chop, vocoder, shared, dry, wet, fxSend, fxReturn] {
            engine.attach(node)
        }
        engine.attach(verb)
        engine.attach(mVerb)
        engine.attach(mDelay)

        let format = canonicalFormat
        // AVAudioMixerNode auto-picks a free input bus per connect, so
        // fanning three buses INTO sharedBus is safe with sequential
        // connects.
        engine.connect(voice, to: shared, format: format)
        engine.connect(chop, to: shared, format: format)
        engine.connect(vocoder, to: shared, format: format)
        // Fan sharedBus OUT to dry, reverb, AND fxSend branches in a
        // single connect() call. Two sequential connects would silently
        // drop the first edge — AVAudioEngine's connect() replaces any
        // existing connection on the source's output bus 0 (see the
        // trap documented at SampleBus.swift's original fan-out).
        engine.connect(
            shared,
            to: [
                AVAudioConnectionPoint(node: dry, bus: 0),
                AVAudioConnectionPoint(node: verb, bus: 0),
                AVAudioConnectionPoint(node: fxSend, bus: 0)
            ],
            fromBus: 0,
            format: format
        )
        engine.connect(verb, to: wet, format: format)
        engine.connect(dry, to: engine.mainMixerNode, format: format)
        engine.connect(wet, to: engine.mainMixerNode, format: format)

        // Master FX send chain: fxSend → mVerb → mDelay → fxReturn → mainMixer
        engine.connect(fxSend, to: mVerb, format: format)
        engine.connect(mVerb, to: mDelay, format: format)
        engine.connect(mDelay, to: fxReturn, format: format)
        engine.connect(fxReturn, to: engine.mainMixerNode, format: format)

        // Loudness-neutral defaults (overwritten by the persisted
        // values when wireSampleSettings' sinks fire on subscribe).
        voice.outputVolume = Float(SampleSettingsStore.defaultVoiceGain)
        chop.outputVolume = Float(SampleSettingsStore.defaultChopGain)
        vocoder.outputVolume = Float(SampleSettingsStore.defaultVocoderGain)
        shared.outputVolume = 1.0
        dry.outputVolume = reverbParams.dryGain
        wet.outputVolume = reverbParams.wetGain

        // Doubling guard: FX return silent until reverb or delay enabled.
        // setFXSettings() will turn this up when FX are active.
        fxReturn.outputVolume = 0

        self.voiceBus = voice
        self.chopBus = chop
        self.vocoderBus = vocoder
        self.sharedBus = shared
        self.dryMixer = dry
        self.sharedReverb = verb
        self.wetMixer = wet
        self.fxSendMixer = fxSend
        self.masterReverb = mVerb
        self.masterDelay = mDelay
        self.fxReturnMixer = fxReturn
    }

    /// Build the master FX insert chain on the idle engine: mainMixer →
    /// masterEQ → masterComp → outputNode. Called once from start(),
    /// after buildContributionGraph(). Idempotent.
    public func buildMasterFXGraph() {
        guard masterEQ == nil else { return }

        // 3-band parametric EQ
        let eq = AVAudioUnitEQ(numberOfBands: 3)
        eq.bypass = false

        // Band 0: low shelf
        eq.bands[0].filterType = .lowShelf
        eq.bands[0].frequency = 200
        eq.bands[0].gain = 0
        eq.bands[0].bypass = false

        // Band 1: mid peak
        eq.bands[1].filterType = .parametric
        eq.bands[1].frequency = 1000
        eq.bands[1].bandwidth = 1.0
        eq.bands[1].gain = 0
        eq.bands[1].bypass = false

        // Band 2: high shelf
        eq.bands[2].filterType = .highShelf
        eq.bands[2].frequency = 6000
        eq.bands[2].gain = 0
        eq.bands[2].bypass = false

        // Dynamics compressor via kAudioUnitSubType_DynamicsProcessor
        let compDesc = AudioComponentDescription(
            componentType: kAudioUnitType_Effect,
            componentSubType: kAudioUnitSubType_DynamicsProcessor,
            componentManufacturer: kAudioUnitManufacturer_Apple,
            componentFlags: 0,
            componentFlagsMask: 0
        )
        let comp = AVAudioUnitEffect(audioComponentDescription: compDesc)

        engine.attach(eq)
        engine.attach(comp)

        // Performance-FX insert nodes (PERFORM_PARITY spec 1). Filter =
        // one resonant band (bypassed until engaged); flanger + throw =
        // short delays at wetDryMix 0; input + gater = plain mixers.
        let perfIn = AVAudioMixerNode()
        let perfFilt = AVAudioUnitEQ(numberOfBands: 1)
        perfFilt.bands[0].bypass = true
        perfFilt.bands[0].filterType = .resonantLowPass
        let perfFlang = AVAudioUnitDelay()
        perfFlang.wetDryMix = 0
        let perfThr = AVAudioUnitDelay()
        perfThr.wetDryMix = 0
        let perfGate = AVAudioMixerNode()
        for node in [perfIn, perfGate] { engine.attach(node) }
        engine.attach(perfFilt)
        engine.attach(perfFlang)
        engine.attach(perfThr)

        // Disconnect mainMixer from outputNode (the implicit connection).
        // Then wire the full master path through the perf insert:
        // mainMixer → perfIn → filter → flanger → throw → gater → eq →
        // comp → outputNode.
        engine.disconnectNodeOutput(engine.mainMixerNode)

        let format = canonicalFormat
        engine.connect(engine.mainMixerNode, to: perfIn, format: format)
        engine.connect(perfIn, to: perfFilt, format: format)
        engine.connect(perfFilt, to: perfFlang, format: format)
        engine.connect(perfFlang, to: perfThr, format: format)
        engine.connect(perfThr, to: perfGate, format: format)
        engine.connect(perfGate, to: eq, format: format)
        engine.connect(eq, to: comp, format: format)
        engine.connect(comp, to: engine.outputNode, format: format)

        // Set initial compressor params (bypassed — amountDb 0 = infinite headroom)
        setCompressorParams(FXCompParams.neutral)

        self.masterEQ = eq
        self.masterComp = comp
        self.perfInputMixer = perfIn
        self.perfFilter = perfFilt
        self.perfFlanger = perfFlang
        self.perfThrow = perfThr
        self.perfGaterMixer = perfGate

        // Hand the nodes to the live controller and wire the stopper's
        // rate sink to the transport clock.
        performanceFX.rateSink = { [weak self] rate in self?.clock.setRate(rate) }
        performanceFX.bind(
            input: perfIn,
            filter: perfFilt,
            flanger: perfFlang,
            throwDelay: perfThr,
            gater: perfGate
        )
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

    /// Boot the engine. Activates the session, builds the contribution
    /// and master FX graphs, and starts the engine. Safe to call
    /// multiple times.
    public func start() {
        session.activate()
        session.preferLowLatency()

        #if canImport(AVFoundation)
        if !engine.isRunning {
            // Build graphs BEFORE engine.start() — attaching on a
            // running engine trips the graph validator.
            buildContributionGraph()
            buildMasterFXGraph()

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
        stopPerfDriver()
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

    // MARK: - Master FX (D-022)

    /// Replace the master FX settings and push them into the graph.
    /// Pushes params only — never mutates graph topology.
    public func setFXSettings(_ settings: FXSettings) {
        let s = settings.clamped()
        fxSettings = s
        #if canImport(AVFoundation)
        setEQParams(s.eq)
        setCompressorParams(s.comp)
        setMasterReverbParams(s.reverb)
        setMasterDelayParams(s.delay)

        // Doubling guard: return volume = 0 when both FX are off.
        let wetActive = !s.reverb.isNeutral || !s.delay.isNeutral
        fxReturnMixer?.outputVolume = wetActive ? Self.linearFromDb(Float(s.fxReturnDb)) : 0
        #endif
    }

    #if canImport(AVFoundation)
    /// Push EQ band params to the masterEQ unit.
    private func setEQParams(_ params: FXEQParams) {
        guard let eq = masterEQ else { return }
        let p = params.clamped()

        eq.bands[0].frequency = Float(p.lowFreq)
        eq.bands[0].gain = Float(p.lowGainDb)

        eq.bands[1].frequency = Float(p.midFreq)
        eq.bands[1].gain = Float(p.midGainDb)

        eq.bands[2].frequency = Float(p.highFreq)
        eq.bands[2].gain = Float(p.highGainDb)
    }

    /// Push compressor params to the masterComp AudioUnit. Uses
    /// AudioUnitSetParameter because AVAudioUnitEffect has no typed
    /// accessors for the dynamics processor.
    private func setCompressorParams(_ params: FXCompParams) {
        guard let comp = masterComp else { return }
        let p = params.clamped()

        // Get the underlying AudioUnit
        let au = comp.audioUnit

        // DynamicsProcessor params (see AudioUnitParameters.h):
        // kDynamicsProcessorParam_Threshold = 0
        // kDynamicsProcessorParam_HeadRoom = 1
        // kDynamicsProcessorParam_ExpansionRatio = 2
        // kDynamicsProcessorParam_AttackTime = 4
        // kDynamicsProcessorParam_ReleaseTime = 5
        // kDynamicsProcessorParam_OverallGain = 6
        AudioUnitSetParameter(au, 0, kAudioUnitScope_Global, 0, Float(p.thresholdDb), 0)
        AudioUnitSetParameter(au, 1, kAudioUnitScope_Global, 0, Float(p.amountDb), 0)
        AudioUnitSetParameter(au, 4, kAudioUnitScope_Global, 0, Float(p.attackMs / 1000), 0) // seconds
        AudioUnitSetParameter(au, 5, kAudioUnitScope_Global, 0, Float(p.releaseMs / 1000), 0) // seconds
        AudioUnitSetParameter(au, 6, kAudioUnitScope_Global, 0, Float(p.makeupDb), 0)
    }

    /// Push reverb params to the masterReverb unit.
    private func setMasterReverbParams(_ params: FXReverbParams) {
        guard let verb = masterReverb else { return }
        let p = params.clamped()

        verb.loadFactoryPreset(Self.presetForSeconds(p.sizeSeconds))
        // mix = 0..100; at mix=0 the verb passes dry signal, so we
        // actually want it fully wet when active (fxReturnMixer controls
        // final blend). But user expects the mix slider to control audibility:
        // map mix to the return level OR keep wetDryMix = 100 and use fxSendMixer gain.
        // For simplicity: wetDryMix = 100, let fxReturn control the amount.
        verb.wetDryMix = 100

        // Control audibility via fxSendMixer gain (proportional to mix)
        fxSendMixer?.outputVolume = Float(max(p.mix, fxSettings.delay.mix) / 100)
    }

    /// Push delay params to the masterDelay unit.
    private func setMasterDelayParams(_ params: FXDelayParams) {
        guard let delay = masterDelay else { return }
        let p = params.clamped()

        delay.delayTime = p.timeSec
        delay.feedback = Float(p.feedback)
        // Delay wetDryMix controls relative blend; since reverb is 100% wet,
        // we set delay to blend within the wet chain.
        delay.wetDryMix = Float(p.mix)

        // Update fxSendMixer gain (max of reverb and delay mix)
        fxSendMixer?.outputVolume = Float(max(fxSettings.reverb.mix, p.mix) / 100)
    }

    /// dB to linear conversion (0 dB = 1.0).
    private static func linearFromDb(_ db: Float) -> Float {
        pow(10, db / 20)
    }
    #endif

    // MARK: - Performance FX (PERFORM_PARITY spec 1)

    /// Push a new performance-FX gesture state (held pads + filter XY).
    /// Applies the static params immediately and starts/stops the 60 Hz
    /// modulation driver depending on whether a time-varying effect
    /// (gater/flanger/stopper) is now engaged.
    public func setPerfFXState(_ state: PerfFXState) {
        performanceFX.setState(state, now: clock.nowSongSeconds)
        // Settle non-driven params once (restores gains on release).
        performanceFX.applyModulation(now: clock.nowSongSeconds)
        if performanceFX.needsModulation {
            startPerfDriver()
        } else {
            stopPerfDriver()
        }
    }

    /// Replace the tunable FX shapes (persisted config).
    public func setPerfFXConfig(_ config: PerfFXConfig) {
        performanceFX.config = config
        performanceFX.applyStatic()
    }

    /// Update the beat grid the perf FX sync to (called on song load).
    public func setPerfFXBeatClock(_ beatClock: BeatClock) {
        performanceFX.beatClock = beatClock
    }

    private func startPerfDriver() {
        guard perfDriver == nil else { return }
        let timer = Timer(timeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in
            // Scheduled on the main run loop, so we are main-isolated.
            MainActor.assumeIsolated {
                guard let self else { return }
                self.performanceFX.applyModulation(now: self.clock.nowSongSeconds)
                if !self.performanceFX.needsModulation {
                    self.stopPerfDriver()
                }
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        perfDriver = timer
    }

    private func stopPerfDriver() {
        perfDriver?.invalidate()
        perfDriver = nil
    }

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
