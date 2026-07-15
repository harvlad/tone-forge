// ToneForgeApp.swift
//
// App entry point + top-level `AppState`. The app target is a
// one-liner:
//
//   import ToneForgeMobile
//   @main struct AppEntry: App { var body: some Scene { ToneForgeScene() } }
//
// AppState owns:
//   - the current SongBundle (fetched from the backend)
//   - the AudioEngine + AudioSession (D-005 master clock lives inside)
//   - the StemPlayer (loaded when a bundle activates)
//   - the BundleStore (persists JSON + downloaded stems for offline)
//   - the current ChordAdvancer (rebuilt when bundle changes)
//   - the contribution engine (ContributionEventBus + ModeCoordinator;
//     AppMode itself lives on the coordinator)
//
// SwiftUI views observe the fields they need via @EnvironmentObject.

// @preconcurrency: the bounce glue hops AVAudioPCMBuffers into a
// detached render task; buffers are built on the main actor and
// never touched again after the hop (same pattern as the offline
// layer export).
@preconcurrency import AVFAudio
import Combine
import SwiftUI
import ToneForgeEngine

/// One virtual sample pack derived from the current song's
/// ``SongBundle.presets`` — a `(stem, sliceMode)` pair rendered into a
/// ``ResolvedSamplePack`` whose pads carry `StemSlice` windows into
/// the stem file rather than filenames. Presented in
/// `BrowsePacksSheet` under the pinned "Song DNA" section.
///
/// The `id` doubles as the presets-dict key (e.g. `"vocals:chord"`)
/// so a UI list can dedupe against the ambient dict without extra
/// bookkeeping.
public struct SongDnaPack: Identifiable, Sendable {
    public let id: String
    public let presetKey: String
    public let stem: String
    public let sliceMode: String
    public let displayName: String
    public let chopCount: Int
    public let pack: ResolvedSamplePack

    public init(
        presetKey: String,
        stem: String,
        sliceMode: String,
        displayName: String,
        chopCount: Int,
        pack: ResolvedSamplePack
    ) {
        self.id = presetKey
        self.presetKey = presetKey
        self.stem = stem
        self.sliceMode = sliceMode
        self.displayName = displayName
        self.chopCount = chopCount
        self.pack = pack
    }

    /// Stem role → sort priority. Vocals first, then rhythm section,
    /// then everything else alpha. Also used by the Song DNA row
    /// ordering in `BrowsePacksSheet` (so the same order appears in
    /// picker and any future analytics).
    public static let stemSortPriority: [String: Int] = [
        "vocals": 0, "drums": 1, "bass": 2, "other": 3, "guitar": 4,
    ]

    /// Build a `[SongDnaPack]` from a `SongBundle`. Deterministic
    /// output order (by stem priority, then stem alpha, then
    /// sliceMode alpha). Empty presets are skipped. Extracted here
    /// so `SongDnaPackTests` can exercise the ordering + skip rules
    /// without touching AppState.
    public static func synthesize(from bundle: SongBundle) -> [SongDnaPack] {
        let sortedKeys = bundle.presets.keys.sorted { lhs, rhs in
            let lp = bundle.presets[lhs]!
            let rp = bundle.presets[rhs]!
            let ls = stemSortPriority[lp.stem.lowercased()] ?? 99
            let rs = stemSortPriority[rp.stem.lowercased()] ?? 99
            if ls != rs { return ls < rs }
            if lp.stem != rp.stem { return lp.stem < rp.stem }
            return lp.sliceMode < rp.sliceMode
        }
        return sortedKeys.compactMap { key in
            guard let preset = bundle.presets[key], !preset.chops.isEmpty else {
                return nil
            }
            let packId = "song-derived:\(bundle.analysisId):\(preset.stem)-\(preset.sliceMode)"
            let display = "\(preset.stem.capitalized) — \(preset.sliceMode)"
            let resolved = SampleBank.songDerived(
                preset: preset,
                packId: packId,
                name: display
            )
            return SongDnaPack(
                presetKey: key,
                stem: preset.stem,
                sliceMode: preset.sliceMode,
                displayName: display,
                chopCount: preset.chops.count,
                pack: resolved
            )
        }
    }
}

/// Root scene. Owns the top-level app state (loaded bundle, playback
/// status). Views underneath are lightweight and stateless —
/// everything animates off state changes here.
public struct ToneForgeScene: Scene {
    @StateObject private var appState = AppState()
    @Environment(\.scenePhase) private var scenePhase

    public init() {
        // UI-test hook: clear the persisted ownership attestation
        // before any AttestationStore instance is created, so the
        // attestation sheet reliably re-appears under test.
        if UITestSupport.resetAttestationRequested {
            AttestationStore.resetPersisted()
        }
        // Same for the persisted account (profile + Keychain token).
        if UITestSupport.resetAccountRequested {
            AccountStore.resetPersisted()
        }
    }

    public var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(appState)
                .preferredColorScheme(.dark)
                .task {
                    appState.bootAudio()
                    // Validate the cached session against the backend
                    // (definitive 401 signs out; offline keeps state).
                    await appState.accountStore.restore(
                        baseURL: appState.backendBaseURL
                    )
                }
                .onChange(of: scenePhase) { _, phase in
                    appState.handleScenePhase(phase)
                }
        }
    }
}

/// Top-level observable app state. Kept small on purpose — anything
/// larger belongs on the AudioEngine or in a dedicated view model.
@MainActor
public final class AppState: ObservableObject {

    // MARK: - Bundle

    @Published public var currentBundle: SongBundle?
    @Published public var loadingError: String?
    /// The analysis id whose bundle is loading right now (JSON fetch +
    /// stem download), or nil. Set the instant a Library row is tapped
    /// so the UI shows a spinner immediately — the cold-start stem
    /// download emits no incremental bytes, so `downloadFraction` stays
    /// nil until the first stem completes and this is the only signal
    /// covering the gap.
    @Published public private(set) var loadingBundleId: String?
    @Published public var backendBaseURL: URL = AppState.loadBackendBaseURL() {
        didSet { AppState.saveBackendBaseURL(backendBaseURL) }
    }

    private static let backendURLDefaultsKey = "toneforge.backendBaseURL"

    private static func loadBackendBaseURL() -> URL {
        if let stored = UserDefaults.standard.string(forKey: backendURLDefaultsKey),
           let url = URL(string: stored) {
            // Purge stale dev/LAN overrides (a MacBook's Bonjour or
            // private-network address that's unreachable off the dev
            // LAN). Keeps public overrides. Ensures a device never
            // gets stranded on a dead dev host across rebuilds.
            if isDevHost(url) {
                UserDefaults.standard.removeObject(forKey: backendURLDefaultsKey)
                return AppConfig.defaultBackendURL
            }
            // ATS blocks plain-http URLSession requests on device, so a
            // stored http:// override strands every Library/pack fetch
            // with "requires the use of a secure connection". Purge it.
            if url.scheme?.lowercased() != "https" {
                UserDefaults.standard.removeObject(forKey: backendURLDefaultsKey)
                return AppConfig.defaultBackendURL
            }
            return url
        }
        // Build-configuration default: dev host Bonjour address in
        // DEBUG (overridable via the DEBUG-only backend fields),
        // production host in release.
        return AppConfig.defaultBackendURL
    }

    /// True when `url`'s host is a local/dev address: Bonjour `.local`,
    /// `localhost`, a bare hostname (no dots), or an RFC-1918 private
    /// IPv4 range. Public hosts (the VPS, a real domain) return false.
    private static func isDevHost(_ url: URL) -> Bool {
        guard let host = url.host?.lowercased() else { return false }
        if host == "localhost" || host.hasSuffix(".local") { return true }
        // Bare hostname with no dot (e.g. a machine name) — not routable
        // off the local network.
        if !host.contains(".") { return true }
        // RFC-1918 private IPv4 ranges.
        let octets = host.split(separator: ".").compactMap { Int($0) }
        if octets.count == 4 {
            if octets[0] == 10 { return true }
            if octets[0] == 192 && octets[1] == 168 { return true }
            if octets[0] == 172 && (16...31).contains(octets[1]) { return true }
            if octets[0] == 127 { return true }
        }
        return false
    }

    private static func saveBackendBaseURL(_ url: URL) {
        UserDefaults.standard.set(url.absoluteString, forKey: backendURLDefaultsKey)
    }

    // MARK: - Playback

    @Published public private(set) var isPlaying: Bool = false
    @Published public private(set) var songSeconds: Double = 0
    /// Master output gain, applied to the engine's main mixer. Governs
    /// the entire song mix (stems + pad synth). 0..1 linear.
    @Published public private(set) var masterGain: Double = 0.85
    /// A/B practice loop (redesign Phase 5). When set, the 30 Hz
    /// tick wraps the transport back to the region start as the
    /// playhead crosses its end. Cleared on song load and eject.
    @Published public private(set) var loopRegion: LoopRegion?
    /// Practice playback rate (D-022). 1.0 everywhere except the
    /// Learn tab, where the persisted `learnSettings.practiceRateX`
    /// applies. Real speed: TransportClock rate + stem timePitch.
    @Published public private(set) var playbackRate: Double = 1.0

    // MARK: - Tab shell (D-022)

    /// The selected top-level tab. Restored in `bootAudio` (after
    /// `modeCoordinator.start()`) so the didSet apply-path runs
    /// against a live coordinator; snapshot tests never boot, so
    /// constructing an AppState stays side-effect free.
    @Published public var selectedTab: AppTab = .contribute {
        didSet {
            if selectedTab.isPerformance { lastPerformanceTab = selectedTab }
            sampleSettings.appTabRaw = selectedTab.rawValue
            applySelectedTab()
        }
    }

    /// Last performance tab the user was on — the deep-link target
    /// when the Library activates a song while Mixer or Library is
    /// selected.
    public private(set) var lastPerformanceTab: AppTab = .contribute

    /// Jump to a performance tab (Library song activation). Keeps the
    /// current tab when it already hosts a playing surface.
    public func showPerformanceTab() {
        if !selectedTab.isPerformance {
            selectedTab = lastPerformanceTab
        }
    }

    /// Library song activation: open the tapped song in Learn. Does NOT
    /// auto-play — the user starts the transport when ready. The tab
    /// switch runs in an animation-disabled transaction so the Learn
    /// surface doesn't scale/zoom in on entry.
    public func openSong() {
        var tx = Transaction()
        tx.disablesAnimations = true
        withTransaction(tx) {
            selectedTab = .learn
        }
    }

    /// Tab → engine-mode policy (see TabModePolicy). Leaving Learn
    /// mid-practice ends the pass (clears the A/B loop, persists the
    /// streak) so the loop doesn't keep wrapping under another tab.
    /// Practice speed (D-022) is Learn-scoped: entering Learn applies
    /// the persisted rate, leaving restores 1.0 (quantize grids,
    /// chord advancer and recordings all assume real-time elsewhere).
    private func applySelectedTab() {
        if selectedTab != .learn, learnController.phase == .practicing {
            learnController.stopPractice()
        }
        if selectedTab == .learn {
            applyPlaybackRate(clampedPracticeRate(learnSettings.practiceRateX))
        } else if playbackRate != 1.0 {
            applyPlaybackRate(1.0)
        }
        if let mode = TabModePolicy.mode(
            for: selectedTab,
            lastContributeModeRaw: sampleSettings.lastContributeModeRaw
        ) {
            modeCoordinator.setMode(mode)
        }
    }

    // MARK: - Chord runtime

    @Published public private(set) var currentChord: ChordEvent?
    @Published public private(set) var nextChord: ChordEvent?
    @Published public private(set) var chordPhase: Double = 0

    // MARK: - Bundle download progress (Library tab)

    @Published public private(set) var downloadProgress: [String: BundleStore.StemProgress] = [:]
    @Published public private(set) var isDownloading: Bool = false
    /// Number of stems the current download expects to finish. Set from
    /// `bundle.stems.count` before the download stream runs so the
    /// fraction knows its denominator even before any stem reports.
    @Published public private(set) var downloadExpectedStems: Int = 0

    /// Aggregate stem-download progress (0..1) for the song currently
    /// loading, or nil when nothing is downloading. Counts completed
    /// stems against the total expected: the download API yields one
    /// terminal `isComplete` event per stem (no incremental bytes), so a
    /// byte-weighted average would read 100% from the very first reported
    /// stem — and a stem stalled mid-transfer would never report, hiding
    /// it entirely. Counting against `downloadExpectedStems` keeps a
    /// stalled stem visible as < 100% instead of a fake full bar.
    public var downloadFraction: Double? {
        guard isDownloading else { return nil }
        let expected = max(downloadExpectedStems, downloadProgress.count)
        guard expected > 0 else { return nil }
        let complete = downloadProgress.values.filter { $0.isComplete }.count
        return min(1, Double(complete) / Double(expected))
    }

    // MARK: - Owned subsystems

    public let audioEngine = AudioEngine()
    public let bundleStore = BundleStore()
    public lazy var stemPlayer: StemPlayer = StemPlayer(engine: audioEngine)
    public lazy var padSynth: PadSynth = PadSynth(engine: audioEngine)

    // MARK: - Contribution engine
    //
    // Every input surface (on-screen grid now, Launchpad Pro MK3 in
    // P2) publishes ContributionEvents on the bus; the ModeCoordinator
    // is the ONLY subscriber that reaches the audio executors. The
    // WavetableSynthNode is the hybrid-mode note instrument.
    public let contributionBus = ContributionEventBus()
    public lazy var wavetableSynthNode: WavetableSynthNode =
        WavetableSynthNode(engine: audioEngine)
    public lazy var modeCoordinator: ModeCoordinator = ModeCoordinator(app: self)

    /// Launchpad Pro MK3 hardware transport (P2). Created in
    /// `bootAudio` (see `wireLaunchpad`) so headless AppStates —
    /// snapshot tests construct one without booting — never open a
    /// CoreMIDI client.
    @Published public private(set) var usbLaunchpad: USBLaunchpadTransport?
    private var launchpadCancellables: Set<AnyCancellable> = []

    /// Generic MIDI note controller transport (keyboards / pad boxes).
    /// Created alongside the Launchpad in `bootAudio`; notes publish on
    /// the contribution bus and drive the wavetable synth via ModeRouter.
    @Published public private(set) var midiKeyboard: MIDIKeyboardTransport?

    /// Mirror of `usbLaunchpad.underpowerSuspected` (P7 banner). The
    /// transport is optional and late-created, so views observe this
    /// AppState field instead of reaching through the optional.
    @Published public private(set) var underpowerBannerVisible = false

    // MARK: - Sample-layer subsystems
    //
    // `sampleBus` sits between the voice pool and the layer bus, so the
    // dry+wet reverb topology matches PadSynth. `sampleVoicePool` fans
    // 32 slots into the bus's voice input. `sampleScheduler` translates
    // pad taps → AVAudioTime, honouring quantize + section-gate rules.
    // `sampleSettingsStore` persists user preferences; its published
    // fields flow into the scheduler via Combine sinks wired at boot.
    public lazy var sampleBus: SampleBus = SampleBus(engine: audioEngine)
    public lazy var sampleVoicePool: SampleVoicePool =
        SampleVoicePool(engine: audioEngine, bus: sampleBus)
    public lazy var sampleScheduler: SampleScheduler =
        SampleScheduler(engine: audioEngine, bus: sampleBus, pool: sampleVoicePool)
    public let sampleBank: SampleBank? = try? SampleBank.defaultBank()
    public let sampleSettings = SampleSettingsStore()

    // MARK: - Local samples (P3 mic pipeline)
    //
    // Mic captures land in `padSampleStore` (Documents/samples, never
    // uploaded), `padAssignmentStore` remembers which grid pad plays
    // which sample per mode, and `micRecorder` owns the capture flow
    // on its own private engine. ModeCoordinator glues the three to
    // the scheduler's local-buffer path.
    public let padSampleStore = PadSampleStore()
    public let padAssignmentStore = PadAssignmentStore()
    /// Saved sequencer patterns, assignable to pads.
    public let sequencerPatternStore = SequencerPatternStore()
    /// Beat Capture (D-024) correction log — device-local training data.
    public let beatTrainingStore = BeatTrainingStore()
    /// Pattern id the sequencer should seed itself from on next open
    /// (set by Beat Capture "Open in Sequencer"; cleared once consumed).
    @Published public var pendingSequencerPatternId: UUID?
    public lazy var micRecorder: MicRecorder =
        MicRecorder(session: audioEngine.session)

    // MARK: - Vocoder capture (P5)
    //
    // Capture-only: the monitor's preview node attaches once at boot
    // (ring inactive = silence) and `vocoderCapture` records the mic
    // against a coordinator-built carrier program. Results are saved
    // via ModeCoordinator.saveVocoderTake (source .vocoded, never
    // uploaded) — there is no persistent live-vocoder path.
    public lazy var vocoderMonitor: VocoderMonitor =
        VocoderMonitor(engine: audioEngine)
    public lazy var vocoderCapture: VocoderCaptureSession =
        VocoderCaptureSession(session: audioEngine.session, monitor: vocoderMonitor)

    // MARK: - Live Beat (real-time percussion)
    //
    // Mic becomes live drum controller. Onset detection triggers
    // immediate sample playback via SampleVoicePool. User calibrates
    // profiles mapping their physical sounds to drum roles.
    public let liveBeatProfileStore = LiveBeatProfileStore()
    public lazy var liveBeatController: LiveBeatController =
        LiveBeatController(session: audioEngine.session)
    public lazy var liveBeatCalibrator: LiveBeatCalibrator =
        LiveBeatCalibrator(session: audioEngine.session)

    // MARK: - Sketch mode

    /// Persisted sketch settings (tempo, time sig, metronome, sketch
    /// quantize, last pack). D-016: "sketch" is no longer a tab —
    /// these drive the synthetic tempo grid whenever no bundle is
    /// loaded (see ModeCoordinator.applyGridContext).
    public let sketchSettings = SketchSettingsStore()

    /// Song-less click track (Sketch plan Phase 2). Runs only when no
    /// bundle is loaded, the toggle is on, and the transport plays —
    /// `syncMetronome` owns that decision. Jam in Key (Phase 7) is
    /// the exception: it runs the metronome from `jamSettings` even
    /// with a song loaded.
    public lazy var metronome: Metronome = Metronome(engine: audioEngine)

    // MARK: - Jam in Key (redesign Phase 7)

    /// Persisted Jam in Key settings (key override, scale variant,
    /// preset, octave, strum, quantize, jam metronome).
    public let jamSettings = JamSettingsStore()

    /// UI state + degree-pad actions for the Jam surface.
    public lazy var jamController: JamInKeyController =
        JamInKeyController(app: self)

    // MARK: - Learn (redesign Phase 8)

    /// Persisted Learn settings (practice playback rate, D-022).
    public let learnSettings = LearnSettingsStore()

    // MARK: - Master FX (D-022 Phase 6)

    /// Persisted master FX settings (EQ, comp, reverb, delay).
    public let fxSettingsStore = FXSettingsStore()

    /// Per-song learn progress on disk
    /// (`Documents/learnProgress/{analysisId}.json`).
    public let learnProgressStore: LearnProgressStore

    /// Practice-session lifecycle + progress for the Learn surface.
    public lazy var learnController: LearnSessionController =
        LearnSessionController(app: self)

    // MARK: - Chord Pads (redesign Phase 12)

    /// Grid content + trigger seam for the Chord Pads surface
    /// (voices directly on the PadSynth, bus bypass per D-019).
    public lazy var chordPadController: ChordPadController =
        ChordPadController(app: self)

    /// The pack currently loaded into the scheduler. Nil until either
    /// the bundled StarterPack loads or a song-derived pack activates.
    @Published public private(set) var activeSamplePack: ResolvedSamplePack?

    /// Mirror of `SampleVoicePool.ringingPadKeys` so views that only
    /// observe AppState re-render when loops start/stop. Drives the
    /// pad-grid "ringing" indicator + the stop-all button.
    @Published public private(set) var ringingPadKeys: Set<SamplePadKey> = []

    /// Song DNA — virtual packs synthesised from `SongBundle.presets`
    /// once the stems for the current bundle have finished downloading.
    /// Cleared when a new bundle activates and repopulated when its
    /// downloads land. Empty when no bundle is loaded.
    @Published public private(set) var songDnaPacks: [SongDnaPack] = []

    // MARK: - Curated packs (Browse → Curated)

    /// Curated pack catalog from `GET /api/sample-packs`. Empty until
    /// `refreshCuratedCatalog()` runs. Repopulated on user pull-to-
    /// refresh or when the Browse sheet opens.
    @Published public private(set) var curatedCatalog: [SamplePackCatalogEntry] = []
    /// Per-pack download progress. Present while a pack downloads;
    /// stays after completion so the row shows "Downloaded".
    @Published public private(set) var curatedDownloads: [String: PackDownloadProgress] = [:]
    /// Pack IDs known to be fully on disk (manifest + all pad files).
    /// Refreshed after every catalog fetch + every download completion.
    @Published public private(set) var cachedPackIds: Set<String> = []
    /// Any active or last error surface from curated pack ops. Cleared
    /// by the UI on retry.
    @Published public var curatedError: String?

    // MARK: - Layer recording (Phase 4 — frozen read-only, D-015)

    /// Backing store for saved layers on disk. New recordings go
    /// through `sessionRecorder`; old layers stay listable,
    /// replayable, and exportable.
    public let layerStore = LayerStore()
    /// Replays saved layers alongside live play. Started/stopped by
    /// transport lifecycle.
    public lazy var layerPlayer: LayerPlayer = makeLayerPlayer()
    /// Saved layers for the currently loaded song. Refreshed on
    /// bundle activate + on every save/delete.
    @Published public private(set) var savedLayers: [LayerTimeline] = []
    /// Song-less sketch layers (sentinel `__sketch__`). Loaded at
    /// boot, refreshed on every sketch save/delete.
    @Published public private(set) var savedSketchLayers: [LayerTimeline] = []
    /// Layers the user has toggled on for playback in the current
    /// session. Persisted as a set to survive pack swaps.
    @Published public private(set) var activePlaybackLayerIds: Set<String> = []
    /// True while the transport is running *because* a layer toggle
    /// kicked it into playback. Cleared whenever the user manually
    /// hits play/pause (they've taken over transport ownership from
    /// that point on) and whenever the last active layer is toggled
    /// off. Used to make the per-layer play/pause button in Profile
    /// symmetric: toggling on starts the song, toggling the last
    /// one off pauses it — so the user doesn't have to hop to the
    /// Play tab just to silence the song they didn't ask to start.
    private var transportStartedByLayer: Bool = false
    /// Surface layer-related errors (save failures, replay init).
    @Published public var layerError: String?

    /// Layer ids currently being uploaded to the backend. UI shows a
    /// spinner + disables the row's Upload menu item while present.
    @Published public private(set) var uploadingLayerIds: Set<String> = []
    /// Layer ids that have been successfully uploaded in this session
    /// (used to swap the Upload menu label to "Uploaded ✓"). Reset on
    /// each bundle activate.
    @Published public private(set) var uploadedLayerIds: Set<String> = []

    /// Layer ids currently being offline-rendered to m4a. UI shows a
    /// spinner + disables the row's Export menu item while present.
    @Published public private(set) var exportingLayerIds: Set<String> = []

    private let layerClient = LayerClient()

    // MARK: - Session capture (P6, D-015)
    //
    // The Record pill arms `sessionRecorder` — a bus subscriber that
    // captures ContributionEvents (no audio) into
    // Documents/sessions/<sessionId>.json via autosave. Replay is
    // `sessionPlayer` re-firing those events with `isReplay: true`;
    // the ModeCoordinator routes them through triggerRaw under the
    // session's padMapping overlay. The legacy Layer* stack above is
    // frozen read-only: old layers stay listable/replayable/
    // exportable, but nothing records into it anymore.

    /// Backing store for saved sessions on disk. Root injectable
    /// through `init` so integration tests stay hermetic (never
    /// touching the real Documents/sessions).
    public let sessionStore: SessionStore
    /// Records the live contribution-event stream. Armed by the
    /// Record pill; autosaves every 10 s + on stop.
    public lazy var sessionRecorder: SessionCaptureRecorder = makeSessionRecorder()
    /// Replays saved sessions through the contribution bus.
    public lazy var sessionPlayer: SessionPlayer = SessionPlayer(
        bus: contributionBus,
        clockNow: { [weak self] in self?.audioEngine.clock.nowSongSeconds ?? 0 }
    )
    /// Sessions on disk, newest first. Refreshed at boot + on every
    /// save/delete.
    @Published public private(set) var savedSessions: [SessionCapture] = []
    /// The session currently loaded for replay (nil = none). Set
    /// BEFORE the transport starts so `play()` can start the player.
    @Published public private(set) var replayingSessionId: UUID?
    /// Session ids currently being offline-bounced. UI shows a
    /// spinner + disables the row's Bounce action while present.
    @Published public private(set) var bouncingSessionIds: Set<UUID> = []

    // MARK: - Layer A/B slots (D-022 Phase 7)

    /// Per-slot session players. Both pump the bus with `isReplay = true`
    /// when their slots have takes assigned. Created lazily on first use.
    public lazy var slotPlayers: [RecordingSlot: SessionPlayer] = [
        .a: SessionPlayer(
            bus: contributionBus,
            clockNow: { [weak self] in self?.audioEngine.clock.nowSongSeconds ?? 0 }
        ),
        .b: SessionPlayer(
            bus: contributionBus,
            clockNow: { [weak self] in self?.audioEngine.clock.nowSongSeconds ?? 0 }
        )
    ]
    /// Current layer slots for the active song (or sketch). Published so
    /// ContributeSurface can show which slot is active and which have takes.
    @Published public private(set) var layerSlots: LayerSlots = LayerSlots()
    /// Per-song slot state (keyed by analysisId; sketch uses sentinel).
    private var slotStates: [String: LayerSlots] = [:]
    /// Sentinel analysisId for sketch (no song) slot state.
    public static let sketchSlotId = "__sketch__"

    /// Stem role → local file URL for the currently activated bundle.
    /// Song-derived packs need this to slice their stems on activate.
    /// Rebuilt every time `activate(bundle:)` runs.
    private var currentStemLocalURLs: [String: URL] = [:]

    /// Normalized waveform peaks for the scrubber (0..1, ~600 bins),
    /// extracted from the downloaded stems off-main. Nil until the
    /// current song's extraction lands (the scrubber falls back to a
    /// plain progress bar) and cleared on eject.
    @Published public private(set) var waveformPeaks: [Float]?

    /// Best downloaded stem for the vocoder's M3 (stem) carrier:
    /// prefers voice-like roles (they loop into the most musical
    /// carriers), else the first stem alphabetically. Nil without a
    /// song or before its downloads land — the carrier builder
    /// degrades to the chord/drone carrier then.
    var vocoderStemURL: URL? {
        guard !currentStemLocalURLs.isEmpty else { return nil }
        for role in ["vocals", "vocal", "lead", "melody", "other"] {
            if let url = currentStemLocalURLs[role] { return url }
        }
        return currentStemLocalURLs.min { $0.key < $1.key }?.value
    }

    /// Optional sign-in state (Sign in with Apple). Owns the session
    /// token + profile; child views observe it directly.
    public let accountStore: AccountStore

    private let loader = BundleLoader()
    private let chopsClient = ChopsClient()
    private let packClient = PackClient()
    private var advancer = ChordAdvancer(chords: [])
    private var tickTimer: Timer?
    private var settingsCancellables: Set<AnyCancellable> = []

    /// - Parameters:
    ///   - sessionStoreRoot: base directory override for the session
    ///     store (tests); nil = the app's Documents directory.
    ///   - learnProgressRoot: same, for the learn-progress store.
    public init(
        sessionStoreRoot: URL? = nil,
        learnProgressRoot: URL? = nil
    ) {
        sessionStore = SessionStore(root: sessionStoreRoot)
        learnProgressStore = LearnProgressStore(root: learnProgressRoot)
        // Device identity + account: every Engine client stamps its
        // requests from AuthContext, so seed it before any fetch.
        AuthContext.shared.deviceId = DeviceIdentity.id()
        accountStore = AccountStore(client: UITestSupport.makeAuthClient())
        // Learn mode scores a practice pass every time the A/B loop
        // wraps (redesign Phase 8). Wired here so the hook exists
        // before the first tick; inert in every other mode.
        onLoopWrap = { [weak self] in
            guard let self,
                  self.modeCoordinator.appMode == .learnSong
            else { return }
            self.learnController.passCompleted()
        }
    }

    // MARK: - Bootstrap

    /// Configure the audio session + start the engine. Idempotent.
    /// Called from the scene's `.task` modifier.
    public func bootAudio() {
        // Build the graph BEFORE starting the engine. Attaching new
        // nodes + calling `connect()` on a running AVAudioEngine
        // trips its `srcNodeMixerConns.empty() && !isSrcNodeConnectedToIONode`
        // graph validator whenever an intermediate node's upstream
        // isn't yet populated — which is exactly the case for
        // `SampleBus.voiceMixer` at attach time (the SampleVoicePool
        // hasn't fanned its 32 slots in yet). PadSynth doesn't hit
        // this because it wires `AVAudioSourceNode → voiceMixer`
        // before its own fan-out. Doing all connects on an idle
        // engine sidesteps the check entirely.
        //
        // Contribution graph (voice/chop/vocoder → shared → dry+wet)
        // first, so the bus inputs exist for the attach calls below.
        audioEngine.buildContributionGraph()
        padSynth.attach()
        // Hybrid-mode note instrument — a dry source on the voice bus,
        // same branch as PadSynth (D-013).
        wavetableSynthNode.attach()
        // Vocoder capture preview — a source on the vocoder bus. Ring
        // stays inactive (silence) outside captures, so the running
        // graph is never rewired mid-jam (P5).
        vocoderMonitor.attach()

        // Sample layer: attach the bus first (so voiceMixer exists),
        // then the pool fans its 32 slots into the bus, then wire the
        // scheduler to observe user settings.
        #if canImport(AVFoundation)
        sampleBus.attach(destination: audioEngine.chopBusInput)
        #endif
        sampleVoicePool.attach()
        // Click track — straight to the main mixer (monitoring aid; no
        // shared reverb, no layer fader — D-013 rationale in Metronome).
        metronome.attach()

        audioEngine.start()

        wireSampleSettings()
        wireSampleEffects()
        loadInitialSamplePack()
        // Sketch layers persist across launches under the sentinel id.
        savedSketchLayers = layerStore.list(
            analysisId: LayerStore.sketchAnalysisId
        )
        // Session capture (P6, D-015): touching the lazy recorder
        // subscribes it to the bus (idle until armed) and wires its
        // autosave into the store; the shelf lists saved sessions.
        _ = sessionRecorder
        savedSessions = sessionStore.list()
        // D-022 Phase 7: rehydrate sketch layer slots (no song loaded
        // at boot, so use the sketch sentinel).
        rehydrateLayerSlots(analysisId: Self.sketchSlotId)
        // Scan disk for downloaded curated packs so they appear in the
        // Browse sheet even before the catalog loads (offline boot).
        refreshCachedPackIds()
        // Contribution engine last: subscribes the coordinator to the
        // bus, arms the scheduler's contribution guard, and pushes the
        // grid context — the pack loaded above is already active, so
        // the first layout is complete.
        modeCoordinator.start()
        // Beat Capture (D-024): pull a newer drum-classifier model in the
        // background; the next capture uses it once cached.
        modeCoordinator.refreshBeatModelInBackground()
        // Live Beat (D-025): wire sample triggering from onset detection.
        wireLiveBeat()
        // Restore the last tab (D-022). Assignment (not an init
        // default) so the didSet apply-path pins the engine mode for
        // the restored tab now that the coordinator is live.
        let restoredTab = AppTab(rawValue: sampleSettings.appTabRaw) ?? .contribute
        // No song auto-reloads at launch, so a restored Learn/Jam would
        // land on an empty surface. Fall back to Library so the user can
        // pick a song first.
        selectedTab = (restoredTab.requiresSong && currentBundle == nil)
            ? .library : restoredTab
        wireLaunchpad()

        // Background-survivable analysis completion. Wires notification
        // routing, resumes any jobs orphaned by a kill/relaunch, and
        // flushes a finished song waiting to open.
        JobCompletionCenter.shared.boot(appState: self)
    }

    /// P2: Launchpad Pro MK3 over CoreMIDI. Pad events publish on the
    /// contribution bus (stamped on the MIDI thread, PRE main-actor
    /// hop); the coordinator's padVisuals mirror onto the hardware
    /// LEDs so both grids always paint the same frame.
    private func wireLaunchpad() {
        let transport = USBLaunchpadTransport(
            midi: CoreMIDIInterface(),
            nowProvider: { [clock = audioEngine.clock] in
                (song: clock.nowSongSeconds, host: mach_absolute_time())
            }
        )
        transport.onContribution = { [weak self] event in
            self?.contributionBus.publish(event)
        }
        modeCoordinator.$padVisuals
            .sink { [weak transport] visuals in
                transport?.setLights(Self.launchpadFrame(from: visuals))
            }
            .store(in: &launchpadCancellables)

        // Chord anticipation on the Launchpad, 10 Hz while playing.
        // Learn practice takes priority (countdown top row + section
        // chord pads on the bottom row); otherwise jam chord-follow
        // paints its countdown row. When a practice session ends, one
        // full padVisuals repaint clears the practice rows.
        Timer.publish(every: 0.1, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self, weak transport] _ in
                guard let self = self else { return }
                let practicing = self.learnController.phase == .practicing
                if self.launchpadPracticeWasActive && !practicing {
                    self.launchpadPracticeWasActive = false
                    transport?.setLights(
                        Self.launchpadFrame(from: self.modeCoordinator.padVisuals))
                }
                guard self.isPlaying else { return }
                if practicing {
                    self.launchpadPracticeWasActive = true
                    transport?.setLights(Self.learnPracticeFrame(appState: self))
                } else if self.jamSettings.followEnabled {
                    transport?.setLights(Self.chordFollowRowFrame(appState: self))
                }
            }
            .store(in: &launchpadCancellables)
        // Connection flaps feed the idle-timer predicate (P7): a
        // performer jamming from the hardware grid must not have
        // auto-lock kill the session mid-set.
        transport.$connectionState
            .sink { [weak self] _ in self?.syncIdleTimer() }
            .store(in: &launchpadCancellables)
        // Underpower heuristic → root banner (P7).
        transport.$underpowerSuspected
            .removeDuplicates()
            .sink { [weak self] suspected in
                self?.underpowerBannerVisible = suspected
            }
            .store(in: &launchpadCancellables)
        #if os(iOS)
        // Best-effort courtesy on kill: hand the hardware back to its
        // standalone Live Mode so the grid isn't left dark in the
        // dead Programmer Mode (P7). No guarantee iOS delivers this,
        // hence best-effort — reconnect always resyncs anyway.
        NotificationCenter.default
            .publisher(for: UIApplication.willTerminateNotification)
            .sink { [weak transport] _ in transport?.suspend() }
            .store(in: &launchpadCancellables)
        #endif
        usbLaunchpad = transport
        wireMIDIKeyboard()
    }

    /// Generic MIDI note controllers (keyboards / pad boxes). Separate
    /// CoreMIDI client from the Launchpad so the two never contend for
    /// the same input ports; the Launchpad's grid is excluded here to
    /// avoid double-firing its pad notes.
    private func wireMIDIKeyboard() {
        let transport = MIDIKeyboardTransport(
            midi: CoreMIDIInterface(),
            nowProvider: { [clock = audioEngine.clock] in
                (song: clock.nowSongSeconds, host: mach_absolute_time())
            }
        )
        transport.onContribution = { [weak self] event in
            self?.contributionBus.publish(event)
        }
        transport.noteRouting = Self.noteRouting(
            padsToSamples: sampleSettings.midiPadsToSamples
        )
        midiKeyboard = transport
    }

    /// Persisted toggle → transport routing. `true` sends generic MIDI
    /// pads to the active sample pack (note 36 = pad 0); `false` keeps
    /// the wavetable-synth default.
    private static func noteRouting(
        padsToSamples: Bool
    ) -> MIDIKeyboardTransport.NoteRouting {
        padsToSamples
            ? .samplePads(baseNote: MIDIKeyboardTransport.defaultPadBaseNote)
            : .synth
    }

    /// Banner dismiss: clears the transport's flag (the mirror sink
    /// follows). The heuristic re-raises it on the next flap or send
    /// error, so a genuinely underpowered hub keeps warning.
    public func dismissUnderpowerBanner() {
        usbLaunchpad?.underpowerSuspected = false
        underpowerBannerVisible = false
    }

    /// PadVisual[64] (PadIndex order, row 1 = bottom) → LaunchpadLight
    /// frame (LaunchpadPad, row 0 = top). Dim pads scale channels
    /// ×0.4 so the hardware mirrors the on-screen bright/dim split.
    static func launchpadFrame(
        from visuals: [PadVisual]
    ) -> [LaunchpadPad: LaunchpadLight] {
        guard visuals.count == 64 else { return [:] }
        var frame: [LaunchpadPad: LaunchpadLight] = [:]
        for row in 1...8 {
            for col in 1...8 {
                let visual = visuals[(row - 1) * 8 + (col - 1)]
                let pad = LaunchpadPad(row: 8 - row, col: col - 1)
                if visual.colorHint == 0 {
                    frame[pad] = .off
                } else if visual.isBright {
                    frame[pad] = .solid(colorHint: visual.colorHint)
                } else {
                    frame[pad] = .solid(colorHint: Self.dimmed(visual.colorHint))
                }
            }
        }
        return frame
    }

    private static func dimmed(_ hint: UInt32) -> UInt32 {
        let r = UInt32(Double((hint >> 16) & 0xFF) * 0.4)
        let g = UInt32(Double((hint >> 8) & 0xFF) * 0.4)
        let b = UInt32(Double(hint & 0xFF) * 0.4)
        return (r << 16) | (g << 8) | b
    }

    /// Chord follow countdown row: lights up pads 0-7 on top row (row 0)
    /// based on countdown progress. Progress 0→1 lights pads left→right.
    static func chordFollowRowFrame(
        appState: AppState
    ) -> [LaunchpadPad: LaunchpadLight] {
        guard let progress = ChordFollowStrip.countdownProgress(appState: appState) else {
            return [:]
        }
        let litCount = ChordFollowStrip.launchpadLitCount(progress: progress)
        // Accent color: teal-ish (matches app accent)
        let accentHint: UInt32 = 0x00BFFF
        var frame: [LaunchpadPad: LaunchpadLight] = [:]
        for col in 0..<8 {
            let pad = LaunchpadPad(row: 0, col: col)
            if col < litCount {
                frame[pad] = .solid(colorHint: accentHint)
            } else {
                // Dim the remaining pads
                frame[pad] = .solid(colorHint: Self.dimmed(accentHint))
            }
        }
        return frame
    }

    /// True while the 10 Hz Launchpad tick is painting the Learn
    /// practice frame — lets the tick repaint the normal padVisuals
    /// frame exactly once when practice ends.
    private var launchpadPracticeWasActive = false

    /// Learn practice Launchpad frame (web launchpad-mode parity):
    ///   - top row: countdown toward the next DISTINCT chord, pads
    ///     fill left→right through a cyan→amber→red gradient and
    ///     pulse in the last 600 ms
    ///   - bottom row: the practiced section's chord progression,
    ///     active chord solid bright, up-next chord pulsing amber,
    ///     the rest dim
    static func learnPracticeFrame(
        appState: AppState
    ) -> [LaunchpadPad: LaunchpadLight] {
        var frame: [LaunchpadPad: LaunchpadLight] = [:]
        let controller = appState.learnController
        let prediction = controller.prediction()

        // Top row countdown.
        let accentHint: UInt32 = 0x00BFFF
        if let prediction {
            let lit = ChordFollowStrip.launchpadLitCount(
                progress: prediction.progress)
            for col in 0..<8 {
                let pad = LaunchpadPad(row: 0, col: col)
                if col < lit {
                    let hint = Self.countdownGradientHint(
                        Double(col) / 7.0)
                    frame[pad] = prediction.imminent
                        ? .pulse(colorHint: hint)
                        : .solid(colorHint: hint)
                } else {
                    frame[pad] = .solid(colorHint: Self.dimmed(accentHint))
                }
            }
        } else {
            for col in 0..<8 {
                frame[LaunchpadPad(row: 0, col: col)] =
                    .solid(colorHint: Self.dimmed(accentHint))
            }
        }

        // Bottom row: song chord vocabulary (first 8 distinct
        // chords, same source as the on-screen practice grid).
        if controller.activeSection != nil {
            let chords = controller.songChords
            let currentSymbol = appState.currentChord?.symbol
            let nextHint: UInt32 = 0xFFB74D  // amber, matches UI
            for col in 0..<8 {
                let pad = LaunchpadPad(row: 7, col: col)
                guard col < chords.count, col < 8 else {
                    frame[pad] = .off
                    continue
                }
                let symbol = chords[col]
                if symbol == currentSymbol {
                    frame[pad] = .solid(colorHint: accentHint)
                } else if symbol == prediction?.nextSymbol {
                    frame[pad] = .pulse(colorHint: nextHint)
                } else {
                    frame[pad] = .solid(colorHint: Self.dimmed(0xFFFFFF))
                }
            }
        }
        return frame
    }

    /// Piecewise cyan→amber→red gradient (web `_countdownGradient`):
    /// t 0…0.65 cyan→amber, 0.65…1 amber→red.
    static func countdownGradientHint(_ t: Double) -> UInt32 {
        let clamped = min(1, max(0, t))
        let (r, g, b): (Double, Double, Double)
        if clamped < 0.65 {
            let s = clamped / 0.65
            r = 77 + (255 - 77) * s
            g = 208 + (183 - 208) * s
            b = 225 + (77 - 225) * s
        } else {
            let s = (clamped - 0.65) / 0.35
            r = 255
            g = 183 + (82 - 183) * s
            b = 77 + (82 - 77) * s
        }
        return (UInt32(r) << 16) | (UInt32(g) << 8) | UInt32(b)
    }

    /// Per-pad effects resolution for the scheduler. (This used to
    /// also bridge `SampleScheduler.onEvent` → LayerRecorder; that
    /// bridge is gone — SessionCaptureRecorder subscribes to the
    /// contribution bus directly, D-015.)
    private func wireSampleEffects() {
        // Resolve per-pad effect params on every trigger. Reads the
        // user override from SampleSettingsStore and falls back to the
        // manifest baseline / neutral inside the store helper.
        sampleScheduler.effectsResolver = { [weak self] packId, padIdx, manifest in
            self?.sampleSettings.effectivePadEffects(
                packId: packId, padIdx: padIdx, manifestBaseline: manifest
            ) ?? manifest ?? .neutral
        }
    }

    /// Build the session recorder wired into the contribution bus +
    /// transport clock, with autosave flowing straight to disk under
    /// the take's single sessionId (overwrite, crash-safe).
    private func makeSessionRecorder() -> SessionCaptureRecorder {
        let recorder = SessionCaptureRecorder(
            bus: contributionBus,
            clockNow: { [weak self] in
                self?.audioEngine.clock.nowSongSeconds ?? 0
            }
        )
        recorder.onAutosave = { [weak self] session in
            guard let self else { return }
            do {
                try self.sessionStore.save(session)
            } catch {
                self.layerError =
                    "Autosave session: \(error.localizedDescription)"
            }
        }
        return recorder
    }

    /// Build the LayerPlayer with closures that route replayed events
    /// through the exact live-input paths — so a saved layer sounds
    /// bit-identical to the original take.
    private func makeLayerPlayer() -> LayerPlayer {
        LayerPlayer(
            clockNow: { [weak self] in
                self?.audioEngine.clock.nowSongSeconds ?? 0
            },
            onSampleOn: { [weak self] padIdx, packId in
                // triggerRaw (not trigger) — the saved timeline
                // already stores the intended song-times of every
                // hit, so the scheduler must not re-quantize / gate
                // them. Using the live `trigger` path was silently
                // snapping each replayed hit forward to the next
                // `defaultQuantize` boundary and then losing the
                // slot to the following event's snap, producing a
                // fully silent replay of StarterPack layers.
                //
                // packId is the pack each hit was recorded on
                // (event packIdOverride ?? timeline.activePackId) so
                // multi-pack takes replay correctly regardless of
                // which carousel page is currently active. Packs are
                // preloaded in toggleLayerPlayback.
                _ = self?.sampleScheduler.triggerRaw(padIdx: padIdx, packId: packId)
            },
            onSampleOff: { [weak self] padIdx, packId in
                self?.sampleScheduler.release(padIdx: padIdx, packId: packId)
            },
            onNoteOn: { [weak self] midi, vel in
                self?.padSynth.triggerNote(midi: midi, velocity: Float(vel * 127))
            },
            onNoteOff: { _ in
                // PadSynth voices decay naturally via their release
                // envelope; no explicit noteOff needed for replay.
            }
        )
    }

    /// Bridge `SampleSettingsStore` → `SampleScheduler`. Every user
    /// change to quantize / hold / beat-bar / section gates flows into
    /// the scheduler immediately so the next tap picks up the new
    /// policy.
    private func wireSampleSettings() {
        settingsCancellables.removeAll()

        // Ringing-loop set → AppState mirror (grids observe AppState,
        // not the pool). Publishes only when the set changes.
        sampleVoicePool.$ringingPadKeys
            .sink { [weak self] keys in
                self?.ringingPadKeys = keys
            }
            .store(in: &settingsCancellables)

        // Two quantize owners, split by loaded-song state (D-016):
        // a loaded bundle → sampleSettings; no bundle ("sketch") →
        // sketchSettings. ModeCoordinator.applyGridContext performs
        // the swap on bundle load/clear; these sinks keep the LIVE
        // owner's edits flowing.
        sampleSettings.$quantizeMode
            .sink { [weak self] mode in
                guard let self, self.currentBundle != nil else { return }
                self.sampleScheduler.quantize = mode
            }
            .store(in: &settingsCancellables)

        sketchSettings.$quantizeMode
            .sink { [weak self] mode in
                guard let self, self.currentBundle == nil else { return }
                self.sampleScheduler.quantize = mode
            }
            .store(in: &settingsCancellables)

        // NOTE: @Published sinks fire on willSet, so handlers must use
        // the delivered value — reading self.sketchSettings.X here
        // would see the OLD value. syncMetronome takes overrides for
        // exactly this reason.
        sketchSettings.$tempoBpm
            .sink { [weak self] bpm in
                guard let self, self.currentBundle == nil else { return }
                self.sampleScheduler.updateSyntheticContext(tempoBpm: bpm)
                self.syncMetronome(bpm: bpm)
            }
            .store(in: &settingsCancellables)

        sketchSettings.$timeSigNumerator
            .sink { [weak self] beats in
                guard let self, self.currentBundle == nil else { return }
                self.syncMetronome(timeSig: beats)
            }
            .store(in: &settingsCancellables)

        sketchSettings.$metronomeEnabled
            .sink { [weak self] enabled in
                guard let self, self.currentBundle == nil else { return }
                self.syncMetronome(enabled: enabled)
            }
            .store(in: &settingsCancellables)

        sampleSettings.$holdMode
            .sink { [weak self] mode in
                self?.sampleScheduler.holdMode = mode
            }
            .store(in: &settingsCancellables)

        sampleSettings.$beatBarMode
            .sink { [weak self] mode in
                self?.sampleScheduler.beatBarMode = mode
            }
            .store(in: &settingsCancellables)

        // Layer fader dB → linear on the layer bus.
        sampleSettings.$layerFaderDb
            .sink { [weak self] db in
                let linear = Float(pow(10.0, db / 20.0))
                self?.audioEngine.setLayerGain(max(0, min(2, linear)))
            }
            .store(in: &settingsCancellables)

        // Chop level → chop bus volume. Fires with the persisted
        // value on wire-up, so boot applies the stored setting too.
        sampleSettings.$chopGainLinear
            .sink { [weak self] gain in
                self?.audioEngine.setChopGain(Float(max(0, min(1, gain))))
            }
            .store(in: &settingsCancellables)

        // Voice level → voice bus volume. PadSynth's fixed 0.311 trim
        // × the 0.9 default lands on the long-standing 0.28 net gain
        // so defaults stay loudness-neutral (DECISIONS.md D-010/D-013).
        sampleSettings.$voiceGainLinear
            .sink { [weak self] gain in
                self?.audioEngine.setVoiceGain(Float(max(0, min(1, gain))))
            }
            .store(in: &settingsCancellables)

        // Vocoder-preview level → vocoder bus volume. The bus is
        // silent until P5 lands a source on it; persisting + wiring
        // the gain now keeps the P1 topology complete (D-013).
        sampleSettings.$vocoderGainLinear
            .sink { [weak self] gain in
                self?.audioEngine.setVocoderGain(Float(max(0, min(1, gain))))
            }
            .store(in: &settingsCancellables)

        // Generic MIDI pad routing → transport. Live edits flip the
        // attached pad box between synth and sample-pack pads.
        sampleSettings.$midiPadsToSamples
            .sink { [weak self] on in
                self?.midiKeyboard?.noteRouting = Self.noteRouting(padsToSamples: on)
            }
            .store(in: &settingsCancellables)

        // Master FX settings → AudioEngine (D-022). Fires with the
        // persisted value on subscribe, so boot applies stored FX.
        fxSettingsStore.$settings
            .sink { [weak self] settings in
                self?.audioEngine.setFXSettings(settings)
            }
            .store(in: &settingsCancellables)
    }

    /// Wire Live Beat (D-025): onset detection → immediate BeatKit trigger.
    /// Uses `triggerRaw` to bypass quantize — live percussion needs
    /// sub-20ms latency to feel like an instrument.
    private func wireLiveBeat() {
        // Wire the controller's sample trigger to the scheduler. Each
        // detected onset fires the BeatKit pad for the matched drum role.
        liveBeatController.onTriggerSample = { [weak self] role, velocity in
            guard let self else { return }
            // BeatKit must be preloaded for immediate triggers. Ensure it's
            // loaded now (silent if already loaded).
            if self.sampleScheduler.isPackLoaded(packId: BeatKit.packId) {
                _ = self.sampleScheduler.triggerRaw(
                    padIdx: role.padIdx, packId: BeatKit.packId
                )
            } else {
                // Fallback: load beatkit then trigger (first hit may be delayed).
                Task { [weak self] in
                    guard let self else { return }
                    await self.preloadBeatKit()
                    _ = self.sampleScheduler.triggerRaw(
                        padIdx: role.padIdx, packId: BeatKit.packId
                    )
                }
            }
        }
        // Wire the active profile from the store to the controller.
        if let profile = liveBeatProfileStore.activeProfile {
            liveBeatController.activeProfile = profile
        }
    }

    /// Preload the BeatKit pack for Live Beat mode. Non-destructive: does
    /// not change the active pack, just ensures BeatKit buffers are resident.
    private func preloadBeatKit() async {
        guard let bank = sampleBank,
              let pack = try? bank.loadBundled(packId: BeatKit.packId)
        else { return }
        await sampleScheduler.preloadPackAsync(pack, stemFiles: [:])
    }

    /// Attempt to load the bundled StarterPack (or whatever pack was
    /// active on last launch) so the app is playable immediately, even
    /// offline with no song loaded. Failures are silent — a missing
    /// bundled pack just means the Samples panel shows an empty grid
    /// until the user picks a pack.
    private func loadInitialSamplePack() {
        guard let bank = sampleBank else { return }
        let packId = sampleSettings.currentPackId
        // Song-derived packs can't be loaded here — they require a
        // bundle. Fall back to bundled starter for those cases.
        let loaded: ResolvedSamplePack? =
            (try? bank.loadBundled(packId: packId))
            ?? (try? bank.loadCached(packId: packId))
            ?? (try? bank.loadBundled(packId: "starter"))
        guard let pack = loaded else { return }
        activateSamplePack(pack, stemFiles: [:])
    }

    /// Hand a resolved pack to the scheduler + remember it as active.
    /// `stemFiles` maps stem role → local URL for song-derived packs;
    /// pass `[:]` for file-backed packs.
    public func activateSamplePack(
        _ pack: ResolvedSamplePack,
        stemFiles: [String: URL]
    ) {
        // Front the pack + publish its metadata synchronously so the
        // grid, carousel, and settings update this run loop. The WAV
        // decode (up to 16 pads at max SRC quality, ~1–3 s cold) runs
        // off-main via setActivePackAsync so switching into Contribute
        // never stalls the main thread — the stall was surfacing as a
        // frozen tab plus the iOS touch-and-hold loupe. First-visit taps
        // during the short decode window fall back to padNotFound
        // silence; revisits are already resident and trigger instantly.
        activeSamplePack = pack
        sampleSettings.currentPackId = pack.pack.packId
        // Remember packs chosen while song-less ("sketching", D-016)
        // separately so sketch-layer metadata can name them.
        if currentBundle == nil {
            sketchSettings.lastSketchPackId = pack.pack.packId
        }
        // New pack → new sample-quadrant content + pad bindings. Labels
        // come from the manifest, not the buffers, so this is safe to
        // paint before the decode lands.
        modeCoordinator.refreshLayout()
        // Register the pack's shipped starter groove (if any) into the
        // sequencer store so it shows up in the sequence picker.
        // Idempotent by the pattern's deterministic id.
        if let groove = pack.pack.defaultSequence {
            sequencerPatternStore.save(groove)
        }
        Task { await sampleScheduler.setActivePackAsync(pack, stemFiles: stemFiles) }
    }

    // MARK: - Pack carousel (multi-pack)

    /// Resolved file-backed packs, keyed by packId, so carousel pages
    /// don't re-read manifests from disk on every render. Song DNA
    /// packs aren't cached here — they already live resolved on
    /// `songDnaPacks`.
    private var resolvedPackCache: [String: ResolvedSamplePack] = [:]

    /// Every pack the carousel can page through, in display order:
    /// Song DNA → bundled Starter → downloaded curated. Derived from
    /// @Published collections, so SwiftUI re-renders the carousel
    /// when a bundle loads or a curated download completes.
    public var carouselPages: [PackPage] {
        PackPageBuilder.build(
            songDnaPacks: songDnaPacks,
            bundled: bundledPackEntries(),
            cachedPackIds: cachedPackIds.sorted(),
            catalog: curatedCatalog
        )
    }

    /// Bundled packs available as carousel pages. Currently just the
    /// Starter pack; resolved once and cached.
    private func bundledPackEntries() -> [(packId: String, name: String)] {
        if let cached = resolvedPackCache["starter"] {
            return [(cached.pack.packId, cached.pack.name)]
        }
        guard let bank = sampleBank,
              let starter = try? bank.loadBundled(packId: "starter")
        else { return [] }
        resolvedPackCache["starter"] = starter
        return [(starter.pack.packId, starter.pack.name)]
    }

    /// The ResolvedSamplePack behind a carousel page, for grid
    /// rendering + activation. nil when the backing files vanished
    /// (cache purge mid-session) — the page renders an empty grid.
    public func resolvedPack(for page: PackPage) -> ResolvedSamplePack? {
        switch page.source {
        case .songDna(let presetKey):
            return songDnaPacks.first { $0.presetKey == presetKey }?.pack
        case .bundled:
            if let cached = resolvedPackCache[page.id] { return cached }
            guard let bank = sampleBank,
                  let pack = try? bank.loadBundled(packId: page.id)
            else { return nil }
            resolvedPackCache[page.id] = pack
            return pack
        case .curated:
            if let cached = resolvedPackCache[page.id] { return cached }
            guard let bank = sampleBank,
                  let pack = try? bank.loadCached(packId: page.id)
            else { return nil }
            resolvedPackCache[page.id] = pack
            return pack
        }
    }

    /// Carousel page-settle handler: make the visible page's pack the
    /// active one (loading its buffers on first visit). Ringing voices
    /// from other packs are left untouched — that's the whole point
    /// of the multi-pack carousel.
    public func activateCarouselPage(packId: String) {
        guard activeSamplePack?.pack.packId != packId else { return }
        guard let page = carouselPages.first(where: { $0.id == packId }),
              let pack = resolvedPack(for: page)
        else { return }
        let stemFiles: [String: URL]
        if case .songDna = page.source {
            stemFiles = currentStemLocalURLs
        } else {
            stemFiles = [:]
        }
        activateSamplePack(pack, stemFiles: stemFiles)
    }

    /// Make sure `packId`'s buffers are resident in the scheduler
    /// without changing the active pack. Used before multi-pack layer
    /// replay and sample preview. Silently skips unresolvable packs
    /// (deleted cache, different song's DNA) — those events degrade to
    /// padNotFound, matching the pre-multi-pack behavior.
    public func ensurePackLoaded(packId: String) {
        guard !sampleScheduler.isPackLoaded(packId: packId) else { return }
        // Decode off-main (preloadPackAsync): this is called from the
        // layout pass for pinned non-fronted pads, so a synchronous WAV
        // decode here froze every Contribute tab switch.
        if let entry = songDnaPacks.first(where: { $0.pack.pack.packId == packId }) {
            let pack = entry.pack
            let stems = currentStemLocalURLs
            Task { await sampleScheduler.preloadPackAsync(pack, stemFiles: stems) }
            return
        }
        guard let bank = sampleBank else { return }
        if let pack = (try? bank.loadBundled(packId: packId))
            ?? (try? bank.loadCached(packId: packId)) {
            Task { await sampleScheduler.preloadPackAsync(pack, stemFiles: [:]) }
        }
    }

    /// Awaited twin of `ensurePackLoaded`: returns only after the
    /// pack's buffers are resident (or the pack is unresolvable).
    /// Bounce needs this — the fire-and-forget preload leaves a
    /// window where `baseBuffer` is still nil, so a bounce right
    /// after pack activation rendered an empty pad map ("Session
    /// has no renderable events"). `preloadPackAsync` re-checks
    /// residency after its decode, so racing an in-flight
    /// activation is safe (worst case one duplicate decode).
    private func ensurePackLoadedAsync(packId: String) async {
        guard !sampleScheduler.isPackLoaded(packId: packId),
              let pack = resolvedPack(forPackId: packId) else { return }
        let stems = songDnaPacks.contains { $0.pack.pack.packId == packId }
            ? currentStemLocalURLs : [:]
        await sampleScheduler.preloadPackAsync(pack, stemFiles: stems)
    }

    /// Resolve a bare packId to its `ResolvedSamplePack` using the
    /// same lookup order as `ensurePackLoaded`: current song's DNA
    /// packs, the active pack, then bundled and cached banks. nil =
    /// gone (deleted cache, another song's DNA, the device-local
    /// pseudo-pack). Used by the m4a exporter to gather every pack a
    /// multi-pack layer references.
    private func resolvedPack(forPackId packId: String) -> ResolvedSamplePack? {
        if let entry = songDnaPacks.first(where: {
            $0.pack.pack.packId == packId
        }) {
            return entry.pack
        }
        if let active = activeSamplePack, active.pack.packId == packId {
            return active
        }
        guard let bank = sampleBank else { return nil }
        return (try? bank.loadBundled(packId: packId))
            ?? (try? bank.loadCached(packId: packId))
    }

    /// Pad manifest info (name, family, loops) for a pack pad in any
    /// resolvable pack — used to paint a foreign-pack pad pinned to a
    /// single grid cell (multi-pack grids). nil = pack/pad unresolvable.
    public func packPadInfo(
        packId: String, padIdx: Int
    ) -> (name: String, family: SampleFamily, loops: Bool)? {
        guard let resolved = resolvedPack(forPackId: packId),
              let pad = resolved.pack.pads.first(where: { $0.padIdx == padIdx })
        else { return nil }
        return (pad.name, pad.family, pad.loopPointSec != nil)
    }

    /// Drop the current song's DNA packs from the scheduler — their
    /// stem slices can never be pages again once a different bundle
    /// (or none) is loaded. If the user was fronting one of them,
    /// fall back to the Starter pack so the grid stays playable.
    private func unloadSongDnaPacks() {
        guard !songDnaPacks.isEmpty else { return }
        let staleIds = Set(songDnaPacks.map { $0.pack.pack.packId })
        let activeWasStale = activeSamplePack.map {
            staleIds.contains($0.pack.packId)
        } ?? false
        for packId in staleIds {
            sampleScheduler.unloadPack(packId: packId)
        }
        if activeWasStale, let bank = sampleBank,
           let starter = try? bank.loadBundled(packId: "starter") {
            activateSamplePack(starter, stemFiles: [:])
        }
    }

    // MARK: - Bundle loading

    /// Fetch the bundle from the backend and activate it (download
    /// stems + wire the stem player + reset transport). If the backend
    /// is unreachable (e.g. off the home LAN — the dev backend is an
    /// mDNS host) falls back to the bundle.json persisted by a previous
    /// load, so already-downloaded songs keep working offline (D-021).
    /// `onReady` fires as soon as the song is activated (bundle set,
    /// grid/transport wired) — BEFORE the multi-minute stem download —
    /// so callers can switch to Learn immediately instead of waiting
    /// out ~175MB of stems.
    public func loadBundle(
        analysisId: String,
        onReady: (() -> Void)? = nil
    ) async {
        loadingError = nil
        loadingBundleId = analysisId
        defer { loadingBundleId = nil }
        do {
            let bundle = try await loader.fetch(
                from: backendBaseURL,
                analysisId: analysisId
            )
            try? bundleStore.saveBundle(bundle)
            await activate(bundle: bundle, onReady: onReady)
        } catch {
            if let cached = try? bundleStore.loadBundle(analysisId: analysisId) {
                await activate(bundle: cached, onReady: onReady)
            } else {
                loadingError = error.localizedDescription
            }
        }
    }

    /// Open a background-finished analysis: load its bundle and flip to
    /// Learn. Called by JobCompletionCenter when a job completes
    /// out-of-band (poll/relaunch) or a completion notification is
    /// tapped — the single entry point that turns a finished history id
    /// into a loaded song. Lands on Learn like a Library song tap.
    public func openFinishedSong(historyId: String) {
        Task { @MainActor in
            await loadBundle(analysisId: historyId) { [weak self] in
                self?.openSong()
            }
        }
    }

    /// Activate a locally persisted bundle without touching the
    /// network manifest — the Library's Downloaded section uses this
    /// when the backend history endpoint is unreachable. Cached stems
    /// load straight from disk; stems missing from the cache still
    /// surface a download error.
    public func loadCachedBundle(
        _ bundle: SongBundle,
        onReady: (() -> Void)? = nil
    ) async {
        loadingError = nil
        loadingBundleId = bundle.analysisId
        defer { loadingBundleId = nil }
        await activate(bundle: bundle, onReady: onReady)
    }

    /// Activate an already-loaded bundle (from disk cache or network).
    /// Wires the ChordAdvancer, kicks off stem download, and — when
    /// downloads complete — hands the local URLs to the StemPlayer.
    public func activate(
        bundle: SongBundle,
        onReady: (() -> Void)? = nil
    ) async {
        currentBundle = bundle
        advancer = ChordAdvancer(chords: bundle.timeline.chords)
        currentChord = nil
        nextChord = advancer.chords.first
        chordPhase = 0
        audioEngine.seek(to: 0)
        songSeconds = 0
        loopRegion = nil

        // Clear per-bundle Song DNA state; rebuilt once stems land.
        // Unload the previous song's DNA pack buffers first — those
        // packIds can never be carousel pages again.
        unloadSongDnaPacks()
        songDnaPacks = []
        currentStemLocalURLs = [:]

        // Feed quantize + section-gate context into the scheduler so
        // pads snap to this song's beats/downbeats/sections, and
        // rebuild the grid (hybrid mode keys off the song's key).
        modeCoordinator.applyGridContext()
        modeCoordinator.refreshLayout()
        // Song context: no click (bundle activation can land while
        // the sketch transport is running — activate never pauses).
        syncMetronome()

        // Refresh saved layers for this song from disk. Empty when
        // none exist. Playback set starts empty; user opts each layer
        // in from Profile → Layers.
        savedLayers = layerStore.list(analysisId: bundle.analysisId)
        activePlaybackLayerIds = []
        uploadedLayerIds = []
        uploadingLayerIds = []
        layerPlayer.clear()

        // D-022 Phase 7: rehydrate layer A/B slots for this song.
        rehydrateLayerSlots(analysisId: bundle.analysisId)

        // Song is fully activated (grid, transport, chords wired) —
        // let the caller navigate NOW, before the stem download.
        onReady?()

        await downloadAndLoad(bundle: bundle)
    }

    private func downloadAndLoad(bundle: SongBundle) async {
        isDownloading = true
        downloadExpectedStems = bundle.stems.count
        downloadProgress.removeAll()
        waveformPeaks = nil
        defer {
            isDownloading = false
            downloadExpectedStems = 0
        }
        var localURLs: [String: URL] = [:]

        do {
            for try await progress in bundleStore.download(bundle: bundle, baseURL: backendBaseURL) {
                downloadProgress[progress.role] = progress
                if progress.isComplete, let url = progress.localURL {
                    localURLs[progress.role] = url
                }
            }
            // Load stems into the player. Errors are non-fatal — a bad
            // stem shouldn't block the whole song.
            do {
                try await stemPlayer.load(bundle: bundle, localURLs: localURLs)
                // If the user hit Practice/Play mid-download, the
                // transport is running but StemPlayer.play() was a
                // silent no-op on empty channels — start the freshly
                // loaded stems at the current song position.
                if isPlaying {
                    stemPlayer.play(atSongSeconds: songSeconds)
                }
            } catch {
                loadingError = "Stem player: \(error.localizedDescription)"
            }
            // Remember stem URLs so Song DNA pack activation can slice
            // them; then synthesise the inline Song DNA packs.
            // Drop presets whose stem role has no downloaded file
            // (e.g. "mix"/"other" presets on songs separated into
            // guitar_center/guitar_sides) — their pads can never load
            // a buffer, so every tap would be a silent padNotFound.
            currentStemLocalURLs = localURLs
            songDnaPacks = SongDnaPack.synthesize(from: bundle)
                .filter { localURLs[$0.stem] != nil }
            updateWaveformPeaks(
                analysisId: bundle.analysisId,
                stemURLs: localURLs.values.sorted { $0.path < $1.path }
            )
        } catch {
            loadingError = error.localizedDescription
        }
    }

    /// Compute (or load cached) scrubber peaks for the song's local
    /// stems, off-main. Result is dropped if the user ejected/loaded
    /// another song while extraction ran.
    private func updateWaveformPeaks(analysisId: String, stemURLs: [URL]) {
        waveformPeaks = nil
        guard !stemURLs.isEmpty else { return }
        Task { [weak self] in
            let peaks = await Task.detached(priority: .utility) { () -> [Float] in
                let cache = WaveformCache()
                if let cached = cache.load(analysisId: analysisId),
                   !cached.isEmpty {
                    return cached
                }
                let extracted = WaveformPeakExtractor.extractPeaks(
                    stemURLs: stemURLs
                )
                cache.save(extracted, analysisId: analysisId)
                return extracted
            }.value
            guard let self, self.currentBundle?.analysisId == analysisId else {
                return
            }
            self.waveformPeaks = peaks
        }
    }

    /// Convenience: hand a Song DNA pack straight to the scheduler,
    /// wiring in the current bundle's stem URLs so `StemSlice` pads
    /// can find their audio.
    public func activateSongDnaPack(_ entry: SongDnaPack) {
        activateSamplePack(entry.pack, stemFiles: currentStemLocalURLs)
    }

    // MARK: - Deletion (compliance)

    /// Delete one analysis everywhere: server (history entry + stems +
    /// R2 objects + layers via the deep-delete route), local bundle
    /// JSON + cached stems, and the in-memory state if it's the song
    /// currently loaded. Throws on server failure so the UI can keep
    /// the row and surface the error.
    public func deleteAnalysis(analysisId: String) async throws {
        try await HistoryClient().delete(baseURL: backendBaseURL, entryId: analysisId)
        bundleStore.deleteLocal(analysisId: analysisId)
        if currentBundle?.analysisId == analysisId {
            clearLoadedBundle()
        }
    }

    /// Wipe all server-side analyses (DELETE /api/history deep-deletes
    /// every entry's artifacts) plus the whole local cache.
    public func deleteAllServerData() async throws {
        try await HistoryClient().deleteAll(baseURL: backendBaseURL)
        bundleStore.deleteAllLocal()
        clearLoadedBundle()
    }

    /// User-facing eject: return the Play tab to the song-less sketch
    /// surface (D-016). Keeps the analysis on disk + in history — only
    /// the in-memory load is dropped.
    public func ejectSong() {
        clearLoadedBundle()
    }

    /// Drop the loaded song from memory: transport stopped, stems
    /// unloaded, chord/DNA/layer state reset. Leaves curated packs and
    /// user settings untouched.
    private func clearLoadedBundle() {
        pause()
        stemPlayer.unload()
        currentBundle = nil
        advancer = ChordAdvancer(chords: [])
        currentChord = nil
        nextChord = nil
        chordPhase = 0
        songSeconds = 0
        loopRegion = nil
        currentStemLocalURLs = [:]
        waveformPeaks = nil
        unloadSongDnaPacks()
        songDnaPacks = []
        savedLayers = []
        activePlaybackLayerIds = []
        uploadedLayerIds = []
        uploadingLayerIds = []
        layerPlayer.clear()
        // D-022 Phase 7: clear layer A/B slots on eject.
        clearLayerSlots()
        downloadProgress.removeAll()
        loadingError = nil
        // No song → sketch context (synthetic tempo grid, D-016) +
        // grid rebuild (hybrid loses its key coloring).
        modeCoordinator.applyGridContext()
        modeCoordinator.refreshLayout()
    }

    // MARK: - Curated packs

    /// Refresh `curatedCatalog` from the backend. Also rescans the
    /// cache dir so `cachedPackIds` reflects which catalog entries are
    /// already downloaded. Failures are surfaced via `curatedError`.
    public func refreshCuratedCatalog() async {
        curatedError = nil
        do {
            let entries = try await packClient.fetchCatalog(baseURL: backendBaseURL)
            curatedCatalog = entries
            refreshCachedPackIds()
            await refreshCachedManifests()
        } catch {
            curatedError = error.localizedDescription
        }
    }

    /// Re-fetch the manifest for every cached pack so a backend schema
    /// bump (e.g. v1→v2 adding `defaultSequence`) reaches installs that
    /// already downloaded the pads — the pad-existence cache check never
    /// re-fetches on its own. When a refreshed manifest carries a
    /// starter groove, register it into the sequencer store (idempotent
    /// by the pattern's deterministic id) so it appears in the picker.
    /// Per-pack failures are ignored (offline keeps the cached copy).
    private func refreshCachedManifests() async {
        guard let bank = sampleBank else { return }
        for packId in cachedPackIds {
            guard let fresh = try? await packClient.refreshCachedManifest(
                baseURL: backendBaseURL,
                packId: packId,
                cacheRoot: bank.cachedPacksRoot
            ) else { continue }
            if let groove = fresh.defaultSequence {
                sequencerPatternStore.save(groove)
            }
        }
    }

    /// Recompute `cachedPackIds` by scanning the cache dir for pack
    /// directories with a manifest.json. Disk is the source of truth
    /// (works offline + covers packs later delisted from the catalog);
    /// the catalog only supplies display names. Cheap enough to run
    /// every catalog refresh + every download complete + at boot.
    private func refreshCachedPackIds() {
        guard let bank = sampleBank else {
            cachedPackIds = []
            return
        }
        cachedPackIds = Set(bank.listCachedPackIds())
    }

    /// Download a curated pack in the background. Progress lands in
    /// `curatedDownloads[packId]`; when the terminal event arrives the
    /// pack is added to `cachedPackIds`. Safe to call again on a pack
    /// that's already cached — the client will short-circuit to a
    /// single complete event.
    public func downloadCuratedPack(_ entry: SamplePackCatalogEntry) async {
        guard let bank = sampleBank else {
            curatedError = "Sample bank unavailable."
            return
        }
        curatedError = nil
        // Seed a 0/N progress so the UI can paint immediately.
        curatedDownloads[entry.packId] = PackDownloadProgress(
            packId: entry.packId,
            padsCompleted: 0,
            padsTotal: entry.padCount,
            bytesDownloaded: 0,
            bytesTotal: entry.sizeBytes ?? 0,
            isComplete: false,
            manifestLocalURL: nil,
            packLocalDir: bank.cachedPackDir(packId: entry.packId)
        )
        do {
            for try await progress in packClient.download(
                baseURL: backendBaseURL,
                packId: entry.packId,
                cacheRoot: bank.cachedPacksRoot
            ) {
                curatedDownloads[entry.packId] = progress
                if progress.isComplete {
                    cachedPackIds.insert(entry.packId)
                }
            }
        } catch {
            curatedError = "Pack download: \(error.localizedDescription)"
            curatedDownloads.removeValue(forKey: entry.packId)
        }
    }

    /// Load a downloaded curated pack from disk and hand it to the
    /// scheduler. No-op if the pack isn't cached yet.
    public func activateCuratedPack(packId: String) {
        guard let bank = sampleBank else {
            curatedError = "Sample bank unavailable."
            return
        }
        do {
            let pack = try bank.loadCached(packId: packId)
            activateSamplePack(pack, stemFiles: [:])
        } catch {
            curatedError = "Activate '\(packId)': \(error.localizedDescription)"
        }
    }

    /// Fetch chops for a `(stem, sliceMode)` combo that isn't already
    /// in the bundle's inline presets, build a virtual pack, and
    /// activate it. Used by the Browse sheet when the user picks a
    /// slice mode not covered by the backend's default preset set.
    public func loadAndActivateChops(stem: String, sliceMode: String) async {
        guard let bundle = currentBundle else { return }
        do {
            let chops = try await chopsClient.fetchChops(
                baseURL: backendBaseURL,
                analysisId: bundle.analysisId,
                stem: stem,
                sliceMode: sliceMode
            )
            guard !chops.isEmpty else { return }
            let preset = BundlePreset(stem: stem, sliceMode: sliceMode, chops: chops)
            let packId = "song-derived:\(bundle.analysisId):\(stem)-\(sliceMode)"
            let display = "\(stem.capitalized) — \(sliceMode)"
            let resolved = SampleBank.songDerived(
                preset: preset,
                packId: packId,
                name: display
            )
            let entry = SongDnaPack(
                presetKey: "\(stem):\(sliceMode)",
                stem: stem,
                sliceMode: sliceMode,
                displayName: display,
                chopCount: chops.count,
                pack: resolved
            )
            // Dedupe by presetKey; overwrite if an older entry existed.
            songDnaPacks.removeAll { $0.presetKey == entry.presetKey }
            songDnaPacks.append(entry)
            // Drop any previously-loaded buffers for this packId —
            // preloadPack no-ops on already-loaded packs, so a refetch
            // would otherwise keep serving the stale chop set.
            sampleScheduler.unloadPack(packId: packId)
            activateSongDnaPack(entry)
        } catch {
            loadingError = "Chops fetch: \(error.localizedDescription)"
        }
    }

    // MARK: - Scene lifecycle (P7)

    /// Scene-phase transitions, wired from `ToneForgeScene`. The app
    /// has no background-audio entitlement, so `.background` winds
    /// everything down cleanly rather than pretending to continue:
    ///   - Launchpad returns to standalone Live Mode (`suspend()`);
    ///     `.active` re-enters Programmer Mode and repaints the full
    ///     grid from the LED cache (`resume()`).
    ///   - In-flight mic / vocoder captures are discarded — captures
    ///     cap at 8 s and a take truncated mid-word is never worth
    ///     keeping (both cancels are no-ops when idle).
    ///   - The transport parks via `pause()`, which also stamps the
    ///     recording take's pause gap.
    ///   - A recording take is autosaved IMMEDIATELY — the 10 s
    ///     autosave may be stale and iOS may never resume us. The
    ///     recorder stays armed so returning to the app continues
    ///     the same take (same sessionId, same file).
    /// `.inactive` (app switcher, incoming call banner) deliberately
    /// changes nothing — flapping the Launchpad mode SysEx on every
    /// notification shade pull would be worse than useless.
    public func handleScenePhase(_ phase: ScenePhase) {
        switch phase {
        case .background:
            usbLaunchpad?.suspend()
            micRecorder.cancel()
            vocoderCapture.cancel()
            if isPlaying { pause() }
            if sessionRecorder.state == .recording {
                do {
                    try sessionStore.save(sessionRecorder.snapshot())
                } catch {
                    layerError =
                        "Autosave: \(error.localizedDescription)"
                }
            }
        case .active:
            usbLaunchpad?.resume()
            // A job that finished while we were backgrounded queues its
            // song; opening it here (rather than mid-poll) guarantees the
            // UI is live when the performance tab flips in.
            JobCompletionCenter.shared.flushPendingOpen()
        case .inactive:
            break
        @unknown default:
            break
        }
        syncIdleTimer()
    }

    /// Apply the IdleTimerPolicy predicate to the system idle timer.
    /// Called from every state change that feeds the predicate:
    /// transport play/pause, recorder arm/stop/cancel, Launchpad
    /// connection flaps, and scene-phase transitions.
    private func syncIdleTimer() {
        #if os(iOS)
        let connected: Bool
        if case .connected = usbLaunchpad?.connectionState {
            connected = true
        } else {
            connected = false
        }
        UIApplication.shared.isIdleTimerDisabled =
            IdleTimerPolicy.shouldDisableIdleTimer(
                isPlaying: isPlaying,
                launchpadConnected: connected,
                recorderActive: sessionRecorder.state != .idle,
                captureActive: micRecorder.isRecording
                    || vocoderCapture.isCapturing
            )
        #endif
    }

    // MARK: - Transport

    public func play() {
        audioEngine.play()
        stemPlayer.play(atSongSeconds: audioEngine.clock.nowSongSeconds)
        isPlaying = true
        startTicking()
        // Layer replay follows transport — start only if the user has
        // opted at least one layer in.
        if !activePlaybackLayerIds.isEmpty {
            layerPlayer.start()
        }
        // Session replay follows transport the same way (P6, D-015).
        if replayingSessionId != nil {
            sessionPlayer.start()
        }
        // Layer A/B slot players (D-022 Phase 7): both start together
        // so recorded layers on both slots play back simultaneously.
        // Skip if a legacy session replay is active — the user is
        // explicitly replaying a specific session from the shelf.
        if replayingSessionId == nil {
            for (slot, player) in slotPlayers where layerSlots.hasTake(slot) {
                player.start()
            }
        }
        // User has taken explicit ownership of the transport — from
        // here on, toggling a layer off should NOT auto-pause.
        transportStartedByLayer = false
        syncMetronome()
        syncIdleTimer()
    }

    public func pause() {
        // A recording take marks the pause so bounce/replay know the
        // performance stopped here (no-op unless recording).
        sessionRecorder.noteTransportPause()
        audioEngine.pause()
        stemPlayer.pause()
        isPlaying = false
        stopTicking()
        layerPlayer.stop()
        sessionPlayer.stop()
        // Layer A/B slot players (D-022 Phase 7): both stop together.
        for player in slotPlayers.values {
            player.stop()
        }
        transportStartedByLayer = false
        syncMetronome()
        syncIdleTimer()
    }

    /// Reconcile the metronome with the current app state: it clicks
    /// only in sketch context (no bundle) with the toggle on and the
    /// transport playing. Parameters override the corresponding
    /// sketchSettings read for callers inside @Published sinks (which
    /// fire on willSet, before the store's property updates).
    ///
    /// Jam in Key is the exception (redesign Phase 7): it drives the
    /// metronome from `jamSettings` — its own accent/sound/subdivide
    /// and enable toggle — and also runs with a song loaded (at the
    /// song's tempo). Internal (not private) so ModeCoordinator and
    /// JamInKeyController can re-sync on mode/settings changes.
    func syncMetronome(
        bpm: Double? = nil, timeSig: Int? = nil, enabled: Bool? = nil
    ) {
        if modeCoordinator.appMode == .jamInKey {
            // Guard the sound assignment — its didSet rebuilds the
            // click buffers, which we don't want on every sync.
            if metronome.sound != jamSettings.metronomeSound {
                metronome.sound = jamSettings.metronomeSound
            }
            metronome.update(grid: MetronomeGrid(
                bpm: currentBundle?.meta.tempoBpm
                    ?? (bpm ?? sketchSettings.tempoBpm),
                beatsPerBar: timeSig ?? sketchSettings.timeSigNumerator,
                accent: jamSettings.metronomeAccent,
                subdivide: jamSettings.metronomeSubdivide
            ))
            if jamSettings.metronomeEnabled && isPlaying {
                metronome.start()
            } else {
                metronome.stop()
            }
            return
        }
        // Leaving jam mode restores the historic sketch click.
        if metronome.sound != .sine {
            metronome.sound = .sine
        }
        metronome.update(grid: MetronomeGrid(
            bpm: bpm ?? sketchSettings.tempoBpm,
            beatsPerBar: timeSig ?? sketchSettings.timeSigNumerator
        ))
        let shouldRun = currentBundle == nil
            && (enabled ?? sketchSettings.metronomeEnabled)
            && isPlaying
        if shouldRun {
            metronome.start()
        } else {
            metronome.stop()
        }
    }

    public func seek(to seconds: Double) {
        // Gap marker BEFORE the clock moves — the recorder stamps the
        // pre-seek position (no-op unless recording).
        sessionRecorder.noteTransportSeek(
            from: audioEngine.clock.nowSongSeconds, to: seconds
        )
        audioEngine.seek(to: seconds)
        stemPlayer.seek(to: seconds)
        songSeconds = seconds
        refreshChordFrame()
        layerPlayer.seek(to: seconds)
        sessionPlayer.seek(to: seconds)
        // Layer A/B slot players (D-022 Phase 7).
        for player in slotPlayers.values {
            player.seek(to: seconds)
        }
    }

    /// Move to `seconds` and force playback ON. Used by section-chip
    /// taps where the user expects the song to start playing from the
    /// chosen section, and — critically — the play/pause button to
    /// reflect that so a single subsequent tap stops playback. The
    /// separate `seek(to:)` above is preserved for scrubber drags and
    /// rewind/ff, which must preserve prior play/pause state.
    public func seekAndPlay(to seconds: Double) {
        // Gap marker BEFORE the clock moves (no-op unless recording).
        sessionRecorder.noteTransportSeek(
            from: audioEngine.clock.nowSongSeconds, to: seconds
        )
        // Reposition the transport clock first so the ensuing play()
        // anchors at `seconds` rather than 0.
        audioEngine.seek(to: seconds)
        songSeconds = seconds
        refreshChordFrame()
        layerPlayer.seek(to: seconds)
        sessionPlayer.seek(to: seconds)
        // Layer A/B slot players (D-022 Phase 7).
        for player in slotPlayers.values {
            player.seek(to: seconds)
        }
        if isPlaying {
            // Already playing — restart stems at the new position so
            // audio matches the clock's new anchor.
            stemPlayer.seek(to: seconds)
        } else {
            // Not playing: run the full play() path so isPlaying flips
            // true, clock enters .playing, and stems + layer replay
            // all engage together.
            play()
        }
    }

    public func togglePlayPause() {
        if isPlaying { pause() } else { play() }
    }

    /// Set (or clear, with nil) the A/B practice loop. The region
    /// takes effect on the next tick — no seek happens here, so
    /// callers that want playback inside the loop (Learn's Start
    /// Section) pair this with `seekAndPlay(to:)`.
    public func setLoop(_ region: LoopRegion?) {
        loopRegion = region
    }

    /// Set the practice playback rate (D-022, Learn tab's Speed
    /// control). Clamped to `LearnSettingsStore.rateRange` and
    /// persisted. No-op while the session recorder is armed or
    /// recording — captured timestamps are song-seconds against the
    /// real-time grid and must not be produced at slowed rates.
    public func setPlaybackRate(_ rate: Double) {
        guard sessionRecorder.state == .idle else { return }
        let r = clampedPracticeRate(rate)
        learnSettings.practiceRateX = r
        applyPlaybackRate(r)
    }

    private func clampedPracticeRate(_ rate: Double) -> Double {
        min(
            max(rate, LearnSettingsStore.rateRange.lowerBound),
            LearnSettingsStore.rateRange.upperBound
        )
    }

    /// Push `rate` to the clock + stem timePitch + published state
    /// WITHOUT touching the persisted practice rate — tab switches
    /// and recorder arming force 1.0 temporarily while the user's
    /// chosen Learn speed survives for the next practice session.
    private func applyPlaybackRate(_ rate: Double) {
        playbackRate = rate
        audioEngine.clock.setRate(rate)
        stemPlayer.setPlaybackRate(rate)
    }

    /// Set the engine's master output gain. Clamped to 0..1.
    public func setMasterGain(_ gain: Double) {
        let g = max(0, min(1, gain))
        masterGain = g
        audioEngine.setMasterGain(Float(g))
    }

    // MARK: - Sample voice utilities

    /// Panic/stop-all: fade out every ringing sample voice across all
    /// packs (loops and tails alike). Stems + pad synth are untouched.
    public func stopAllSamplePads() {
        sampleVoicePool.stopAll()
        modeCoordinator.sequencePadManager.stopAll()
    }

    /// Update the per-song section allowlist. Persists to
    /// `sampleSettings` and pushes to the scheduler so the next tap
    /// gates against the new set. Pass `nil` to allow all sections.
    public func setSectionGates(_ labels: Set<String>?) {
        guard let bundle = currentBundle else { return }
        sampleSettings.setSectionGates(labels, for: bundle.analysisId)
        sampleScheduler.allowedSections = labels
    }

    // MARK: - UI tick loop

    /// Drives songSeconds + currentChord updates while playing. 30 Hz
    /// is enough for the ribbon animation — the audio is always sample
    /// accurate, this timer is purely for UI paint.
    private func startTicking() {
        stopTicking()
        tickTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            Task { @MainActor in
                self.tick()
            }
        }
    }

    private func stopTicking() {
        tickTimer?.invalidate()
        tickTimer = nil
    }

    private func tick() {
        songSeconds = audioEngine.clock.nowSongSeconds
        // A/B loop wrap (redesign Phase 5): tick-driven (≤33 ms
        // jitter, fine for practice). Wrapping through the regular
        // seek path re-anchors stems/layers/chords together; a
        // scrub far past the region does not snap back (see
        // LoopRegion.wrapTarget).
        if isPlaying, let target = loopRegion?.wrapTarget(now: songSeconds) {
            seek(to: target)
            if let handler = onLoopWrap {
                handler()
            }
        }
        refreshChordFrame()
    }

    /// Invoked after each A/B loop wrap. Learn mode hooks this to
    /// score the just-finished practice pass (Phase 8).
    public var onLoopWrap: (() -> Void)?

    private func refreshChordFrame() {
        let previousSymbol = currentChord?.symbol
        let frame = advancer.frame(at: songSeconds)
        currentChord = frame.active
        nextChord = frame.next
        chordPhase = frame.phase
        // Hybrid mode brightens the sounding chord's tones — rebuild
        // only on actual chord changes (~once per bar, not per tick).
        if frame.active?.symbol != previousSymbol {
            modeCoordinator.chordChanged()
        }
    }

    // MARK: - Session recording (P6, D-015)

    /// Arm the session recorder against the current context. Song
    /// loaded → keyed to its analysisId, timing from the analysed
    /// grid (no fixed tempo); no song → the sketch settings' tempo,
    /// with an optional one-bar count-in (the transport seeks to
    /// negative time; ModeCoordinator suppresses the sound via
    /// `isCountingIn` and the recorder skips negative-timestamp
    /// events, so captured events always land at songTime ≥ 0).
    /// Also kicks off playback if the transport wasn't running —
    /// recording without the clock advancing would capture
    /// meaningless timestamps. The arm-time seek inserts no gap
    /// marker (the recorder is armed, not yet recording).
    /// - Parameter startTransport: when false, arms the recorder without
    ///   starting the song transport (or the sketch count-in). Used by the
    ///   sequencer, whose own clock drives playback — pressing Record there
    ///   should not kick off the song.
    public func armSessionRecording(startTransport: Bool = true) {
        layerError = nil
        // Recording at practice speed would capture timestamps
        // skewed against the real-time grid — force 1.0 for the take
        // (D-022; the persisted Learn rate is untouched).
        if playbackRate != 1.0 {
            applyPlaybackRate(1.0)
        }
        if let bundle = currentBundle {
            sessionRecorder.arm(
                songBackendId: bundle.analysisId,
                appMode: modeCoordinator.appMode,
                tempoBpm: nil,
                padMapping: modeCoordinator.currentPadMapping()
            )
        } else {
            sessionRecorder.arm(
                songBackendId: nil,
                appMode: modeCoordinator.appMode,
                tempoBpm: sketchSettings.tempoBpm,
                padMapping: modeCoordinator.currentPadMapping()
            )
            if startTransport {
                let barDuration = Double(sketchSettings.timeSigNumerator)
                    * 60.0 / sketchSettings.tempoBpm
                seek(to: sketchSettings.countInEnabled ? -barDuration : 0)
            }
        }
        if startTransport, !isPlaying {
            play()
        }
        syncIdleTimer()
    }

    /// True while the transport is running the negative-time count-in
    /// window of a sketch recording. Pad + note input is suppressed
    /// so nothing can be captured before songTime 0.
    public var isCountingIn: Bool {
        currentBundle == nil && isPlaying
            && audioEngine.clock.nowSongSeconds < 0
    }

    /// Stop the recorder. The take is already on disk — stop() fires
    /// one final autosave through `sessionStore` — so this just
    /// refreshes the shelf. Silent no-op if nothing was captured.
    ///
    /// D-022 Phase 7: stamps the active slot label on the capture,
    /// re-saves, assigns it to layerSlots, and loads the slot player
    /// so the take immediately participates in playback.
    public func stopAndSaveSessionRecording() {
        guard var session = sessionRecorder.stop() else {
            // Nothing captured; still need to sync state.
            syncIdleTimer()
            if currentBundle == nil {
                pause()
                seek(to: 0)
            }
            return
        }
        // Stamp the active slot label and re-save (overwriting the
        // onAutosave write that just happened).
        let activeSlot = layerSlots.active
        session.slotLabel = activeSlot.rawValue
        try? sessionStore.save(session)
        savedSessions = sessionStore.list()

        // Assign to the active slot and persist the slot state.
        let slotKey = currentBundle?.analysisId ?? Self.sketchSlotId
        layerSlots.assign(sessionId: session.sessionId, to: activeSlot)
        slotStates[slotKey] = layerSlots

        // Load the slot player so the new take replays immediately.
        loadSlotPlayer(slot: activeSlot, session: session)

        // Sketch context: stopping the take also parks the transport.
        // There's no song underneath to keep listening to — the
        // metronome would just click on forever.
        if currentBundle == nil {
            pause()
            seek(to: 0)
        }
        syncIdleTimer()
    }

    /// Discard the take: drop the recorder's buffer AND the
    /// autosaved file (a >10 s take has already hit disk).
    public func cancelSessionRecording() {
        guard sessionRecorder.state != .idle else { return }
        let sessionId = sessionRecorder.snapshot().sessionId
        sessionRecorder.cancel()
        try? sessionStore.delete(sessionId: sessionId)
        savedSessions = sessionStore.list()
        if currentBundle == nil {
            pause()
            seek(to: 0)
        }
        syncIdleTimer()
    }

    // MARK: - Layer A/B slots (D-022 Phase 7)

    /// Toggle the active recording slot (A ↔ B). No-op while recording.
    /// Returns the newly active slot.
    @discardableResult
    public func toggleActiveSlot() -> RecordingSlot {
        guard sessionRecorder.state == .idle else { return layerSlots.active }
        let newSlot = layerSlots.toggleActive()
        let slotKey = currentBundle?.analysisId ?? Self.sketchSlotId
        slotStates[slotKey] = layerSlots
        return newSlot
    }

    /// Load a session into the specified slot player. The player pumps
    /// events through the bus with `isReplay = true`.
    ///
    /// Unlike the legacy sessionPlayer (toggled from the sessions list),
    /// slot replay does NOT apply a pad-mapping overlay — the live grid
    /// stays as the user configured it, and slot events route through
    /// the session's captured padMapping. This allows both slots to
    /// replay simultaneously without conflicting overlays.
    private func loadSlotPlayer(slot: RecordingSlot, session: SessionCapture) {
        guard let player = slotPlayers[slot] else { return }
        // Ensure referenced packs are resident (same pattern as
        // toggleSessionReplay).
        for ref in session.padMapping.values {
            if case .packPad(let packId, _) = ref {
                ensurePackLoaded(packId: packId)
            }
        }
        player.load(session)
        player.seek(to: audioEngine.clock.nowSongSeconds)
    }

    /// Rehydrate layer slots for a song (or sketch). Finds the most
    /// recent take per slot label and loads both slot players.
    private func rehydrateLayerSlots(analysisId: String) {
        // Restore or create slot state for this song.
        let key = analysisId
        if let existing = slotStates[key] {
            layerSlots = existing
        } else {
            layerSlots = LayerSlots()
        }

        // Find the latest take per slot label for this song.
        // For sketch (sentinel key), sessions have songBackendId == nil.
        let isSketch = analysisId == Self.sketchSlotId
        let relevant = savedSessions.filter {
            isSketch ? $0.songBackendId == nil : $0.songBackendId == analysisId
        }
        var latestA: SessionCapture?
        var latestB: SessionCapture?
        for session in relevant {
            switch session.slotLabel {
            case "A":
                if latestA == nil || session.capturedAt > latestA!.capturedAt {
                    latestA = session
                }
            case "B":
                if latestB == nil || session.capturedAt > latestB!.capturedAt {
                    latestB = session
                }
            default:
                break
            }
        }

        // Assign and load.
        if let a = latestA {
            layerSlots.assign(sessionId: a.sessionId, to: .a)
            loadSlotPlayer(slot: .a, session: a)
        } else {
            layerSlots.clear(slot: .a)
            slotPlayers[.a]?.clear()
        }
        if let b = latestB {
            layerSlots.assign(sessionId: b.sessionId, to: .b)
            loadSlotPlayer(slot: .b, session: b)
        } else {
            layerSlots.clear(slot: .b)
            slotPlayers[.b]?.clear()
        }

        slotStates[key] = layerSlots
    }

    /// Clear layer slots (e.g., on song eject).
    private func clearLayerSlots() {
        for player in slotPlayers.values {
            player.stop()
            player.clear()
        }
        layerSlots = LayerSlots()
        modeCoordinator.clearReplayOverlay()
    }

    // MARK: - Session replay (P6, D-015)

    /// Toggle replay of a saved session. Loading switches to the
    /// session's mode, restores its pad mapping as a transient
    /// overlay (packs preloaded, local samples re-decoded), and
    /// follows the transport — starts immediately when already
    /// playing, else starts playback. Toggling the replaying session
    /// again stops it.
    public func toggleSessionReplay(sessionId: UUID) {
        if replayingSessionId == sessionId {
            stopSessionReplay()
            return
        }
        guard let session = savedSessions.first(
            where: { $0.sessionId == sessionId }
        ) else { return }
        modeCoordinator.setMode(session.appMode)
        // Resident buffers for every pack the mapping references —
        // the same guarantee toggleLayerPlayback makes for legacy
        // layers. Unresolvable packs degrade to silent pads.
        for ref in session.padMapping.values {
            if case .packPad(let packId, _) = ref {
                ensurePackLoaded(packId: packId)
            }
        }
        modeCoordinator.applyReplayOverlay(session)
        sessionPlayer.load(session)
        // Set BEFORE play() so its replay-start branch sees it.
        replayingSessionId = sessionId
        if isPlaying {
            sessionPlayer.start()
        } else {
            play()
        }
    }

    /// Stop session replay + drop the pad-mapping overlay. The
    /// transport keeps whatever state it had.
    public func stopSessionReplay() {
        sessionPlayer.stop()
        sessionPlayer.clear()
        modeCoordinator.clearReplayOverlay()
        replayingSessionId = nil
    }

    /// Delete a saved session from disk (stopping its replay first).
    public func deleteSession(sessionId: UUID) {
        if replayingSessionId == sessionId {
            stopSessionReplay()
        }
        do {
            try sessionStore.delete(sessionId: sessionId)
        } catch {
            layerError = "Delete session: \(error.localizedDescription)"
        }
        savedSessions = sessionStore.list()
    }

    /// Delete every saved session (storage browser, P7). Stops any
    /// active replay first — its session is about to vanish.
    public func deleteAllSessions() {
        stopSessionReplay()
        for session in savedSessions {
            try? sessionStore.delete(sessionId: session.sessionId)
        }
        savedSessions = sessionStore.list()
    }

    /// Delete every locally-captured sample (storage browser, P7).
    /// Routes through `ModeCoordinator.deleteLocalSample` so pad
    /// bindings referencing a sample are unassigned before its
    /// WAV + sidecar leave the disk.
    public func deleteAllLocalSamples() {
        for meta in padSampleStore.samples {
            modeCoordinator.deleteLocalSample(id: meta.id)
        }
    }

    // MARK: - Session bounce (P6, D-015)

    /// Offline-bounce a saved session to an audio file in
    /// Documents/bounces. Deterministic: the renderer is pure-Swift
    /// DSP (no AVAudioEngine — see D-015), so the same session
    /// bounces bit-identically every run. Pad audio resolves from
    /// the session's own padMapping — packs load on demand, local
    /// samples decode from the store, armed transform chains apply,
    /// missing audio renders silent. `includeOriginalSong` mixes the
    /// loaded song's cached stems underneath and requires the
    /// ownership attestation (UI gates it AND the renderer re-checks
    /// and throws). Returns the file URL for the share sheet, or nil
    /// with the reason in `layerError`.
    public func bounceSession(
        sessionId: UUID,
        includeOriginalSong: Bool = false,
        format: BounceFormat = .wav
    ) async -> URL? {
        guard let session = savedSessions.first(
            where: { $0.sessionId == sessionId }
        ) else {
            layerError = "Session no longer in library"
            return nil
        }
        bouncingSessionIds.insert(sessionId)
        defer { bouncingSessionIds.remove(sessionId) }

        // Pad audio from the session's mapping, post-transform (the
        // resolver applies the pad's armed chain — same audio the
        // live path plays).
        var padBuffers: [Int: AVAudioPCMBuffer] = [:]
        for (addr, ref) in session.padMapping
        where addr.mode == session.appMode {
            let raw = addr.pad.rawValue
            switch ref {
            case .packPad(let packId, let padIdx):
                await ensurePackLoadedAsync(packId: packId)
                guard let base = sampleScheduler.baseBuffer(
                    packId: packId, padIdx: padIdx
                ) else { continue }
                padBuffers[raw] = sampleScheduler
                    .transformResolver?(base, packId, padIdx) ?? base
            case .localSample(let id):
                guard let base = try? await padSampleStore.loadBuffer(id: id)
                else { continue }
                padBuffers[raw] = sampleScheduler.transformResolver?(
                    base, SampleScheduler.localPackId, raw
                ) ?? base
            case .sequence:
                // Sequences have no single buffer to bounce; skip.
                continue
            }
        }

        // Hybrid sessions need the note surface; song-key context
        // applies only when the session's song is the loaded one.
        let layout: any GridLayoutProviding
        if session.appMode == .hybrid {
            let keyLabel = session.songBackendId != nil
                && session.songBackendId == currentBundle?.analysisId
                ? currentBundle?.meta.detectedKey
                : nil
            layout = HybridModeLayout(
                keyLabel: keyLabel,
                chordPitchClasses: [],
                sampleContent: [:]
            )
        } else {
            layout = SampleModeLayout(content: [:])
        }

        // Live mix state → bounce gains (layer fader dB → linear).
        let reverb = audioEngine.reverbParams
        let gains = BounceGains(
            voice: Float(sampleSettings.voiceGainLinear),
            chop: Float(sampleSettings.chopGainLinear),
            layer: Float(pow(10.0, sampleSettings.layerFaderDb / 20.0)),
            dry: reverb.dryGain,
            wet: reverb.wetGain,
            reverbSeconds: reverb.seconds
        )
        let synthParams = wavetableSynthNode.params
        // Attestation persists in UserDefaults; a fresh store reads
        // the same flag the Settings sheet wrote.
        let attested = AttestationStore().isAccepted

        // Original-song mix: only meaningful when the session's song
        // is the loaded one (its stems are what's cached on disk).
        var songAudio: AVAudioPCMBuffer?
        if includeOriginalSong,
           let songId = session.songBackendId,
           songId == currentBundle?.analysisId,
           !currentStemLocalURLs.isEmpty {
            let urls = currentStemLocalURLs
                .sorted { $0.key < $1.key }
                .map(\.value)
            let rate = AudioEngine.canonicalSampleRate
            songAudio = await Task.detached(priority: .userInitiated) {
                Self.stemMix(urls: urls, sampleRate: rate)
            }.value
        }

        let outputDir = FileManager.default.urls(
            for: .documentDirectory, in: .userDomainMask
        )[0].appendingPathComponent("bounces", isDirectory: true)

        do {
            try FileManager.default.createDirectory(
                at: outputDir, withIntermediateDirectories: true
            )
            let buffers = padBuffers
            let song = songAudio
            let url = try await Task.detached(priority: .userInitiated) {
                try SessionBounceRenderer.bounceSession(
                    session,
                    padBuffers: buffers,
                    layout: layout,
                    gains: gains,
                    synthParams: synthParams,
                    songAudio: song,
                    includeOriginalSong: includeOriginalSong,
                    attestationAccepted: attested,
                    format: format,
                    outputDirectory: outputDir
                ).url
            }.value
            return url
        } catch {
            layerError = "Bounce session: \(error.localizedDescription)"
            return nil
        }
    }

    /// Decode + sum cached stem files into one canonical-rate stereo
    /// buffer (the bounce mixes it under the performance at frame 0).
    /// Wrong-rate/channel stems convert; unreadable files are
    /// skipped; nil when nothing decodes.
    private nonisolated static func stemMix(
        urls: [URL], sampleRate: Double
    ) -> AVAudioPCMBuffer? {
        guard let format = AVAudioFormat(
            standardFormatWithSampleRate: sampleRate, channels: 2
        ) else { return nil }
        var left: [Float] = []
        var right: [Float] = []
        for url in urls {
            guard let file = try? AVAudioFile(forReading: url),
                  file.length > 0,
                  let raw = AVAudioPCMBuffer(
                      pcmFormat: file.processingFormat,
                      frameCapacity: AVAudioFrameCount(file.length)
                  ),
                  (try? file.read(into: raw)) != nil,
                  let stereo = convertBuffer(raw, to: format),
                  let data = stereo.floatChannelData
            else { continue }
            let frames = Int(stereo.frameLength)
            guard frames > 0 else { continue }
            if frames > left.count {
                left.append(contentsOf: repeatElement(
                    0, count: frames - left.count))
                right.append(contentsOf: repeatElement(
                    0, count: frames - right.count))
            }
            let l = data[0]
            let r = stereo.format.channelCount > 1 ? data[1] : data[0]
            for i in 0..<frames {
                left[i] += l[i]
                right[i] += r[i]
            }
        }
        guard !left.isEmpty,
              let out = AVAudioPCMBuffer(
                  pcmFormat: format,
                  frameCapacity: AVAudioFrameCount(left.count)
              ),
              let outData = out.floatChannelData
        else { return nil }
        out.frameLength = AVAudioFrameCount(left.count)
        left.withUnsafeBufferPointer {
            outData[0].update(from: $0.baseAddress!, count: left.count)
        }
        right.withUnsafeBufferPointer {
            outData[1].update(from: $0.baseAddress!, count: right.count)
        }
        return out
    }

    /// One-shot AVAudioConverter pass (rate + channel-layout). Returns
    /// the input untouched when it's already in the target format.
    private nonisolated static func convertBuffer(
        _ buffer: AVAudioPCMBuffer, to format: AVAudioFormat
    ) -> AVAudioPCMBuffer? {
        if buffer.format.sampleRate == format.sampleRate,
           buffer.format.channelCount == format.channelCount,
           buffer.format.commonFormat == format.commonFormat {
            return buffer
        }
        guard let converter = AVAudioConverter(
            from: buffer.format, to: format
        ) else { return nil }
        let ratio = format.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(
            Double(buffer.frameLength) * ratio
        ) + 1024
        guard let out = AVAudioPCMBuffer(
            pcmFormat: format, frameCapacity: capacity
        ) else { return nil }
        var fed = false
        var convError: NSError?
        converter.convert(to: out, error: &convError) { _, status in
            if fed {
                status.pointee = .endOfStream
                return nil
            }
            fed = true
            status.pointee = .haveData
            return buffer
        }
        guard convError == nil, out.frameLength > 0 else { return nil }
        return out
    }

    // MARK: - Layer playback (legacy Phase 4 — read-only, D-015)

    /// Toggle a saved layer on/off for playback in the current
    /// session. Playback follows transport — starts on next play if
    /// already playing.
    public func toggleLayerPlayback(layerId: String) {
        guard let timeline = savedLayers.first(where: { $0.layerId == layerId }) else {
            return
        }
        togglePlayback(of: timeline, muteStems: false)
    }

    /// Toggle a saved *sketch* layer (sentinel `__sketch__`). Same
    /// transport-following semantics as `toggleLayerPlayback`, except
    /// sketches are song-less takes: when a song happens to be loaded
    /// its stems are silenced for the replay (play() would otherwise
    /// start them underneath). Stems do NOT auto-resume on toggle-off
    /// — the user re-owns the transport with a manual Play.
    public func toggleSketchLayerPlayback(layerId: String) {
        guard let timeline = savedSketchLayers.first(where: { $0.layerId == layerId }) else {
            return
        }
        togglePlayback(of: timeline, muteStems: currentBundle != nil)
    }

    private func togglePlayback(of timeline: LayerTimeline, muteStems: Bool) {
        let layerId = timeline.layerId
        if activePlaybackLayerIds.contains(layerId) {
            activePlaybackLayerIds.remove(layerId)
            layerPlayer.removeLayer(layerId: layerId)
            if activePlaybackLayerIds.isEmpty {
                layerPlayer.stop()
                // Symmetric ownership: if the transport was started
                // *by* toggling a layer on (not by the user hitting
                // Play), toggling the last one off should stop it
                // too. Without this the user has to hop to the Play
                // tab to silence a song they didn't ask to start —
                // which was the actual complaint. play()/pause()
                // clear the flag, so a manual play mid-session
                // hands ownership back to the user and this branch
                // becomes a no-op.
                if transportStartedByLayer && isPlaying {
                    pause()
                }
            }
        } else {
            activePlaybackLayerIds.insert(layerId)
            // Make sure every pack the take references is resident in
            // the scheduler so multi-pack recordings replay all their
            // pads — the user may be fronting a different carousel
            // page than any of the ones they recorded on.
            var replayPackIds = Set(
                timeline.events.compactMap { $0.params.packIdOverride }
            )
            if let base = timeline.activePackId {
                replayPackIds.insert(base)
            }
            for packId in replayPackIds {
                ensurePackLoaded(packId: packId)
            }
            // Rewind to the layer's first-event time before adding.
            // Without this the cursor is initialized past the last
            // event whenever the transport is already beyond the
            // recording (e.g. user played the song for a while, then
            // toggled a saved layer). That produced the "layer play
            // just plays the song, not the recording" symptom: the
            // tick loop was running but every event was already
            // behind the playhead, so nothing dispatched.
            //
            // Rewinding to the first-event time (or 0 for empty /
            // pre-song layers) matches DAW convention: enabling a
            // take makes you hear it from its start.
            let layerStart = timeline.events.first?.songTimeSec ?? 0
            seek(to: layerStart)
            layerPlayer.addLayer(timeline)
            if isPlaying {
                layerPlayer.start()
            } else {
                // Kick the transport into playback and mark the flag
                // so toggle-off can undo it symmetrically. play()
                // itself clears the flag, so set it *after*.
                play()
                transportStartedByLayer = true
            }
            // Sketch replay over a loaded song: silence the stems so
            // only the sketch is heard. The transport keeps running
            // (the LayerPlayer follows it); stems come back on the
            // next manual play().
            if muteStems {
                stemPlayer.pause()
            }
        }
    }

    /// Delete a saved layer from disk. Also drops it from the
    /// playback set.
    public func deleteLayer(layerId: String) {
        guard let bundle = currentBundle else { return }
        do {
            try layerStore.delete(analysisId: bundle.analysisId, layerId: layerId)
            savedLayers.removeAll { $0.layerId == layerId }
            if activePlaybackLayerIds.remove(layerId) != nil {
                layerPlayer.removeLayer(layerId: layerId)
            }
        } catch {
            layerError = "Delete layer: \(error.localizedDescription)"
        }
    }

    /// Rename a saved layer on disk + in the in-memory list.
    public func renameLayer(layerId: String, to newName: String) {
        guard let bundle = currentBundle else { return }
        do {
            try layerStore.rename(
                analysisId: bundle.analysisId,
                layerId: layerId,
                to: newName
            )
            savedLayers = layerStore.list(analysisId: bundle.analysisId)
        } catch {
            layerError = "Rename layer: \(error.localizedDescription)"
        }
    }

    /// Delete a saved sketch layer from disk + the playback set.
    public func deleteSketchLayer(layerId: String) {
        do {
            try layerStore.delete(
                analysisId: LayerStore.sketchAnalysisId, layerId: layerId
            )
            savedSketchLayers.removeAll { $0.layerId == layerId }
            if activePlaybackLayerIds.remove(layerId) != nil {
                layerPlayer.removeLayer(layerId: layerId)
            }
        } catch {
            layerError = "Delete sketch: \(error.localizedDescription)"
        }
    }

    /// Rename a saved sketch layer on disk + in the in-memory list.
    public func renameSketchLayer(layerId: String, to newName: String) {
        do {
            try layerStore.rename(
                analysisId: LayerStore.sketchAnalysisId,
                layerId: layerId,
                to: newName
            )
            savedSketchLayers = layerStore.list(
                analysisId: LayerStore.sketchAnalysisId
            )
        } catch {
            layerError = "Rename sketch: \(error.localizedDescription)"
        }
    }

    /// Push a saved layer to the backend at the current `backendBaseURL`.
    /// Idempotent — re-upload overwrites the stored file. On success
    /// the layer id is added to `uploadedLayerIds`; on failure the
    /// reason lands in `layerError`.
    public func uploadLayer(layerId: String) async {
        guard let timeline = savedLayers.first(where: { $0.layerId == layerId })
        else {
            layerError = "Layer no longer in library"
            return
        }
        uploadingLayerIds.insert(layerId)
        defer { uploadingLayerIds.remove(layerId) }
        do {
            _ = try await layerClient.upload(
                baseURL: backendBaseURL,
                timeline: timeline
            )
            uploadedLayerIds.insert(layerId)
        } catch {
            layerError = "Upload layer: \(error.localizedDescription)"
        }
    }

    /// Offline-render the layer to an AAC-encoded .m4a in the temp dir
    /// via `LayerOfflineRenderer`. Resolves EVERY pack the take
    /// references (`packIdOverride ?? activePackId` per event — the
    /// same rule replay uses), so multi-pack takes export all their
    /// hits. The current song's stem URLs ride along so DNA-pack
    /// chops (stem-slice pads) render too, and device-local pads
    /// (mic/vocoded recordings) resolve via the current mode's grid
    /// assignments — the same table live replay consults, so the
    /// export matches what replay plays today. (Including them in a
    /// local m4a doesn't touch `neverUpload`, which guards backend
    /// uploads of the raw samples.) Packs that can't be resolved
    /// anymore (deleted cache, another song's DNA pack, a cleared
    /// local assignment) degrade to skipped events, mirroring
    /// replay's padNotFound behavior. On success returns the rendered
    /// file URL so callers can present a share sheet. On failure
    /// returns nil and stores the reason in `layerError`.
    public func exportLayerToM4A(layerId: String) async -> URL? {
        guard let timeline = savedLayers.first(where: { $0.layerId == layerId })
        else {
            layerError = "Layer no longer in library"
            return nil
        }
        var referencedPackIds = Set(
            timeline.events.compactMap { $0.params.packIdOverride }
        )
        if let base = timeline.activePackId {
            referencedPackIds.insert(base)
        }
        let packs = referencedPackIds.compactMap { resolvedPack(forPackId: $0) }

        // Grid padIdx → WAV for local pads the take references.
        var localPadFiles: [Int: URL] = [:]
        if referencedPackIds.contains(SampleScheduler.localPackId) {
            let assignments = padAssignmentStore
                .assignments(for: modeCoordinator.appMode)
            for (gridRaw, slot) in assignments {
                guard case .localSample(let id) = slot.ref,
                      let url = try? padSampleStore.wavURL(id: id)
                else { continue }
                localPadFiles[gridRaw] = url
            }
        }

        guard !packs.isEmpty || !localPadFiles.isEmpty else {
            layerError = "None of this layer's sample packs are available"
            return nil
        }

        let safeName = timeline.name
            .replacingOccurrences(of: "/", with: "-")
            .replacingOccurrences(of: "\\", with: "-")
            .replacingOccurrences(of: ":", with: "-")
        let outputURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(safeName).m4a")
        // AVAudioFile refuses to overwrite an existing file — clear the
        // slot so re-exports work.
        try? FileManager.default.removeItem(at: outputURL)

        exportingLayerIds.insert(layerId)
        defer { exportingLayerIds.remove(layerId) }

        let stemFiles = currentStemLocalURLs
        do {
            let url = try await Task.detached(priority: .userInitiated) { [localPadFiles] in
                let renderer = LayerOfflineRenderer()
                let result = try renderer.render(
                    timeline: timeline,
                    packs: packs,
                    stemFiles: stemFiles,
                    localPadFiles: localPadFiles,
                    outputURL: outputURL
                )
                return result.url
            }.value
            return url
        } catch {
            layerError = "Export m4a: \(error.localizedDescription)"
            return nil
        }
    }

    /// Write the layer's JSON to a temp file suitable for `ShareLink`
    /// so the user can AirDrop/save/mail it. Returns nil if the layer
    /// isn't in the current library or if the write fails.
    public func exportLayerToTempFile(layerId: String) -> URL? {
        guard let timeline = savedLayers.first(where: { $0.layerId == layerId })
            ?? savedSketchLayers.first(where: { $0.layerId == layerId })
        else { return nil }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        do {
            let data = try encoder.encode(timeline)
            // Sanitize the layer name for filesystem use — keep it human-
            // readable but strip separators so we can't traverse.
            let safeName = timeline.name
                .replacingOccurrences(of: "/", with: "-")
                .replacingOccurrences(of: "\\", with: "-")
                .replacingOccurrences(of: ":", with: "-")
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("\(safeName).toneforge-layer.json")
            try data.write(to: url, options: [.atomic])
            return url
        } catch {
            layerError = "Export layer: \(error.localizedDescription)"
            return nil
        }
    }
}

// MARK: - SequencerPlayerDelegate (D-023)

/// Sounds the sequencer track types that don't route through the pad
/// bus: bundle chops (slices of the loaded song's stems), local samples,
/// and custom URLs. Each resolves to a file URL + optional slice window
/// and fires a gated one-shot through `SampleScheduler`. packPad tracks
/// never reach here — they publish `padDown/padUp` to the bus directly.
extension AppState: SequencerPlayerDelegate {

    public func sequencerPlayer(
        _ player: SequencerPlayer,
        playBundleChop presetKey: String,
        chopIndex: Int,
        velocity: Float,
        pan: Float
    ) {
        fireBundleChopOneShot(
            presetKey: presetKey, chopIndex: chopIndex,
            velocity: velocity, pan: pan
        )
    }

    public func sequencerPlayer(
        _ player: SequencerPlayer,
        playLocalSample id: UUID,
        velocity: Float,
        pan: Float
    ) {
        fireLocalSampleOneShot(id: id, velocity: velocity, pan: pan)
    }

    public func sequencerPlayer(
        _ player: SequencerPlayer,
        playURL url: URL,
        startSec: Double?,
        endSec: Double?,
        velocity: Float,
        pan: Float
    ) {
        fireURLOneShot(
            url: url, startSec: startSec, endSec: endSec,
            velocity: velocity, pan: pan
        )
    }

    public func sequencerPlayer(
        _ player: SequencerPlayer,
        playPackPad packId: String,
        padIdx: Int,
        velocity: Float,
        pan: Float
    ) {
        ensurePackLoaded(packId: packId)
        // triggerRaw bypasses the contribution guard — this is a
        // sequencer-driven trigger, not a live user contribution.
        _ = sampleScheduler.triggerRaw(padIdx: padIdx, packId: packId, pan: pan)
    }

    public func sequencerPlayer(
        _ player: SequencerPlayer,
        playSynthChord symbol: String,
        octaveShift: Int,
        velocity: Float
    ) {
        triggerSynthChord(symbol: symbol, octaveShift: octaveShift, velocity: velocity)
    }
}

// MARK: - Chop preview + one-shot firing

extension AppState {

    /// One-shot playback length (seconds) of a pack pad, so preview UI
    /// can revert its play/stop button when the sound finishes. Loads
    /// the pack if needed (preview already does this). Returns nil for
    /// looping pads or if the buffer can't be resolved.
    public func previewPadDurationSec(packId: String, padIdx: Int) -> Double? {
        ensurePackLoaded(packId: packId)
        return sampleScheduler.oneShotDurationSec(packId: packId, padIdx: padIdx)
    }

    /// Preview a chop reference using the same audio path as playback.
    /// Reliable while browsing (does not depend on grid assignment):
    /// packPad plays directly from the pack; other refs use the one-shot
    /// file engine. This is what the ChopPickerSheet ▶ buttons call.
    public func previewChopReference(_ ref: ChopReference, velocity: Float = 1.0) {
        switch ref {
        case .packPad(let packId, let padIdx):
            modeCoordinator.previewPadFromPack(packId: packId, padIdx: padIdx)
        case .bundleChop(let presetKey, let chopIndex, _):
            fireBundleChopOneShot(presetKey: presetKey, chopIndex: chopIndex, velocity: velocity)
        case .localSample(let id):
            fireLocalSampleOneShot(id: id, velocity: velocity)
        case .customURL(let url, let startSec, let endSec):
            fireURLOneShot(url: url, startSec: startSec, endSec: endSec, velocity: velocity)
        case .sequence:
            break
        case .synthChord(let symbol, let octaveShift):
            triggerSynthChord(symbol: symbol, octaveShift: octaveShift, velocity: velocity)
        }
    }

    /// Voice a chord symbol on the pad synth. `velocity` is 0–1
    /// (sequencer/preview convention); scaled to MIDI 0–127.
    fileprivate func triggerSynthChord(
        symbol: String, octaveShift: Int = 0, velocity: Float
    ) {
        let midis = ChordVoicing.midiNotes(symbol: symbol, octaveShift: octaveShift)
        guard !midis.isEmpty else { return }
        padSynth.triggerChord(midis: midis, velocity: velocity * 127)
    }

    fileprivate func fireBundleChopOneShot(
        presetKey: String, chopIndex: Int, velocity: Float, pan: Float = 0
    ) {
        guard let bundle = currentBundle,
              let preset = bundle.presets[presetKey],
              preset.chops.indices.contains(chopIndex),
              let stemURL = currentStemLocalURLs[preset.stem]
        else { return }
        let chop = preset.chops[chopIndex]
        sampleScheduler.triggerFileOneShot(
            url: stemURL,
            startSec: chop.startSec,
            endSec: chop.endSec,
            velocity: velocity,
            pan: pan,
            cacheKey: "chop:\(bundle.analysisId):\(presetKey):\(chopIndex)"
        )
    }

    fileprivate func fireLocalSampleOneShot(
        id: UUID, velocity: Float, pan: Float = 0
    ) {
        guard let url = try? padSampleStore.wavURL(id: id) else { return }
        sampleScheduler.triggerFileOneShot(
            url: url,
            startSec: nil,
            endSec: nil,
            velocity: velocity,
            pan: pan,
            cacheKey: "local:\(id.uuidString)"
        )
    }

    fileprivate func fireURLOneShot(
        url: URL, startSec: Double?, endSec: Double?,
        velocity: Float, pan: Float = 0
    ) {
        sampleScheduler.triggerFileOneShot(
            url: url,
            startSec: startSec,
            endSec: endSec,
            velocity: velocity,
            pan: pan,
            cacheKey: "url:\(url.absoluteString):\(startSec ?? 0):\(endSec ?? 0)"
        )
    }
}
