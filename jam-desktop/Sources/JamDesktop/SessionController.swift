// SessionController.swift
//
// Glue between the Core intent models (TransportController,
// StemMixModel, ChordRibbonModel), the Audio layer
// (EngineController, MonitorController) and the session bridge
// (BridgeClient). One instance per app; a new session re-loads the
// stem subgraph and mixer matrix in place.
//
// Transport mirroring (hybrid local-first): this app is the audio
// owner and transport authority — the audio clock is ground truth.
// It emits transport_state (throttled while playing, immediate on
// discrete changes), session_data + load_stems on attach, and
// connect_state/latency_report/input_meter via MonitorController.
// Inbound peer transport frames apply last-writer-wins, exactly the
// web client's de-facto semantics.
//
// The Core models stay audio-free (headless tests); this class is
// the only place that knows all sides.

import Foundation
import Combine
import ToneForgeEngine
import JamDesktopCore
import JamDesktopAudio

/// Using ObservableObject (not @Observable) to avoid Swift 6
/// observation crashes with @MainActor isolation checks.
@MainActor
final class SessionController: ObservableObject {

    let engine = EngineController()
    let transport = TransportController()
    let mix = StemMixModel()
    let fx = FXPanelModel()
    let bridge = BridgeClient()
    @Published private(set) var ribbon: ChordRibbonModel?

    /// Monitor/tone surface over the shared ConnectCore engine.
    private(set) lazy var monitor = MonitorController(engine: engine.engine)

    /// Chop grid logic (Core) + the segment player it drives (Audio).
    private(set) lazy var launchpad: LaunchpadController = {
        let clock = engine.clock
        return LaunchpadController(nowProvider: { clock.nowSongSeconds })
    }()
    private(set) lazy var chopPlayer = ChopPlayer(avEngine: engine.engine.avEngine)
    /// Hardware transport, created once — hot-plug is handled inside.
    @Published private(set) var usbLaunchpad: USBLaunchpadTransport?
    /// Generic MIDI keyboard/pad controller — note route to synth or pads.
    @Published private(set) var midiKeyboard: MIDIKeyboardTransport?

    /// Contribution-event funnel (recording taps into it in P4; the
    /// sequencer requires it at init).
    let eventBus = ContributionEventBus()
    /// Step sequencer engine (ToneForgeEngine, ObservableObject —
    /// views observe it via @ObservedObject, so keep it unwrapped).
    private(set) lazy var sequencer = SequencerPlayer(eventBus: eventBus)
    /// Routes sequencer trigger callbacks into the chop voice pool.
    private(set) lazy var sequencerAdapter = SequencerAudioAdapter(sink: chopPlayer)
    /// Saved sequencer patterns (iOS-compatible wire format).
    let patternStore = SequencerPatternStore()
    /// Custom pad assignments (sequences, local samples, packs).
    let padAssignmentStore = PadAssignmentStore()
    /// Multiple patterns running on pads simultaneously.
    private(set) lazy var sequencePadManager = SequencePadManager(
        eventBus: eventBus,
        patternStore: patternStore
    )
    /// Chop boundary edits (per analysisId + presetKey); changes
    /// re-resolve the Launchpad grid and sequencer adapter.
    let chopEditStore = ChopEditStore()
    /// Layer recording: capture/replay state + JSON library (P4).
    let recording: RecordingModel
    /// Jam in Key pad surface state (P5) — pure logic; notes route
    /// into the synth below.
    let jam = JamInKeyModel()
    /// Learn mode session state (scoring, progress, predictions).
    let learn = LearnSessionModel()
    /// Curated pack browser state (P5); activation registers the
    /// resolved pack with the pack player.
    let packs = PacksModel()
    /// Wavetable synth on musicBus — jam pads + sequencer synthChord.
    private(set) lazy var synthNode = DesktopSynthNode(
        avEngine: engine.engine.avEngine)
    /// Pack pad playback through the chop voice pool.
    private(set) lazy var packPlayer = PackPadPlayer(sink: chopPlayer)
    /// Sounds replayed bus events through the chop voice pool.
    private let replayExecutor: ReplayExecutor
    /// Local samples store (mic captures, vocoder output, baked transforms).
    let padSampleStore = PadSampleStore()
    /// Transform render-on-arm host — swaps transformed buffers at trigger.
    let transformHost = PadTransformHost()
    /// Bake orchestration: render transforms → classify → save to store.
    private(set) lazy var transformBakeService = TransformBakeService(
        padSampleStore: padSampleStore)
    /// Vocoder preview ring + output node (captures voice → carrier blend).
    private(set) lazy var vocoderMonitor = VocoderMonitor(
        avEngine: engine.engine.avEngine)
    /// Vocoder capture session (mic → preview → offline render).
    private(set) lazy var vocoderCapture = VocoderCaptureSession(
        monitor: vocoderMonitor)
    /// Beat Capture (D-024): analysis-only mic take → drum pattern.
    private(set) lazy var beatCapture = BeatCaptureSession()
    /// Device-local drum-classifier correction log (training data).
    let beatTrainingStore = BeatTrainingStore()
    /// Guards one-time registration of the bundled `beatkit` pack.
    var beatKitRegistered = false

    /// Attached song's tempo, when a bundle is loaded — Beat Capture
    /// follows it before falling back to estimation.
    var currentSongTempoBpm: Double? { attachedBundle?.meta.tempoBpm }

    /// Analysis id of the session currently wired into the engine —
    /// PerformView re-attaches only when it changes.
    @Published private(set) var attachedAnalysisId: String?
    /// The attached bundle, kept for chop-edit re-resolution.
    private var attachedBundle: SongBundle?
    /// The attached session's local stem files, kept for bounce.
    private var attachedStemURLs: [String: URL] = [:]

    @Published var engineError: String?

    /// True once an inbound connect_state arrived — some OTHER
    /// connect-role client (e.g. a real Connect.app) shares this
    /// session id and also owns audio. Settings warns about it.
    @Published private(set) var foreignAudioOwnerSeen = false

    @Published var clickEnabled = false {
        didSet { engine.clickEnabled = clickEnabled }
    }

    private var engineStarted = false
    /// Suppresses outbound echoes while applying a peer's transport
    /// frame (onDiscreteChange fires for the applied intents).
    private var applyingPeerTransport = false

    init() {
        let recordingClock = engine.clock
        recording = RecordingModel(
            bus: eventBus,
            clockNow: { recordingClock.nowSongSeconds }
        )
        replayExecutor = ReplayExecutor(bus: eventBus)

        transport.clock = engine.clock
        transport.audio = engine
        mix.onMixChanged = { [weak self] in self?.applyMix() }
        fx.onFXChanged = { [weak self] settings in
            self?.engine.musicBus.apply(settings)
        }

        monitor.bind(to: bridge)
        engine.onGraphReattached = { [weak self] in
            self?.monitor.startInputMeter()
            self?.chopPlayer.reattach()
            self?.synthNode.reattach()
            if let musicBus = self?.engine.musicBus.input {
                self?.vocoderMonitor.reattach(outputNode: musicBus)
            }
            // Transforms don't need audio reattach (rendered buffers
            // stay in host memory); but clear the cache seam is here
            // if needed for device format changes.
        }

        // Sequencer trigger callbacks land on the chop voice pool;
        // packPad/synthChord steps route to their P5 players.
        sequencer.delegate = sequencerAdapter
        sequencerAdapter.packPlayer = packPlayer
        sequencerAdapter.synth = synthNode

        // Sequence pad manager: multiple patterns on pads, all routing
        // through the same audio adapter. Wire pulse updates to the
        // launchpad controller for LED and UI animation.
        sequencePadManager.delegate = sequencerAdapter
        sequencePadManager.onPulse = { [weak self] padIdx, pulse in
            self?.launchpad.updateSequencePulse(padIdx: padIdx, pulse: pulse)
        }

        // Jam pads: notes sound on the wavetable synth (started
        // lazily on first press so the sketch surface works without
        // a session).
        jam.onNoteOn = { [weak self] midi, velocity in
            guard let self else { return }
            self.ensureEngineStarted()
            self.synthNode.noteOn(midi: midi, velocity: velocity)
        }
        jam.onNoteOff = { [weak self] midi in
            self?.synthNode.noteOff(midi: midi)
        }

        // Activated packs become triggerable immediately.
        packs.onPackActivated = { [weak self] resolved in
            self?.packPlayer.register(resolved)
        }

        // Learn mode chord presses voice through the synth.
        learn.onPlayChord = { [weak self] symbol in
            self?.ensureEngineStarted()
            self?.synthNode.playChord(symbol: symbol)
        }

        // Chop edits re-resolve the grid + sequencer immediately.
        chopEditStore.onEditsChanged = { [weak self] analysisId in
            guard let self, analysisId == self.attachedAnalysisId else { return }
            self.applyChopEdits(analysisId: analysisId)
        }

        // Launchpad: hardware pads route through the controller; its
        // trigger callback converts the quantized fire-at song-time
        // into a wall-clock delay for the segment player (practice
        // rate stretches the distance to the next boundary).
        let clock = engine.clock
        let usb = USBLaunchpadTransport(
            midi: CoreMIDIInterface(),
            nowProvider: { (song: clock.nowSongSeconds, host: mach_absolute_time()) }
        )
        usbLaunchpad = usb
        launchpad.attach(transport: usb)
        launchpad.sequencePadManager = sequencePadManager
        launchpad.padAssignmentStore = padAssignmentStore
        launchpad.onTrigger = { [weak self] pad, assignment, fireAt in
            guard let self else { return }
            print("[Trigger] pad=\(pad) stem=\(assignment.stem) chopIdx=\(assignment.chop.idx)")
            let rate = max(0.1, self.transport.tempoPct)
            let delay = max(0, fireAt - clock.nowSongSeconds) / rate
            self.chopPlayer.trigger(assignment, afterSeconds: delay)
            // Publish for the session recorder. Timestamp = the
            // quantized fire-at moment (what actually SOUNDED), so
            // replays land on the grid, not the raw press time.
            if let coords = PadEventMapping.eventCoordinates(for: pad) {
                self.eventBus.publish(ContributionEvent(
                    source: .launchpad,
                    kind: .padDown(row: coords.row, col: coords.col),
                    timestamp: fireAt,
                    hostTime: mach_absolute_time()
                ))
            }
        }
        launchpad.onRelease = { [weak self] pad, assignment in
            guard let self else { return }
            self.chopPlayer.release(assignment)
            if let coords = PadEventMapping.eventCoordinates(for: pad) {
                self.eventBus.publish(ContributionEvent(
                    source: .launchpad,
                    kind: .padUp(row: coords.row, col: coords.col),
                    timestamp: clock.nowSongSeconds,
                    hostTime: mach_absolute_time()
                ))
            }
        }
        launchpad.onPackPadTrigger = { [weak self] packId, sourcePadIdx in
            self?.triggerPackPad(packId: packId, padIdx: sourcePadIdx)
        }

        // MIDI keyboard: generic USB/BT/network keyboards route to synth
        // or sample pads. Separate CoreMIDI client from Launchpad.
        let keyboard = MIDIKeyboardTransport(
            midi: CoreMIDIInterface(),
            nowProvider: { (song: clock.nowSongSeconds, host: mach_absolute_time()) }
        )
        midiKeyboard = keyboard
        keyboard.onContribution = { [weak self] event in
            guard let self else { return }
            self.ensureEngineStarted()
            switch event.kind {
            case .midiNote(let note, let velocity, let on):
                if on {
                    self.synthNode.noteOn(midi: note, velocity: Float(velocity) / 127.0)
                } else {
                    self.synthNode.noteOff(midi: note)
                }
            case .padDown(let row, let col):
                // Sample pad route: resolve grid cell to assignment
                if let pad = PadEventMapping.launchpadPad(row: row, col: col),
                   let assignment = self.launchpad.assignments[pad] {
                    self.chopPlayer.trigger(assignment, afterSeconds: 0, velocity: Float(event.velocity))
                }
            case .padUp(let row, let col):
                if let pad = PadEventMapping.launchpadPad(row: row, col: col),
                   let assignment = self.launchpad.assignments[pad] {
                    self.chopPlayer.release(assignment)
                }
            default:
                break
            }
            // Publish all keyboard events for recording
            self.eventBus.publish(event)
        }

        // Replayed takes resolve pads against the CURRENT grid and
        // sound through the same voice pool as live pads.
        replayExecutor.assignmentProvider = { [weak self] pad in
            self?.launchpad.assignments[pad]
        }
        replayExecutor.onTrigger = { [weak self] assignment, velocity in
            self?.chopPlayer.trigger(
                assignment, afterSeconds: 0, velocity: velocity)
        }
        replayExecutor.onRelease = { [weak self] assignment in
            self?.chopPlayer.release(assignment)
        }

        // Transport discontinuities become gap markers in the take
        // (recorder ignores them unless actively recording).
        transport.onPause = { [weak self] in
            self?.recording.recorder.noteTransportPause()
        }
        transport.onSeek = { [weak self] from, to in
            self?.recording.recorder.noteTransportSeek(from: from, to: to)
        }

        transport.onDiscreteChange = { [weak self] in
            self?.sendTransportState(discrete: true)
        }
        bridge.onTransportState = { [weak self] frame in
            self?.applyPeerTransport(frame)
        }
        bridge.onPeerConnectState = { [weak self] _ in
            self?.foreignAudioOwnerSeen = true
        }
    }

    // MARK: - Bridge lifecycle

    /// (Re)connect the session bridge. Idempotent; safe to call again
    /// when settings change the session id or backend.
    func startBridge(sessionId: String, backendBaseURL: URL) {
        foreignAudioOwnerSeen = false
        bridge.start(
            sessionId: sessionId,
            url: BridgeClient.bridgeURL(backendBaseURL: backendBaseURL)
        )
    }

    func stopBridge() {
        bridge.stop()
    }

    // MARK: - Display pump

    /// 30 Hz from PerformView: advances the transport off the audio
    /// clock and mirrors continuous position to peers (BridgeClient
    /// throttles to ~4 Hz on the wire).
    func tick() {
        transport.tick()
        if transport.isPlaying {
            sendTransportState(discrete: false)
        }
    }

    // MARK: - Session attach

    /// Wire a loaded session into the audio graph, reset the intent
    /// models and announce it to bridge peers. Idempotent per
    /// analysis id.
    func attach(_ session: LoadedSession) async {
        guard session.bundle.analysisId != attachedAnalysisId else { return }
        ensureEngineStarted()
        await engine.loadSession(session)
        mix.load(roles: engine.stemPlayer.loadedRoles)
        transport.durationSeconds = session.bundle.meta.durationSec
        transport.seek(to: 0)
        ribbon = ChordRibbonModel(timeline: session.bundle.timeline)
        attachedAnalysisId = session.bundle.analysisId
        applyMix()
        launchpad.configure(bundle: session.bundle)
        await chopPlayer.load(stemURLs: session.stemURLs)
        sequencer.stop()
        sequencer.songBPM = session.bundle.meta.tempoBpm ?? 120
        attachedBundle = session.bundle
        attachedStemURLs = session.stemURLs
        applyChopEdits(analysisId: session.bundle.analysisId)
        jam.configure(
            detectedKey: session.bundle.meta.detectedKey,
            analysisId: session.bundle.analysisId
        )
        learn.configure(bundle: session.bundle)
        announce(session)
    }

    // MARK: - Layer recording (P4)

    /// Arm capture for the attached song; the first pad press starts
    /// the take.
    func armRecording() {
        recording.arm(
            songBackendId: attachedAnalysisId,
            tempoBpm: attachedBundle?.meta.tempoBpm
        )
    }

    /// Replay a take from the top: rewind, roll the song and start
    /// the bus replayer (events fire as song time reaches them).
    func startReplay(_ session: SessionCapture) {
        transport.seek(to: 0)
        if !transport.isPlaying { transport.play() }
        recording.startReplay(session)
    }

    func stopReplay() {
        recording.stopReplay()
    }

    /// Render a take to WAV against the current grid. Returns the
    /// bounced file's URL.
    func bounce(_ session: SessionCapture) async throws -> URL {
        let result = try await SessionBounceService.bounce(
            session: session,
            assignments: launchpad.assignments,
            stemURLs: attachedStemURLs,
            outputDirectory: SessionBounceService.bouncesDir()
        )
        return result.url
    }

    /// Overlay saved chop-boundary edits onto the Launchpad grid and
    /// the sequencer's chop resolution (attach + every store change).
    private func applyChopEdits(analysisId: String) {
        guard let bundle = attachedBundle else { return }
        let edits = chopEditStore.edits(analysisId: analysisId)
        // Edits are keyed by preset — fetched stem/sliceMode grids
        // (presetKey nil) never get an overlay.
        if let presetKey = launchpad.presetKey {
            let presetEdits = edits[presetKey]
            launchpad.applyEdits(
                presetEdits?.hasEdits == true ? presetEdits : nil
            )
        }
        sequencerAdapter.configure(bundle: bundle, edits: edits)
    }

    // MARK: - Pack pads (P5)

    /// Fire a pad from the pack browser's trigger grid. Starts the
    /// engine lazily — packs play without a session attached.
    func triggerPackPad(packId: String, padIdx: Int, velocity: Float = 1) {
        print("[PackPad] packId=\(packId) padIdx=\(padIdx) registered=\(packPlayer.isRegistered(packId: packId))")
        ensureEngineStarted()
        // Ensure pack is registered (may not be if assigned before app restart)
        if !packPlayer.isRegistered(packId: packId) {
            packs.activate(packId: packId)
            print("[PackPad] activated pack, now registered=\(packPlayer.isRegistered(packId: packId))")
        }
        packPlayer.trigger(packId: packId, padIdx: padIdx, velocity: velocity)
    }

    // MARK: - Vocoder capture

    /// Build carrier program for vocoder capture mode.
    func buildVocoderProgram(for mode: VocoderMode) async -> VocoderProgram {
        let builder = VocoderProgramBuilder(
            bundle: attachedBundle,
            stemURLs: attachedStemURLs,
            currentPosition: transport.positionSeconds,
            currentChordPitchClasses: { [weak self] in
                guard let self, let ribbon = self.ribbon else { return [] }
                let pos = self.transport.positionSeconds
                guard let idx = ribbon.chordIndex(at: pos) else { return [] }
                let symbol = ribbon.chords[idx].symbol
                return Array(ChordVoicing.pitchClassSet(symbol: symbol))
            }
        )
        return await builder.buildProgram(for: mode)
    }

    /// Save vocoder take to pad sample store and return metadata.
    /// Note: grid assignment deferred — PadAssignment requires Chop+stem,
    /// local sample pad support is a follow-up.
    func saveVocoderTake(
        _ take: VocoderCaptureSession.Take, toGridPad padIndex: Int
    ) async throws -> PadSampleMetadata {
        let samples = take.processed
        guard !samples.isEmpty else {
            throw VocoderCaptureSession.CaptureError.noInputAvailable
        }

        // Classify
        let sampleRate = VocoderCaptureSession.canonicalSampleRate
        let (classification, confidence) = HeuristicClassifier().classify(
            samples: samples, sampleRate: sampleRate
        )

        // Build metadata
        let metadata = PadSampleMetadata(
            source: .vocoded,
            classification: classification,
            confidence: confidence,
            durationSec: Double(samples.count) / sampleRate,
            sampleRate: sampleRate,
            channels: 1,
            colorHint: 0x9B4DFF,  // purple for vocoded
            vocoderMode: take.mode.rawValue,
            sourceSongId: attachedAnalysisId
        )

        // Save to store
        return try await padSampleStore.save(
            samples: samples,
            sampleRate: sampleRate,
            metadata: metadata
        )
    }

    // MARK: - Tone card

    /// Apply a recommended chain: server resolves the id, broadcasts
    /// to peers and acks us with the resolved spec embedded — the ack
    /// path (MonitorController.onAck) programs the local DSP.
    func applyToneChain(chainId: String) {
        bridge.sendApplyChain(chainId: chainId)
    }

    // MARK: - Private

    func ensureEngineStarted() {
        guard !engineStarted else { return }
        do {
            try engine.start()
            chopPlayer.outputNode = engine.musicBus.input
            synthNode.attach(to: engine.musicBus.input)
            vocoderMonitor.attach(outputNode: engine.musicBus.input)
            engine.musicBus.apply(fx.settings)  // persisted FX
            engineStarted = true
            engineError = nil
            monitor.startInputMeter()
            monitor.publishSnapshot(to: bridge)
        } catch {
            engineError = error.localizedDescription
        }
    }

    private func sendTransportState(discrete: Bool) {
        guard !applyingPeerTransport else { return }
        bridge.sendTransportState(
            TransportStateFrame(
                playing: transport.isPlaying,
                positionS: transport.positionSeconds,
                tempoPct: transport.tempoPct,
                loopInS: transport.loop?.inSeconds,
                loopOutS: transport.loop?.outSeconds
            ),
            discrete: discrete
        )
    }

    /// Last-writer-wins application of a peer's transport intent.
    /// Position only re-seeks past a small threshold so 4 Hz mirror
    /// frames don't fight the local audio clock.
    private func applyPeerTransport(_ frame: TransportStateFrame) {
        applyingPeerTransport = true
        defer { applyingPeerTransport = false }

        if let pos = frame.positionS,
           abs(pos - transport.positionSeconds) > 0.5 {
            transport.seek(to: pos)
        }
        if let playing = frame.playing, playing != transport.isPlaying {
            playing ? transport.play() : transport.pause()
        }
        if let pct = frame.tempoPct, pct != transport.tempoPct {
            transport.setTempo(pct)
        }
        if let loopIn = frame.loopInS, let loopOut = frame.loopOutS {
            let region = LoopRegion(inSeconds: loopIn, outSeconds: loopOut)
            if region.isValid, region != transport.loop {
                transport.setLoop(region)
            }
        }
    }

    /// session_data + load_stems for a co-open browser (it renders
    /// chords/sections without re-fetching the bundle).
    private func announce(_ session: LoadedSession) {
        let bundle = session.bundle
        bridge.sendSessionData(
            SessionDataFrame(
                song: .init(id: bundle.analysisId, title: bundle.meta.title),
                bpm: bundle.meta.tempoBpm,
                chordProgression: bundle.timeline.chords.map {
                    .init(symbol: $0.symbol, startS: $0.start, endS: $0.end)
                },
                sectionMarkers: bundle.timeline.sections.map {
                    .init(
                        name: $0.label ?? "Section",
                        startS: $0.start, endS: $0.end
                    )
                }
            )
        )
        let stems = bundle.stems.compactMap { stem in
            stem.url.map {
                LoadStemsFrame.Stem(
                    id: stem.role, url: $0,
                    displayName: stem.role.capitalized
                )
            }
        }
        if !stems.isEmpty {
            bridge.sendLoadStems(LoadStemsFrame(stems: stems))
        }
    }

    /// Push the whole mix matrix to the audio nodes (cheap: a handful
    /// of outputVolume writes).
    private func applyMix() {
        let player = engine.stemPlayer
        for stem in mix.stems {
            player.setVolume(
                Float(mix.effectiveGain(for: stem.role)), forRole: stem.role
            )
        }
        player.setSongGain(Float(mix.songGain))
    }
}
