// LaunchpadController.swift
//
// Musical meaning above the LaunchpadTransport seam: maps a song's
// chops onto the 8x8 grid, quantizes pad presses against the bundle
// timeline, drives pad LEDs (solid = assigned, pulse = sounding) and
// fetches alternate (stem, sliceMode) chop sets via ChopsClient.
//
// Pure logic — no AVFoundation. The audio layer subscribes to
// `onTrigger` / `onRelease` and does the actual sample scheduling
// (ChopPlayer in JamDesktopAudio). The on-screen panel calls
// `padDown`/`padUp` directly; a hardware transport routes through the
// same methods via `attach(transport:)`.
//
// Grid mapping: chops are laid out row-major from the TOP-LEFT pad in
// ascending `idx` order (row 0 = top, matching LaunchpadPad), 64 max.

import Foundation
import Observation
import ToneForgeEngine

/// One pad's chop assignment: which slice, and which stem file it
/// reads from (chops are per-stem slices).
public struct PadAssignment: Equatable, Sendable {
    public let chop: Chop
    public let stem: String

    public init(chop: Chop, stem: String) {
        self.chop = chop
        self.stem = stem
    }
}

/// Seam for chop fetching so controller tests run offline.
public protocol LaunchpadChopsFetching: Sendable {
    func fetchChops(
        baseURL: URL, analysisId: String, stem: String?, sliceMode: String?
    ) async throws -> [Chop]
}

/// Default fetcher: GET /api/song/{id}/chops via ToneForgeEngine.
public struct BackendChopsFetcher: LaunchpadChopsFetching {
    private let client = ChopsClient()

    public init() {}

    public func fetchChops(
        baseURL: URL, analysisId: String, stem: String?, sliceMode: String?
    ) async throws -> [Chop] {
        try await client.fetchChops(
            baseURL: baseURL, analysisId: analysisId,
            stem: stem, sliceMode: sliceMode
        )
    }
}

@MainActor
@Observable
public final class LaunchpadController {

    /// Slice modes the backend serves (contribute_chops.py).
    public static let sliceModes = [
        "chord", "section", "beat", "phrase", "onset", "drum-bundle",
    ]

    /// How a pad plays back — the 3-way One-Shot | Follow | Latch contract
    /// shared with iOS (SampleTriggerMode, 28fec22e) and web (kit.js setMode).
    /// All three force the VOICE to loop (iOS loopOverride/f081d725: a one-shot
    /// that plays its full length can't be stopped on finger-lift); they differ
    /// in launch timing, START PHASE, and finger-lift.
    public enum PadPlaybackMode: String, CaseIterable, Sendable {
        /// One-Shot: finger-drumming GATE — fires the instant the pad is
        /// pressed, from the SAMPLE TOP (phase 0, NO lattice join), loops while
        /// held so a hold sustains, STOPS on finger-lift (quick tap = a short
        /// blip), and retriggers from the beginning every tap. Never quantizes,
        /// never rolls the clock or seeds the shared grid. iOS twin:
        /// SampleTriggerMode.oneShot under `forceInstantLaunch` + `forceZeroPhase`
        /// (28fec22e) — differs from Follow ONLY in the start phase.
        case oneShot
        /// Follow: fires instantly too — no quantize, no bar-wait, no armed
        /// hourglass, even while the transport rolls — but JOINS the shared
        /// lattice at its current phase (mid-body) so layered pads lock
        /// together; loops while held, STOPS on finger-lift. This is desktop's
        /// original zero-latency "Tap" (37f851d6/d56dc351/f081d725), renamed —
        /// behavior preserved. iOS twin: SampleTriggerMode.follow. The
        /// deliberate iOS/desktop deviation from web, which quantizes tap.
        case follow
        /// Latch: quantized TOGGLE loop — launch phase-locks to the shared
        /// lattice, the clip latches and keeps looping until a re-tap stops it
        /// (finger-lift is a no-op). The only mode that quantizes / drives the
        /// shared grid. iOS twin: SampleTriggerMode.latch.
        case latch

        public var title: String {
            switch self {
            case .oneShot: return "One-Shot"
            case .follow:  return "Follow"
            case .latch:   return "Latch"
            }
        }
        /// The VOICE loops in every mode (iOS `loopOverride == true` for all —
        /// f081d725): Latch so the clip repeats in sync; One-Shot/Follow so the
        /// momentary gate is a live, releasable, sustaining voice. NOTE this is
        /// the desktop analogue of iOS `loopOverride`, NOT iOS
        /// `SampleTriggerMode.loops` (which means "rolls the shared clock" —
        /// latch-only, expressed here by `quantizesLaunch`). Genuine one-shots
        /// (drum hits, played through the file path) still play their own
        /// length regardless of this.
        public var loops: Bool { true }
        /// Only Latch launches QUANTIZED + phase-locked to the shared lattice
        /// (and so drives the shared grid — the iOS `loops`/rolls-clock axis).
        /// One-Shot and Follow are zero-latency gates that always fire NOW.
        public var quantizesLaunch: Bool { self == .latch }
        /// One-Shot retriggers from the sample TOP (phase 0, no lattice join);
        /// Follow/Latch join the shared clock phase. iOS twin:
        /// SampleTriggerMode.startsFromZero (forceZeroPhase, 28fec22e).
        public var startsFromZero: Bool { self == .oneShot }
        /// Latch is the only TOGGLE (re-tap stops); One-Shot and Follow are
        /// momentary/hold gates whose finger-lift is driven explicitly by padUp.
        public var isToggle: Bool { self == .latch }

        /// Map a legacy persisted raw value (the retired tap|loop|latch set)
        /// onto the new One-Shot|Follow|Latch taxonomy, so a stored session or
        /// pad snapshot from before 28fec22e never decodes to an unknown mode.
        /// tap → follow (the synced instant gate they had); loop → follow (the
        /// closest survivor, a synced gate); latch → latch. Mirrors iOS
        /// SampleTriggerMode.migratedFromLegacy; default (new users) is Follow.
        public static func migratedFromLegacy(_ raw: String) -> PadPlaybackMode? {
            switch raw {
            case "tap", "loop": return .follow
            case "latch":       return .latch
            default:            return PadPlaybackMode(rawValue: raw)
            }
        }
    }

    /// Musical category of a pad — groups + colors the grid and drives Instant
    /// Groove (one best loop per category). Derived from the stem + Riley
    /// contentType, mirroring the backend kit categorization.
    public enum PadCategory: String, CaseIterable, Sendable {
        case drums = "DRUMS", bass = "BASS", chords = "CHORDS", lead = "LEAD"
        case vocal = "VOCAL", rhythm = "RHYTHM", texture = "TEXTURE"
        case fx = "FX", stab = "STAB", sample = "SAMPLE"

        /// 0xRRGGBB accent per category for the pad grid.
        public var colorHex: Int {
            switch self {
            case .drums: return 0xEF4444
            case .bass: return 0x22C55E
            case .chords: return 0xF59E0B
            case .lead: return 0xF97316
            case .vocal: return 0xEC4899
            case .rhythm: return 0x3B82F6
            case .texture: return 0x06B6D4
            case .fx: return 0xA855F7
            case .stab: return 0x8B5CF6
            case .sample: return 0x64748B
            }
        }
    }

    /// Category for a BORROW pad, which carries only a logical stem (no Riley
    /// contentType). Kept separate from `category(stem:contentType:)` because a
    /// borrow's "other" stem means chords/harmonic in kit terms, and because a
    /// hard-coded stem once made every borrow pad render one color (the "all
    /// pads red" bug — mount hard-coded stem "drums"). Pure + testable so that
    /// regression fails CI, not the user's eyes.
    public static func borrowCategory(forStem stem: String?) -> PadCategory {
        switch stem {
        case "drums":  return .drums
        case "bass":   return .bass
        case "vocals": return .vocal
        case "synth":  return .texture   // 6s `other` residual = synth proxy
        default:       return .chords   // "other"/unknown = chords/harmonic
        }
    }

    public static func category(stem: String, contentType: String?) -> PadCategory {
        switch stem {
        case "drums": return .drums
        case "bass": return .bass
        case "vocals": return .vocal
        case "synth": return .texture   // 6s `other` residual = synth proxy
        default: break
        }
        switch contentType {
        case "rhythm_loop": return .rhythm
        case "lead_loop": return .lead
        case "chord_loop": return .chords
        case "bass_groove": return .bass
        case "texture", "drone", "ambient": return .texture
        case "impact", "transition", "pickup", "ending": return .fx
        case "one_shot": return .stab
        default: return .sample
        }
    }

    public func category(for pad: LaunchpadPad) -> PadCategory? {
        guard let a = assignments[pad] else { return nil }
        return Self.category(stem: a.stem, contentType: a.chop.contentType)
    }

    /// Human label for a pad — the descriptive kit name ("Chorus Guitar riff"),
    /// else a chord symbol, else a positional fallback.
    public func padLabel(_ pad: LaunchpadPad) -> String {
        guard let a = assignments[pad] else { return "" }
        return a.chop.sectionLabel ?? a.chop.chordSymbol ?? "Pad \(pad.row * 8 + pad.col + 1)"
    }

    /// Instant Groove: start the single best-scoring loop in each core category
    /// (drums/bass/chords/lead/rhythm/texture) — all bar-synced via the Loop
    /// path, so one tap yields a locked, playable groove.
    public func instantGroove() {
        // Latch (not Loop): Instant Groove fires pads with no finger holding
        // them, so they must LATCH and stay looping (a hold-to-play Loop would
        // stop the moment nothing "holds" it), and a re-tap must toggle a
        // groove layer off.
        playbackMode = .latch
        let targets: [PadCategory] = [.drums, .bass, .chords, .lead, .rhythm, .texture]
        var best: [PadCategory: LaunchpadPad] = [:]
        var bestScore: [PadCategory: Double] = [:]
        for (pad, a) in assignments {
            let cat = Self.category(stem: a.stem, contentType: a.chop.contentType)
            let score = a.chop.performanceScore ?? a.chop.loopScore ?? 0
            if score > (bestScore[cat] ?? -1) {
                best[cat] = pad
                bestScore[cat] = score
            }
        }
        for cat in targets {
            if let pad = best[cat], !activePads.contains(pad) {
                padDown(pad)
            }
        }
    }

    // MARK: - Layer stack (one active loop per category)

    /// Assigned pads in a category, best (highest performanceScore) first — the
    /// swap menu for a layer row.
    public func pads(in category: PadCategory) -> [LaunchpadPad] {
        assignments.compactMap { (pad, a) -> (LaunchpadPad, Double)? in
            guard Self.category(stem: a.stem, contentType: a.chop.contentType) == category else { return nil }
            return (pad, a.chop.performanceScore ?? a.chop.loopScore ?? 0)
        }
        .sorted { $0.1 > $1.1 }
        .map { $0.0 }
    }

    /// The pad currently looping in a category (the active layer), if any.
    public func activeLayer(_ category: PadCategory) -> LaunchpadPad? {
        activePads.first { self.category(for: $0) == category }
    }

    /// Make `pad` the active loop for its category: stop whatever was looping in
    /// that category, start this one (looping, bar-synced).
    public func setLayer(_ pad: LaunchpadPad, category: PadCategory) {
        // Latch: layers are latched loops toggled by re-tap (setLayer/
        // clearLayer/toggleLayer all stop a layer via a second padDown, the
        // Latch toggle-off branch). A hold-to-play Loop would never stay up.
        playbackMode = .latch
        for p in activePads where self.category(for: p) == category && p != pad {
            padDown(p)   // toggle-off the previous layer
        }
        if !activePads.contains(pad) {
            padDown(pad) // start the new one
        }
    }

    /// Stop the active loop in a category (empty the layer).
    public func clearLayer(_ category: PadCategory) {
        for p in activePads where self.category(for: p) == category {
            padDown(p)
        }
    }

    /// Global stop: silence every sounding pad on the launchpad (all layers,
    /// loops, one-shots) and reset the grid to idle.
    public func stopAllPads() {
        for pad in Array(activePads) {
            if let a = assignments[pad] {
                onRelease?(pad, a)
                transport?.setLight(.solid(colorHint: colorHint(for: a)), at: pad)
            } else {
                transport?.setLight(.off, at: pad)
            }
        }
        // Belt-and-braces: also hard-stop every voice so a ring that outlived
        // its pad binding (e.g. orphaned by an earlier grid swap) is silenced
        // too — the "Stop won't kill it" bug.
        onStopAllVoices?()
        activePads.removeAll()
    }

    /// Toggle a layer: stop if active, else start the category's best pad.
    public func toggleLayer(_ category: PadCategory) {
        if let p = activeLayer(category) {
            padDown(p)
        } else if let p = pads(in: category).first {
            setLayer(p, category: category)
        }
    }

    // MARK: - Observable state

    public var quantize: QuantizeMode = .off

    /// Usage feedback: fired with (assetId, "play"|"skip") when a kit
    /// pad's outcome is known — the host batches these to the backend
    /// so future kits re-rank around what the user actually plays.
    public var onPadUsage: ((String, String) -> Void)?
    private var padUsageStart: [LaunchpadPad: Date] = [:]
    public var playbackMode: PadPlaybackMode = .follow
    /// Loop lock: when on, a triggered loop snaps to the next BAR of the
    /// shared lock lattice and the wait stays ≤ 1 bar. Off = the loop
    /// starts per the user's Quantize control (instant at `.off`).
    /// EITHER WAY the launch phase-JOINS the running cycle — the web
    /// engine anchors/joins every looping trigger, quantized or not
    /// (padengine.js:1166 anchor, :1269-1273 join), so this toggle is
    /// the QUANTIZE switch for loops, never the phase-join switch
    /// (supersedes the D-028 "lock toggle is the phase-lock switch"
    /// clause — see D-029).
    public var loopLockEnabled: Bool = true

    /// How many pads the merged Launchpad surface currently shows and
    /// mirrors to hardware: 16 (compact 4×4 — the first 16 pads, ideal
    /// for the 16-pad Auto Kit) or 64 (full 8×8). This is a DISPLAY +
    /// hardware-LED concern only: assignments for pads beyond the count
    /// are retained (hidden/dark), so toggling back to 64 restores the
    /// whole grid untouched. A physical press on an out-of-range pad is
    /// ignored, and any voice sounding in a now-hidden cell is silenced
    /// so a held loop can't ring on with no visible pad to stop it.
    public var padCount: Int = 64 {
        didSet {
            guard padCount != oldValue else { return }
            if padCount < oldValue { silenceOutOfRange() }
            // A mounted borrow RE-ARRANGES at the new capacity (16 =
            // best-of-both, 64 = full) rather than clipping the 64 view and
            // dropping the donor. Non-borrow grids just repaint.
            if borrowMounts != nil { applyBorrowLayout(at: padCount) }
            repaint()
        }
    }

    /// Whether `pad` is within the current `padCount` window (idx < count),
    /// i.e. visible on screen and lit on hardware. Pads are numbered
    /// row-major on the 8-wide grid, so the first 16 (idx 0–15) span the
    /// top two hardware rows and fill the compact 4×4 on screen.
    public func isPadVisible(_ pad: LaunchpadPad) -> Bool {
        pad.row * 8 + pad.col < padCount
    }

    /// Stop and clear any sounding pad that fell outside the pad-count
    /// window after a shrink (64 → 16). Chop loops are released through
    /// `onRelease`; one-shot pack/local samples play through harmlessly.
    private func silenceOutOfRange() {
        for pad in Array(activePads) where !isPadVisible(pad) {
            if let a = assignments[pad] { onRelease?(pad, a) }
            activePads.remove(pad)
        }
    }

    /// Target sample-loop length in seconds: the 8 s kit window snapped to a
    /// whole number of bars at the song tempo (so loops stay musical). The
    /// shared lock cycle uses this period. Falls back to 8 s with no tempo.
    public var loopLengthSeconds: Double {
        // Cycle = the longest analyzer loop actually on the grid: kit
        // windows are whole REAL bars now (e.g. 2 bars = 5.04 s), and the
        // old bars-fitting-8s formula queued presses to a 3-bar cycle no
        // pad plays — armed pads fired mid-cycle of the held loops.
        let fromChops = assignments.values
            .filter { $0.chop.loopScore != nil }
            .map { $0.chop.endSec - $0.chop.startSec }
            .max()
        if let cycle = fromChops, cycle > 0.5 { return cycle }
        guard let bpm = tempoBpm, bpm > 0 else { return 8.0 }
        let barSec = (60.0 / bpm) * 4.0
        let bars = max(1.0, (8.0 / barSec).rounded())
        return bars * barSec
    }

    /// Loop-lock quantize unit: ONE BAR at the song tempo, falling back to
    /// the full loop cycle only when the tempo is unknown (web
    /// padengine._lockLaunchTime parity). Snapping launches to whole
    /// multiples of the ~6–8 s cycle meant a pad tapped mid-cycle sat armed
    /// for seconds — it read as "the pad doesn't play". A single bar keeps
    /// the wait ≤ 1 bar while the phase-locked join (lockPhaseSeconds)
    /// supplies the intra-cycle position, so loops still land in unison.
    private var lockGridUnitSeconds: Double {
        guard let bpm = tempoBpm, bpm > 0 else { return loopLengthSeconds }
        return (60.0 / bpm) * 4.0
    }

    /// Next lock-grid boundary at/after `now` (multiples of
    /// `lockGridUnitSeconds` from song origin), with a grace window so a
    /// press just after a boundary fires NOW on that boundary instead of
    /// waiting a whole unit. Grace 0.08 s = web LOOP_LOCK_GRACE_SEC (was
    /// 0.12 — every other boundary on every surface uses 0.08).
    private func nextLoopBoundary(after now: Double) -> Double {
        let L = lockGridUnitSeconds
        guard L > 0 else { return now }
        let grace = 0.08
        let r = now.truncatingRemainder(dividingBy: L)
        if r < grace { return now }
        let k = (now / L).rounded(.down) + 1
        return k * L
    }

    /// Rolling-transport lock boundary: the next REAL bar of the song,
    /// not a synthetic lattice. Web twin: `_transportLaunchTime`
    /// (padengine.js:1014) — snap to the analyzer's downbeat grid;
    /// BEFORE grid[0] by more than a bar, extrapolate the grid BACKWARD
    /// at tempo (padengine.js:1023 — the analyzer's first downbeat can
    /// sit 50+ s into a long intro, and wait-for-grid[0] armed pads
    /// "forever"); PAST the last downbeat, extrapolate FORWARD at tempo
    /// (padengine.js:1047). All three live in the shared
    /// `Quantizer.nextQuantized` (`.bar`), the same routine the
    /// non-lock path already uses — iOS `nextLoopBoundary` twin. The
    /// old k·bar-from-song-0 lattice here was exactly the constant-
    /// tempo drift bug the web fixed: real first downbeats are not at
    /// t=0 and real tempo wobbles, so locked pads armed off the beat.
    /// No bar data at all (no downbeats AND no tempo) → the shared
    /// full-cycle lattice, web `_lockLaunchTime` fallback.
    private func rollingLoopBoundary(after now: Double) -> Double {
        let downbeats = timeline?.downbeats ?? []
        if !downbeats.isEmpty || (tempoBpm ?? 0) > 0 {
            return Quantizer.nextQuantized(
                songSeconds: now,
                mode: .bar,
                beats: timeline?.beats ?? [],
                downbeats: downbeats,
                sections: timeline?.sections ?? [],
                tempoBpm: tempoBpm
            )
        }
        return nextLoopBoundary(after: now)
    }

    /// Bar-floor for loop launches (iOS `SampleScheduler.loopQuantize`
    /// twin): sub-bar quantize (1/8, 1/4, 1/2) is promoted to `.bar`
    /// when the trigger will loop — a multi-bar loop beat-quantized
    /// starts on whatever beat the tap landed near, so its bar 1 sits
    /// mid-bar. `.off` passes through: an unquantized loop starts NOW
    /// and relies on the phase join for unison (web `quantized=false`
    /// launches, padengine.js:1143). `.phrase` is bar-aligned already.
    static func loopQuantize(
        _ mode: QuantizeMode, willLoop: Bool
    ) -> QuantizeMode {
        guard willLoop else { return mode }
        switch mode {
        case .eighth, .quarter, .half: return .bar
        case .off, .bar, .phrase: return mode
        }
    }
    public private(set) var assignments: [LaunchpadPad: PadAssignment] = [:]

    /// Peak envelope for a pad's chop, or nil. Set by SessionController
    /// (queries ChopPlayer's cached region buffers) — drives the
    /// on-pad waveforms, same look as mobile + the jamn Kit plugin.
    public var padPeaksProvider: ((LaunchpadPad) -> [Float]?)?
    /// Static step flags for a sequence pad (union of active steps),
    /// or nil for non-sequence pads. Set by SessionController from the
    /// pattern store — pads show their pattern even while idle.
    public var padStepFlagsProvider: ((Int) -> [Bool]?)?
    /// Pads currently sounding (pressed, or latched until release).
    public private(set) var activePads: Set<LaunchpadPad> = []
    /// Source of the current grid.
    public private(set) var stem: String?
    public private(set) var sliceMode: String?
    /// Bundle preset key the grid came from (nil for fetched
    /// stem/sliceMode grids). Chop edits are keyed on it.
    public private(set) var presetKey: String?
    /// Runtime chop-boundary overlay (ChopEditStore); applied to the
    /// raw chops via resolvedChops before layout.
    public private(set) var edits: ChopEdits?
    public private(set) var isFetching = false
    public var fetchError: String?

    // MARK: - Callbacks (audio layer)

    /// Fire the chop at `fireAtSongSeconds` (>= press time; equals it
    /// when quantize is off or within the grace window). The pad is
    /// included so the recording layer can capture grid coordinates.
    ///
    /// `lockPhaseSeconds` is the offset of this launch's PRE-shift
    /// quantize boundary from the lock-era anchor (the first loop
    /// launch's boundary), in the same domain as `fireAtSongSeconds`;
    /// 0 for one-shots / unlocked launches. The audio layer folds it
    /// mod the baked loop body so a loop tapped mid-jam JOINS at the
    /// running cycle position instead of restarting its body (web
    /// padengine phase-locked launch). It is deliberately the boundary,
    /// not the shifted start: measuring the per-pad onset shift back in
    /// as buffer offset would cancel the launch compensation and pads
    /// would flam by their shift deltas.
    @ObservationIgnored public var onTrigger: ((LaunchpadPad, PadAssignment, _ fireAtSongSeconds: Double, _ lockPhaseSeconds: Double) -> Void)?
    @ObservationIgnored public var onRelease: ((LaunchpadPad, PadAssignment) -> Void)?
    /// Normalized playhead (0..<1) for a pad that's currently hard-looping, else
    /// nil. Set by SessionController to query ChopPlayer; drives the on-pad
    /// playback ring in the UI.
    @ObservationIgnored public var loopProgressProvider: ((LaunchpadPad) -> Double?)?
    public func loopProgress(_ pad: LaunchpadPad) -> Double? { loopProgressProvider?(pad) }

    /// Fire a pack pad (packId, source pad index within pack).
    @ObservationIgnored public var onPackPadTrigger: ((String, Int) -> Void)?

    /// Fire a locally-recorded sample (vocoder / mic take) by its store id.
    @ObservationIgnored public var onLocalSampleTrigger: ((UUID) -> Void)?

    /// Fired just BEFORE a loop-mode trigger computes its quantize target.
    /// The host starts the song transport here if it's stopped — with a
    /// frozen clock every press "quantized" against a dead grid and fired
    /// immediately, so loops free-ran at their press phases (the "are you
    /// sure it's synced?" bug).
    @ObservationIgnored public var onLoopArm: (() -> Void)?
    /// Whether the song transport is rolling — quantize/phase-lock
    /// applies only then; stopped = pads fire immediately.
    @ObservationIgnored public var isTransportPlaying: (() -> Bool)?
    /// Whether ANY voice is currently audible or armed in the audio
    /// layer (ChopPlayer voices — loops, armed launches, AND one-shots
    /// still ringing after their pad left `activePads`). The free-run
    /// re-anchor must not fire while anything sounds: the web keeps
    /// `_lockAnchor` while `_voices.size > 0` (padengine.js:1154,
    /// one-shots included), so a ringing stab holds the lattice a loop
    /// is about to join. nil (tests / early boot) falls back to
    /// `activePads` alone.
    @ObservationIgnored public var isAnyVoiceSounding: (() -> Bool)?

    /// The surface is silent when no pad is latched/held AND the audio
    /// layer reports no live voice. `activePads` alone misses ringing
    /// one-shots (pack/local-sample pads leave it at padUp but play
    /// through); the provider alone would miss latched sequence pads
    /// between their transient hits.
    private var surfaceIsSilent: Bool {
        activePads.isEmpty && !(isAnyVoiceSounding?() ?? false)
    }
    /// Wall-clock anchor for the stopped-transport loop grid (host
    /// seconds of the first loop's launch; nil = no grid yet).
    @ObservationIgnored private var freerunAnchorHostSeconds: Double?
    /// Song-time anchor of the ROLLING lock era: the first phase-locked
    /// loop launch's pre-shift boundary. Later locked launches measure
    /// `lockPhaseSeconds = boundary − anchor` from it so they join the
    /// running cycle at the right body position (web `_lockAnchor`).
    /// Desktop keeps this SEPARATE from `freerunAnchorHostSeconds`
    /// because a stopped transport freezes song time — there is no
    /// common clock between the two modes (web's ctx clock always
    /// runs), so each mode anchors its own era. Cleared when a free-run
    /// re-anchor starts a fresh era.
    @ObservationIgnored private var lockAnchorSongSeconds: Double?
    /// Host wall clock in seconds — mach_absolute_time in production.
    /// Injectable (internal, `@testable`) so the free-run grid is
    /// deterministic under test; `nowProvider` can't stand in for it
    /// because it's SONG time, frozen while the transport is stopped.
    @ObservationIgnored var hostNowSeconds: () -> Double = {
        Double(mach_absolute_time()) * LaunchpadController.hostTickSeconds
    }
    /// mach_absolute_time ticks -> seconds.
    private static let hostTickSeconds: Double = {
        var info = mach_timebase_info_data_t()
        mach_timebase_info(&info)
        return Double(info.numer) / Double(info.denom) / 1_000_000_000
    }()

    /// Hard-stop EVERY sounding voice unconditionally (ChopPlayer.stopAll),
    /// not just the ones the current assignments can name. Used before a
    /// grid swap so a looping voice can't be orphaned — after `assignments`
    /// is replaced, `onRelease` would release the NEW chop's key and leave
    /// the old voice ringing with no pad able to stop it.
    @ObservationIgnored public var onStopAllVoices: (() -> Void)?

    // MARK: - Sequence Pad Support

    /// Manager for patterns running on pads (set by SessionController).
    @ObservationIgnored public weak var sequencePadManager: SequencePadManager?

    /// Custom pad assignments (sequences, local samples, etc.).
    @ObservationIgnored public weak var padAssignmentStore: PadAssignmentStore?

    /// Pulse state for sequence pads (for animation and LED feedback).
    public private(set) var sequencePulses: [Int: SequencePulse] = [:]

    // MARK: - Private

    @ObservationIgnored private let nowProvider: () -> Double
    @ObservationIgnored private let fetcher: any LaunchpadChopsFetching
    @ObservationIgnored private var transport: (any LaunchpadTransport)?
    @ObservationIgnored private var timeline: BundleTimeline?
    @ObservationIgnored private var tempoBpm: Double?
    @ObservationIgnored private var analysisId: String?
    /// Grid chops as delivered (pre-edit), so edits re-resolve from
    /// bundle truth instead of compounding.
    @ObservationIgnored private var rawChops: [Chop] = []

    /// Fallback pad color when a chop carries no colorHint (muted blue).
    private static let defaultColorHint: UInt32 = 0x2E6FB8

    public init(
        nowProvider: @escaping () -> Double,
        fetcher: any LaunchpadChopsFetching = BackendChopsFetcher()
    ) {
        self.nowProvider = nowProvider
        self.fetcher = fetcher
    }

    // MARK: - Wiring

    /// Route a hardware transport's pads through this controller and
    /// take over its LEDs.
    public func attach(transport: any LaunchpadTransport) {
        self.transport = transport
        transport.onPadDown = { [weak self] pad in self?.padDown(pad) }
        transport.onPadUp = { [weak self] pad in self?.padUp(pad) }
        repaint()
    }

    /// Adopt a song: timeline for the quantizer, default chop grid
    /// from the bundle's inline presets (preferring the harmonic /
    /// chord-sliced preset, matching the mobile default).
    public func configure(bundle: SongBundle) {
        timeline = bundle.timeline
        tempoBpm = bundle.meta.tempoBpm
        analysisId = bundle.analysisId
        activePads.removeAll()
        fetchError = nil

        let chosen: (key: String, preset: BundlePreset)? =
            bundle.presets["harmonic"].map { ("harmonic", $0) }
            ?? bundle.presets.first(where: { $0.value.sliceMode == "chord" })
                .map { ($0.key, $0.value) }
            ?? bundle.presets.sorted(by: { $0.key < $1.key }).first
                .map { ($0.key, $0.value) }
        if let chosen {
            setChops(
                chosen.preset.chops,
                stem: chosen.preset.stem,
                sliceMode: chosen.preset.sliceMode
            )
            presetKey = chosen.key
        } else {
            setChops([], stem: nil, sliceMode: nil)
        }
    }

    /// Clear everything (song unloaded).
    public func reset() {
        timeline = nil
        tempoBpm = nil
        analysisId = nil
        setChops([], stem: nil, sliceMode: nil)
    }

    // MARK: - Grid

    /// Lay `chops` onto the grid row-major from the top-left in
    /// ascending idx order; 64 max. Resets any edit overlay (a new
    /// grid means new chop identities — the caller re-applies).
    public func setChops(_ chops: [Chop], stem: String?, sliceMode: String?) {
        self.stem = stem
        self.sliceMode = sliceMode
        presetKey = nil
        edits = nil
        rawChops = chops.sorted(by: { $0.idx < $1.idx })
        activePads.removeAll()
        layout()
    }

    /// Overlay chop-boundary edits (ChopEditStore) on the current
    /// grid. Pass nil to restore bundle boundaries. Re-resolves from
    /// the raw chops each time, so edits never compound.
    public func applyEdits(_ edits: ChopEdits?) {
        self.edits = edits
        activePads.removeAll()
        layout()
    }

    /// Grid chops in display order: idx-sorted raw chops, or the
    /// startSec-sorted resolved set when edits overlay them.
    private var displayChops: [Chop] {
        if let edits, edits.hasEdits {
            return resolvedChops(bundleChops: rawChops, edits: edits)
                .map { $0.toChop() }
        }
        return rawChops
    }

    private func layout() {
        // Any grid swap replaces `assignments`; kill sounding voices first so
        // a held loop can't be orphaned into an unstoppable ring (callers
        // already cleared activePads).
        onStopAllVoices?()
        var next: [LaunchpadPad: PadAssignment] = [:]
        if let stem {
            for (slot, chop) in displayChops.prefix(64).enumerated() {
                let pad = LaunchpadPad(row: slot / 8, col: slot % 8)
                next[pad] = PadAssignment(chop: chop, stem: stem)
            }
        }
        assignments = next
        borrowSourceLabels = [:]   // a fresh single-song grid drops borrow labels
        borrowMounts = nil         // …and the toggle stops re-arranging a borrow
        repaint()
    }

    /// Adopt a MULTI-STEM assignment set (Performance-Intelligence auto-kit):
    /// each pad is a (chop, stem) pair spanning different stems, laid out
    /// row-major. Unlike `loadChops` (one stem), this drives the grid from a
    /// pre-built kit — the chops carry their own stem + loop `kind`.
    public func adoptAssignments(_ pairs: [(chop: Chop, stem: String)]) {
        // Kill every sounding voice BEFORE the assignment map changes — a
        // looping pad would otherwise keep ringing with no pad able to stop
        // it once its slot points at a different chop (orphaned voice).
        onStopAllVoices?()
        activePads.removeAll()
        var next: [LaunchpadPad: PadAssignment] = [:]
        for (slot, pair) in pairs.prefix(64).enumerated() {
            let pad = LaunchpadPad(row: slot / 8, col: slot % 8)
            next[pad] = PadAssignment(chop: pair.chop, stem: pair.stem)
        }
        assignments = next
        borrowSourceLabels = [:]
        borrowMounts = nil
        repaint()
    }

    /// One borrow pad ready to mount: its (chop, stem), the source-song label
    /// shown on the tile ("This song" / the donor's name), and which song it
    /// came from. The GRID SLOT is NOT pre-baked — the controller lays these out
    /// capacity-aware (`arrangeBorrowLayout`) so the 16/64 toggle can
    /// re-arrange (16 = best-of-both, 64 = full) instead of clipping.
    public struct BorrowMount: Sendable {
        public let chop: Chop
        public let stem: String
        public let sourceLabel: String
        public let source: BorrowPadSource
        public init(
            chop: Chop, stem: String, sourceLabel: String, source: BorrowPadSource
        ) {
            self.chop = chop
            self.stem = stem
            self.sourceLabel = sourceLabel
            self.source = source
        }
        /// Playable ranking used to pick the best pads for the compact (16)
        /// grid: performanceScore ?? loopScore ?? 0. Ignored at 64.
        var score: Double { chop.performanceScore ?? chop.loopScore ?? 0 }
    }

    /// Per-pad source-song label for a borrow grid (set only by
    /// `adoptBorrowAssignments`; cleared by any other grid swap). Drives the
    /// small blue(this song)/amber(donor) source line — web parity with
    /// kit.js `.kit-pad-source`.
    public private(set) var borrowSourceLabels: [LaunchpadPad: String] = [:]

    /// Source-song label for a pad, or nil (non-borrow grid).
    public func sourceLabel(for pad: LaunchpadPad) -> String? {
        borrowSourceLabels[pad]
    }

    /// The full set of borrow pads currently mounted (both songs), retained so
    /// the 16/64 toggle can RE-ARRANGE the grid (`applyBorrowLayout`) instead of
    /// clipping. nil = no borrow active (cleared by any single-song grid swap).
    private var borrowMounts: [BorrowMount]?

    /// Adopt a BORROW grid: BOTH songs' loops, each carrying a source-song
    /// label and source tag. The controller lays them out capacity-aware via
    /// `arrangeBorrowLayout`, so the mount list is the FULL set (every pad) and
    /// the grid slot is derived, not pre-baked. A borrow opens on the full 64
    /// grid (current-on-top / divider / donor-below); the 16/64 toggle then
    /// re-arranges (16 = best-of-both) rather than hiding the donor.
    public func adoptBorrowAssignments(_ mounts: [BorrowMount]) {
        borrowMounts = mounts
        // Borrow opens on the full grid. Setting padCount fires the didSet,
        // which applies the layout; if already 64 the didSet is a no-op, so lay
        // it out explicitly here.
        if padCount == 64 {
            applyBorrowLayout(at: 64)
        } else {
            padCount = 64            // didSet → applyBorrowLayout(at: 64)
        }
        repaint()
    }

    /// (Re)lay the retained borrow mounts onto the grid at `capacity` (16 or
    /// 64) and repaint. Shared by `adoptBorrowAssignments` (first mount) and the
    /// `padCount` toggle. 64 = full (initial top / blank divider / donor below);
    /// 16 = best-of-both (top 8 of each song). Preserves each pad's source-song
    /// label and source tint across the re-arrangement.
    private func applyBorrowLayout(at capacity: Int) {
        guard let mounts = borrowMounts else { return }
        // Kill every sounding voice BEFORE the assignment map changes — a
        // looping pad would otherwise ring on with no pad able to stop it.
        onStopAllVoices?()
        activePads.removeAll()
        let cols = capacity == 16 ? 4 : 8
        let rows = capacity == 16 ? 4 : 8
        let refs = mounts.map {
            BorrowPadRef(padIdx: $0.chop.idx, source: $0.source, score: $0.score)
        }
        let layout = arrangeBorrowLayout(refs, cols: cols, rows: rows)
        var next: [LaunchpadPad: PadAssignment] = [:]
        var labels: [LaunchpadPad: String] = [:]
        for pl in layout.placements where (0..<64).contains(pl.gridSlot) {
            let mount = mounts[pl.inputIndex]
            let pad = LaunchpadPad(row: pl.gridSlot / 8, col: pl.gridSlot % 8)
            next[pad] = PadAssignment(chop: mount.chop, stem: mount.stem)
            labels[pad] = mount.sourceLabel
        }
        assignments = next
        borrowSourceLabels = labels
    }

    /// Fetch and adopt a different (stem, sliceMode) chop set.
    public func loadChops(
        stem: String, sliceMode: String, backend: URL
    ) async {
        guard let analysisId else { return }
        isFetching = true
        fetchError = nil
        defer { isFetching = false }
        do {
            let chops = try await fetcher.fetchChops(
                baseURL: backend, analysisId: analysisId,
                stem: stem, sliceMode: sliceMode
            )
            setChops(chops, stem: stem, sliceMode: sliceMode)
        } catch {
            fetchError = error.localizedDescription
        }
    }

    // MARK: - Pads

    public func padDown(_ pad: LaunchpadPad) {
        // Out-of-range in the current 16/64 window: a hardware press on a
        // dark pad (or a stale on-screen tap during a shrink) is a no-op,
        // so the compact view never triggers a hidden cell.
        guard isPadVisible(pad) else { return }
        // Check for custom pad assignment first
        let padIdx = pad.row * 8 + pad.col
        if let store = padAssignmentStore, let ref = store.slot(padIdx: padIdx) {
            switch ref {
            case .sequence(let patternId):
                handleSequencePadDown(patternId: patternId, padIdx: padIdx, pad: pad)
                return
            case .packPad(let packId, let sourcePadIdx):
                onPackPadTrigger?(packId, sourcePadIdx)
                activePads.insert(pad)
                transport?.setLight(.pulse(colorHint: 0xA855F7), at: pad)
                return
            case .localSample(let id):
                onLocalSampleTrigger?(id)
                activePads.insert(pad)
                transport?.setLight(.pulse(colorHint: 0x9B4DFF), at: pad)  // vocoded purple
                return
            }
        }

        // Normal chop trigger.
        guard let assignment = assignments[pad] else { return }
        // Only LATCH is a toggle: re-tapping a currently-latched pad stops it.
        // One-Shot and Follow are momentary/hold gates driven by padUp — a
        // re-tap there starts a FRESH voice (iOS parity: the gates use .hold,
        // so they never hit the toggle-off branch), so the guard is Latch-only.
        if playbackMode.isToggle && activePads.contains(pad) {
            activePads.remove(pad)
            transport?.setLight(.solid(colorHint: colorHint(for: assignment)), at: pad)
            // Usage feedback: held >= 3 s = play, killed sooner = skip.
            if let assetId = assignment.chop.assetId,
               let started = padUsageStart.removeValue(forKey: pad) {
                let heard = Date().timeIntervalSince(started)
                onPadUsage?(assetId, heard < 3.0 ? "skip" : "play")
            }
            onRelease?(pad, assignment)   // stop the loop
            return
        }
        let now = nowProvider()
        // Transport STOPPED: loops fire immediately and free-run
        // (mobile parity). Auto-starting the transport for a clock
        // made the first loop tap visibly toggle Play and start the
        // whole song underneath — jarring, and not what a pad tap
        // means. Phase-locking applies only when the song is
        // actually rolling.
        let transportRolling = isTransportPlaying?() ?? false
        // The voice loops in every mode (all three force loop, iOS f081d725);
        // `instant` (One-Shot + Follow) is the zero-latency gate that fires NOW
        // regardless of quantize, lock or the transport, and never seeds/
        // advances the grid. `startsFromZero` (One-Shot only) then starts the
        // voice at the sample TOP instead of phase-joining the shared lattice.
        let willLoop = playbackMode.loops
        let instant = playbackMode != .latch     // One-Shot + Follow fire now
        let startsFromZero = playbackMode.startsFromZero  // One-Shot: from the top
        // Loop + lock: start on the next BAR of the shared lock lattice, so
        // pads stack coherently and a tap waits ≤ 1 bar. Lock off keeps the
        // user's Quantize control (sub-bar floored to .bar; .off = NOW).
        // EVERY looping launch joins the running cycle at its boundary's
        // phase — quantized or not (web padengine.js:1166/:1269-1273; the
        // pre-D-029 join-only-when-locked gate restarted an unquantized
        // loop's bar 1 against the mix).
        let fireAt: Double
        var lockPhaseSeconds = 0.0
        if instant {
            // ONE-SHOT + FOLLOW — the zero-latency gate: ALWAYS fire NOW (no
            // quantize, no loop-lock, no arm/hourglass) whether the transport
            // is stopped OR rolling, and NEVER seed or advance the shared grid
            // (a gate must not move the lattice the Latch pads share). The
            // voice still loops (willLoop) so a hold sustains and finger-lift
            // stops it. The two gates differ ONLY in START PHASE:
            //   • FOLLOW joins the current era's cycle so a rolling gate over
            //     running loops lands mid-body in unison — the willLoop-gated
            //     phase-join iOS keeps under forceInstantLaunch. No active era
            //     → phase 0, so a lone Follow plays from the body start.
            //   • ONE-SHOT (startsFromZero) skips the join entirely and starts
            //     at the SAMPLE TOP (phase 0) every tap — the finger-drumming
            //     retrigger. iOS twin: forceZeroPhase (28fec22e).
            fireAt = now
            if !startsFromZero {
                if transportRolling {
                    if let anchor = lockAnchorSongSeconds {
                        lockPhaseSeconds = fireAt - anchor
                    }
                } else if let anchor = freerunAnchorHostSeconds {
                    lockPhaseSeconds = hostNowSeconds() - anchor
                }
            }
        } else if !transportRolling {
            // FREE-RUN GRID: the first loop fires immediately and
            // anchors a wall-clock BAR grid (lockGridUnitSeconds — a
            // single bar, so a tap waits ≤ 1 bar, never the full ~6–8 s
            // cycle); later loop taps queue to that anchor so they
            // stack in phase — the queuing feel, without auto-starting
            // the transport. (Tap is handled by the instant branch above; a
            // lock-off loop is instant too but still measures its join phase
            // from the free-run era — boundary = now, web unquantized launch.)
            if willLoop && loopLockEnabled {
                let hostNow = hostNowSeconds()
                // Re-anchor whenever nothing is SOUNDING: releasing all
                // pads abandons the free-run grid, and the next press
                // fires immediately on a fresh cycle — BY DESIGN (a new
                // jam shouldn't wait on a grid nobody can hear). A
                // still-audible one-shot HOLDS the anchor
                // (surfaceIsSilent consults the audio layer, web
                // `_voices.size` at padengine.js:1154/1172).
                if surfaceIsSilent || freerunAnchorHostSeconds == nil {
                    freerunAnchorHostSeconds = hostNow
                    lockAnchorSongSeconds = nil   // fresh lock era
                    fireAt = now
                } else {
                    let L = lockGridUnitSeconds
                    let elapsed = hostNow - (freerunAnchorHostSeconds ?? hostNow)
                    let intoCycle = elapsed.truncatingRemainder(dividingBy: L)
                    // Small grace: a tap RIGHT on the boundary fires now.
                    let onBoundary = intoCycle < 0.08
                    fireAt = now + (onBoundary ? 0 : L - intoCycle)
                    // Boundary's wall offset from the free-run anchor —
                    // the phase this loop joins its body at. Song time is
                    // frozen here, so it CANNOT come from fireAt deltas
                    // (those collapse the elapsed host time).
                    lockPhaseSeconds = (elapsed - intoCycle) + (onBoundary ? 0 : L)
                }
            } else if willLoop {
                // Lock OFF, stopped: instant start, mid-body join on the
                // free-run era (boundary = now). No stale-anchor clear
                // here — the web clears only on the QUANTIZED path
                // (inside `if (quantized)`, padengine.js:1172).
                let hostNow = hostNowSeconds()
                fireAt = now
                if let anchor = freerunAnchorHostSeconds {
                    lockPhaseSeconds = hostNow - anchor
                } else {
                    freerunAnchorHostSeconds = hostNow
                    lockAnchorSongSeconds = nil   // fresh lock era
                }
            } else {
                fireAt = now
            }
        } else if willLoop && loopLockEnabled {
            // Rolling lock: the song's REAL bar grid, extrapolated at
            // tempo beyond either end (rollingLoopBoundary).
            fireAt = rollingLoopBoundary(after: now)
            if let anchor = lockAnchorSongSeconds {
                lockPhaseSeconds = fireAt - anchor
            } else {
                // Anchor the era at the PRE-shift boundary (never the
                // shifted start — that would bake the first pad's onset
                // shift into the grid and skew every later join by it).
                lockAnchorSongSeconds = fireAt
            }
        } else {
            // Rolling, lock off (Loop/Latch — Tap took the instant branch):
            // the user's Quantize control, with loop launches floored to the
            // BAR grid (loopQuantize — sub-bar loop starts land mid-bar, the
            // "queued pads start at random times" bug; `.off` loops start NOW).
            fireAt = Quantizer.nextQuantized(
                songSeconds: now,
                mode: Self.loopQuantize(quantize, willLoop: willLoop),
                beats: timeline?.beats ?? [],
                downbeats: timeline?.downbeats ?? [],
                sections: timeline?.sections ?? [],
                tempoBpm: tempoBpm
            )
            // Lock-off loops still JOIN the rolling era's cycle at their
            // boundary (= the quantized fire time, or now when unquantized).
            if willLoop {
                if let anchor = lockAnchorSongSeconds {
                    lockPhaseSeconds = fireAt - anchor
                } else {
                    lockAnchorSongSeconds = fireAt
                }
            }
        }
        activePads.insert(pad)
        // Usage feedback: a LATCHED loop judges play/skip at its toggle-off
        // (held ≥ 3 s = play); the One-Shot and Follow gates release on padUp,
        // so their trigger firing IS the play signal.
        if let assetId = assignment.chop.assetId {
            if playbackMode.isToggle {
                padUsageStart[pad] = Date()
            } else {
                onPadUsage?(assetId, "play")
            }
        }
        transport?.setLight(.pulse(colorHint: colorHint(for: assignment)), at: pad)
        onTrigger?(pad, assignment, fireAt, lockPhaseSeconds)
    }

    // MARK: - Arrangement replay (hands-free)

    /// Arm a pad as a phase-locked loop for arrangement replay, regardless of
    /// the user's tap/loop mode — the web twin is kit.js `armPadForReplay`
    /// (engine.trigger looped + quantized). Mirrors padDown's loop-arm branch
    /// but skips the free-run re-anchor / usage bookkeeping so replay is
    /// deterministic. No-op if the pad is out of the current window, unmounted,
    /// or already sounding.
    public func replayArm(_ index: Int) {
        let pad = LaunchpadPad(row: index / 8, col: index % 8)
        guard isPadVisible(pad), let assignment = assignments[pad],
              !activePads.contains(pad) else { return }
        let now = nowProvider()
        let transportRolling = isTransportPlaying?() ?? false
        // Phase-lock to the shared loop grid while the song rolls; fire now when
        // stopped so a hands-free replay still sounds on a static transport.
        let fireAt: Double
        var lockPhaseSeconds = 0.0
        if transportRolling && loopLockEnabled {
            // Same real-bar boundary as padDown (rollingLoopBoundary): a
            // replayed loop must land on the grid the live pads use.
            fireAt = rollingLoopBoundary(after: now)
            // Same era anchoring as padDown: replayed loops join the running
            // cycle at the boundary's phase, not restart their body.
            if let anchor = lockAnchorSongSeconds {
                lockPhaseSeconds = fireAt - anchor
            } else {
                lockAnchorSongSeconds = fireAt
            }
        } else {
            fireAt = now
        }
        activePads.insert(pad)
        transport?.setLight(.pulse(colorHint: colorHint(for: assignment)), at: pad)
        onTrigger?(pad, assignment, fireAt, lockPhaseSeconds)
    }

    /// Release an arrangement-replay pad (kit.js `releasePadForReplay`).
    public func replayRelease(_ index: Int) {
        let pad = LaunchpadPad(row: index / 8, col: index % 8)
        guard let assignment = assignments[pad], activePads.contains(pad) else { return }
        activePads.remove(pad)
        transport?.setLight(.solid(colorHint: colorHint(for: assignment)), at: pad)
        onRelease?(pad, assignment)
    }

    public func padUp(_ pad: LaunchpadPad) {
        // Check for custom pad assignment first
        let padIdx = pad.row * 8 + pad.col
        if let store = padAssignmentStore, let ref = store.slot(padIdx: padIdx) {
            switch ref {
            case .sequence:
                // Sequence pads use toggle mode by default, no action on up
                return
            case .packPad:
                // Pack pads are one-shot, just clear active state
                activePads.remove(pad)
                transport?.setLight(.solid(colorHint: 0xA855F7), at: pad)
                return
            case .localSample:
                // One-shot (plays through); just clear active state.
                activePads.remove(pad)
                transport?.setLight(.solid(colorHint: 0x9B4DFF), at: pad)
                return
            }
        }

        // Normal chop release. One-Shot AND Follow are momentary/HOLD gates —
        // they sound only while held, so finger-lift STOPS the voice NOW (and
        // restores any taken-over stem). They differ only in START phase, never
        // on release. Latch keeps looping until re-tapped, so padUp is a no-op
        // for it (the re-tap in padDown toggles it off). iOS parity:
        // jamPadUpAction → .immediate for oneShot & follow, .none for latch
        // (28fec22e/e8566e69/f081d725/d56dc351).
        guard let assignment = assignments[pad] else { return }
        if !playbackMode.isToggle {
            activePads.remove(pad)
            onRelease?(pad, assignment)   // stop the momentary/gate voice NOW
            transport?.setLight(.solid(colorHint: colorHint(for: assignment)), at: pad)
        }
    }

    // MARK: - Sequence Pad Handling

    /// Toggle a sequence pattern on a pad (tap to start, tap to stop).
    private func handleSequencePadDown(patternId: UUID, padIdx: Int, pad: LaunchpadPad) {
        guard let manager = sequencePadManager else { return }
        let bpm = tempoBpm ?? 120
        manager.toggle(patternId: patternId, padIdx: padIdx, songBPM: bpm)
    }

    /// Update pulse state for a sequence pad (called by SequencePadManager).
    public func updateSequencePulse(padIdx: Int, pulse: SequencePulse?) {
        if let pulse {
            sequencePulses[padIdx] = pulse
        } else {
            sequencePulses.removeValue(forKey: padIdx)
        }
        // Update hardware LED
        let pad = LaunchpadPad(row: padIdx / 8, col: padIdx % 8)
        if let pulse {
            // Pulse LED based on step
            let color: UInt32 = pulse.isDownbeat ? 0xFFFFFF : 0x9B59B6  // white flash / purple
            transport?.setLight(.solid(colorHint: color), at: pad)
        } else if padAssignmentStore?.slot(padIdx: padIdx) != nil {
            // Assigned but not playing: dim purple
            transport?.setLight(.solid(colorHint: 0x5B2C6F), at: pad)
        } else {
            transport?.setLight(.off, at: pad)
        }
    }

    // MARK: - Edit Mode gate

    /// Launchpad Edit Mode (iOS `holdRadialEnabled` twin, mobile commit
    /// 602a9043): whether the pad surface arms its EDIT affordance for a
    /// pad — the right-click radial + hover "⋯" hint on macOS, the
    /// hold-radial on iOS. Edit OFF (the default) = a filled pad is a
    /// pure performance surface with NO edit gesture armed. An EMPTY pad
    /// keeps its Add Sound affordance in both modes — it is not a
    /// performance path. Pure + in Core so the gate is pinned by tests,
    /// not the user's eyes.
    public static func editAffordanceEnabled(
        editing: Bool, hasContent: Bool
    ) -> Bool {
        editing || !hasContent
    }

    // MARK: - Lights

    /// The pad's display color, shared by the hardware LEDs and the
    /// on-screen mirror.
    public func colorHint(for assignment: PadAssignment) -> UInt32 {
        Self.parseColorHint(assignment.chop.colorHint) ?? Self.defaultColorHint
    }

    /// "#RRGGBB" / "RRGGBB" → 0xRRGGBB.
    static func parseColorHint(_ hint: String?) -> UInt32? {
        guard var hex = hint?.trimmingCharacters(in: .whitespaces), !hex.isEmpty
        else { return nil }
        if hex.hasPrefix("#") { hex.removeFirst() }
        guard hex.count == 6, let value = UInt32(hex, radix: 16) else { return nil }
        return value
    }

    private func repaint() {
        guard let transport else { return }
        var frame: [LaunchpadPad: LaunchpadLight] = [:]
        for row in 0..<8 {
            for col in 0..<8 {
                let pad = LaunchpadPad(row: row, col: col)
                // Dark every pad outside the current 16/64 window so the
                // hardware LEDs mirror exactly what the on-screen grid shows.
                if !isPadVisible(pad) {
                    frame[pad] = .off
                    continue
                }
                if let assignment = assignments[pad] {
                    let hint = colorHint(for: assignment)
                    frame[pad] = activePads.contains(pad)
                        ? .pulse(colorHint: hint)
                        : .solid(colorHint: hint)
                } else {
                    frame[pad] = .off
                }
            }
        }
        transport.setLights(frame)
    }
}
