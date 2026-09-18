// SampleScheduler.swift
//
// Ties every user-visible sample control (pad tap, quantize menu,
// hold/toggle, section gate, layer record) to the audio subsystem.
// This is where song-time — the transport-clock domain the whole app
// thinks in — is translated into AVAudioTime, the domain the audio
// graph schedules against.
//
// Trigger flow (Contribute → Samples pad tap):
//
//   PadGrid → scheduler.trigger(padIdx:)
//     ├─ SectionResolver.isAllowed?  no → drop silently, emit .gated
//     ├─ Quantizer.nextQuantized → targetSongSeconds
//     ├─ target - transport.now → delaySeconds
//     ├─ hostTime = mach_absolute_time() + delaySeconds
//     ├─ pool.trigger(SampleTrigger, buffer, at: AVAudioTime(hostTime:))
//     └─ (optional) recorder.append(LayerEvent(.sampleOn, songTime: target))
//
// Hold vs toggle:
//   .hold   → touch-down triggers; touch-up releases.
//   .toggle → first touch-down starts (looped if pad has loop point);
//             second touch-down releases. Touch-ups are ignored.
//
// Buffer preload:
//   On `setActivePack`, every file-backed pad is loaded into an
//   AVAudioPCMBuffer eagerly. Song-derived pads read their stem slice
//   into a buffer at the same time. This is the ~50 MB budget the
//   plan calls out — 16 pads × ~1 s @ 44 kHz stereo Float32 ≈ 5 MB
//   for the StarterPack, headroom for larger song-derived packs.
//
// This scheduler is one of the plan's Critical Files — the song-time
// → AVAudioTime conversion + section gate + hold/toggle bookkeeping
// live here in a single place so schema/timing risk is contained.

import Foundation
import ToneForgeEngine
#if canImport(AVFoundation)
// @preconcurrency: AVFAudio types (AVAudioPCMBuffer) predate Sendable;
// the converter input block below captures a buffer that is only read
// synchronously — safe, but the SDK annotation can't express that.
@preconcurrency import AVFoundation
#endif

@MainActor
public final class SampleScheduler: ObservableObject {

    /// Result of a `trigger(padIdx:)` call. Consumed by the UI layer
    /// so it can flash the pad, show a "gated" hint, or paint the pad
    /// as latched (toggle-on).
    public enum TriggerResult: Equatable {
        /// Scheduled to fire at the given song time. Includes hold-
        /// mode taps (targetSongTime ≈ now) and quantized taps
        /// (targetSongTime > now).
        case scheduled(atSongTime: Double)
        /// Toggle-mode second tap. The pad's previous voice was
        /// released with the release fade.
        case toggledOff
        /// Section gate rejected the trigger. Silent.
        case gated
        /// Pack has no pad at that index, or no buffer preloaded.
        case padNotFound
    }

    /// Live user settings mirrored from SampleSettingsStore.
    @Published public var quantize: QuantizeMode = .off
    /// Loop lock: when on, a looping pad starts on the next BAR boundary
    /// and joins `phase` seconds into its body, so every loop phase-locks
    /// to the shared cycle and layers coherently while waiting ≤ 1 bar
    /// (not the whole multi-bar cycle) to sound. Off = normal quantize
    /// (instant when quantize is .off), no phase join.
    @Published public var loopLock: Bool = true
    @Published public var holdMode: HoldMode = .hold
    /// Session-view latch: force the next trigger(s) to loop their
    /// buffer regardless of loop points / transforms. Set (and
    /// restored) around Jam-sample latch launches — song chops have no
    /// loopPointSec, but a latched clip must ring continuously until
    /// toggled off, like an Ableton clip.
    public var loopOverride = false
    /// Force the next trigger to launch IMMEDIATELY — no launch quantize, no
    /// loop-lock, so it fires at `now` with no armed/hourglass wait even while
    /// the transport rolls and even when the voice loops. Set (and restored)
    /// around the Jam Tap path: a Tap is a zero-latency one-shot that still
    /// loops-while-held, so it must loop the voice but skip the shared-lattice
    /// bar-quantize + loop-lock that Loop/Latch use. Distinct from
    /// `loopOverride` (which forces looping): this defeats WAITING, not looping.
    public var forceInstantLaunch = false
    /// Start the voice at phase 0 (the sample's beginning) instead of
    /// phase-joining the shared lattice. Set around the Jam One-Shot path:
    /// One-Shot is a finger-drumming gate that retriggers from the TOP
    /// every tap — the opposite of Follow (old Tap), which joins mid-body
    /// at the shared clock point so layered pads lock together. Independent
    /// of forceInstantLaunch (both are set for One-Shot: fire NOW, from 0).
    public var forceZeroPhase = false
    @Published public var beatBarMode: BeatBarMode = .beat
    /// Section-label whitelist. `nil` = allow all; empty set = allow none.
    @Published public var allowedSections: Set<String>? = nil

    // MARK: - Private

    private weak var engine: AudioEngine?
    private let pool: SampleVoicePool
    private let bus: SampleBus

    /// One loaded pack's metadata + preloaded buffers. Buffers are
    /// keyed by the bare padIdx *within* the pack; cross-pack identity
    /// is the `loadedPacks` dictionary key (packId).
    private struct LoadedPack {
        let pack: ResolvedSamplePack
        #if canImport(AVFoundation)
        var buffers: [Int: AVAudioPCMBuffer] = [:]
        /// For loop-capable pads whose buffer was decoded WITH continuation
        /// audio past the region end: padIdx → loop body frame count (the
        /// buffer's remaining frames are the continuation). Absent = the
        /// whole buffer is the loop. Threaded into SampleTrigger so the
        /// voice pool can bake the seam at exact bar-snapped length.
        var loopBodyFrames: [Int: Int] = [:]
        /// padIdx → seconds the loop region was moved by the onset-phase
        /// snap at decode. The launch is delayed by the same amount so the
        /// content's downbeat still lands ON the quantize grid — without
        /// this, pads with different snaps armed to the same boundary but
        /// sounded at different times.
        var loopShiftSec: [Int: Double] = [:]
        #endif
    }

    /// Every pack the user has visited this session, keyed by packId.
    /// Multiple packs stay resident simultaneously so voices from a
    /// previous carousel page keep ringing while the user plays the
    /// next one. A pack loads once (first visit) and is only dropped
    /// via `unloadPack`/`unloadAllPacks`.
    ///
    /// MEMORY: no LRU eviction — Starter/curated packs are ~5 MB
    /// each and song-derived pads are capped at 8 s slices; AppState
    /// unloads stale `song-derived:` packs on bundle change. Add LRU
    /// here if device measurement ever shows pressure.
    private var loadedPacks: [String: LoadedPack] = [:]

    /// Synthetic packId for locally-recorded samples (P3 mic
    /// pipeline). Never appears in `loadedPacks` — local pads live in
    /// `localBuffers`, a parallel path consulted BEFORE pack lookup —
    /// but it keys the voice pool + effects resolver so local voices
    /// are addressable like any other pad.
    // nonisolated: immutable Sendable constant, read from off-main
    // contexts (LayerOfflineRenderer's render path).
    public nonisolated static let localPackId = "local"

    #if canImport(AVFoundation)
    /// Locally-recorded samples assigned to grid pads, keyed by grid
    /// padIdx (row*10+col). A local assignment shadows the active
    /// pack's pad at the same index. ModeCoordinator populates this
    /// from PadAssignmentStore + PadSampleStore. Buffers are stored
    /// pre-converted to the canonical format (D-017 single-resample
    /// rule — conversion happens once in `setLocalBuffer`).
    private var localBuffers: [Int: (meta: PadSampleMetadata, buffer: AVAudioPCMBuffer)] = [:]
    #endif

    /// The pack currently fronted by the UI (visible carousel page).
    /// Triggers without an explicit packId resolve against this.
    public private(set) var activePackId: String?

    /// Per-pad user loop override (radial "Loop" toggle). true forces the
    /// pad to loop, false forces one-shot; absent = manifest/transform
    /// behavior. Session-scoped — cleared when the pack unloads.
    public private(set) var padLoopOverrides: [SamplePadKey: Bool] = [:]

    /// Effective loop state for a pad: override > manifest loop point /
    /// loopable flag > transform-chain `.loop`. Drives the radial menu's
    /// Loop segment highlight and the grid's loop badge.
    public func padLoops(packId: String, padIdx: Int) -> Bool {
        let key = SamplePadKey(packId: packId, padIdx: padIdx)
        if let override = padLoopOverrides[key] { return override }
        guard let pad = loadedPacks[packId]?.pack.pack.pads
            .first(where: { $0.padIdx == padIdx })
        else { return loopResolver?(packId, padIdx) ?? false }
        return pad.loopPointSec != nil || (pad.loopable ?? false)
            || (loopResolver?(packId, padIdx) ?? false)
    }

    /// Flip a pad's loop override (radial "Loop"). A ringing voice is
    /// released so the next trigger picks up the new behavior instead of
    /// an un-releasable loop lingering under a now-one-shot pad.
    /// Explicitly set (or clear, with nil) a pad's loop override. Used by
    /// the Tap-mode trigger path to force a single one-shot pass on
    /// loop-capable pads (web/desktop parity: the surface MODE decides
    /// loop vs one-shot; the pad's manifest flag only matters in Loop /
    /// Latch). The radial menu keeps using togglePadLoop.
    public func setPadLoopOverride(packId: String, padIdx: Int, _ value: Bool?) {
        let key = SamplePadKey(packId: packId, padIdx: padIdx)
        if let value { padLoopOverrides[key] = value }
        else { padLoopOverrides.removeValue(forKey: key) }
    }

    public func togglePadLoop(packId: String, padIdx: Int) {
        let key = SamplePadKey(packId: packId, padIdx: padIdx)
        let now = padLoops(packId: packId, padIdx: padIdx)
        padLoopOverrides[key] = !now
        if pool.isActive(padKey: key) { pool.release(padKey: key) }
    }

    /// Bundle context for quantize + gate. Updated when a bundle loads.
    private var beats: [Double] = []
    private var downbeats: [Double] = []
    private var sections: [SectionEvent] = []
    private var tempoBpm: Double? = nil

    /// Callback fired for every trigger + release. AppState wires this
    /// into LayerRecorder in Phase 4. Left as a plain closure so the
    /// scheduler doesn't depend on the recorder.
    public var onEvent: ((LayerEvent) -> Void)?

    /// Injected by AppState to resolve the effective per-pad effect
    /// params for a given (packId, padIdx) — user override > manifest
    /// baseline > `.neutral`. Left as a closure so the scheduler
    /// doesn't depend on SampleSettingsStore. When nil (unwired
    /// tests, boot), effects fall back to the pad's manifest value or
    /// `.neutral`.
    public var effectsResolver: ((_ packId: String, _ padIdx: Int, _ manifest: SamplePadEffects?) -> SamplePadEffects)?

    /// Debug tripwire for the contribution-engine invariant: every
    /// live trigger must arrive via ContributionEventBus →
    /// ModeCoordinator (the executor sets this to return true only
    /// while it is executing a routed AudioAction). nil (tests,
    /// unwired boot) = allowed. `triggerRaw` is exempt — legacy
    /// LayerPlayer replay is the one documented bypass (D-015).
    public var contributionGuard: (() -> Bool)?

    /// P4 seam: per-trigger transform resolution. Given the pad's
    /// preloaded buffer + (packId, padIdx), returns the buffer to
    /// actually play (identity when the pad has no transform chain).
    /// nil = no transform engine wired (P1–P3).
    #if canImport(AVFoundation)
    public var transformResolver: ((AVAudioPCMBuffer, String, Int) -> AVAudioPCMBuffer)?
    #endif

    /// P4 seam: per-pad `.loop` transform flag. Given (packId, padIdx)
    /// returns whether the pad's transform chain contains `.loop` —
    /// looping pads keep ringing after touch-up (hold mode releases
    /// them like pack pads with a loop point). nil = no transform
    /// engine wired.
    public var loopResolver: ((String, Int) -> Bool)?

    public init(engine: AudioEngine, bus: SampleBus, pool: SampleVoicePool) {
        self.engine = engine
        self.bus = bus
        self.pool = pool
    }

    // MARK: - Bundle context

    /// Refresh quantize + gate context from the loaded song. Call
    /// after every successful `AppState.loadBundle`.
    public func updateBundle(timeline: BundleTimeline, meta: BundleMeta) {
        self.beats = timeline.beats
        self.downbeats = timeline.downbeats
        self.sections = timeline.sections
        self.tempoBpm = meta.tempoBpm
    }

    /// Sketch-mode context: no analysed beats/downbeats/sections, so
    /// `Quantizer.nextQuantized` falls through to its synthetic grid
    /// at `tempoBpm`. Pass nil to clear entirely (no bundle, no
    /// tempo — quantize degrades to .off). AppState calls this when
    /// the Sketch tab activates and restores the bundle context via
    /// `updateBundle` when it deactivates.
    /// One 4/4 bar at the song tempo, or nil without tempo. Used to
    /// bar-snap loop buffers at decode so they match the lock grid.
    private var currentBarSeconds: Double? {
        guard let bpm = tempoBpm, bpm > 0 else { return nil }
        return (60.0 / bpm) * 4.0
    }

    /// Shared lock-cycle length (s). Cycle = the longest analyzer loop
    /// region in the ACTIVE pack: kit windows are whole REAL bars now
    /// (e.g. 2 bars = 5.04 s), and the old bars-fitting-8s constant-tempo
    /// formula queued presses to a cycle no pad plays — armed pads fired
    /// mid-cycle of the held loops. Falls back to the constant-tempo
    /// formula for packs without analyzer regions, 8 s without tempo.
    public var loopLengthSeconds: Double {
        loopLengthSeconds(packId: activePackId)
    }

    /// Pack-scoped cycle: the trigger path passes the TRIGGERING pack's
    /// id (which may not be the UI-fronted one — jam overflow / borrow
    /// pads carry explicit packIds), so a pad always tiles/joins against
    /// its own pack's cycle, never a stale carousel page's.
    func loopLengthSeconds(packId: String?) -> Double {
        if let pid = packId, let entry = loadedPacks[pid] {
            let cycle = entry.pack.pack.pads
                .filter { ($0.loopable ?? false) || $0.loopStartSec != nil }
                .compactMap { p -> Double? in
                    guard let ls = p.loopStartSec, let le = p.loopEndSec,
                          le > ls else { return nil }
                    return le - ls
                }
                .max()
            if let cycle, cycle > 0.5 { return cycle }
        }
        guard let bpm = tempoBpm, bpm > 0 else { return 8.0 }
        let barSec = (60.0 / bpm) * 4.0
        let bars = max(1.0, (8.0 / barSec).rounded())
        return bars * barSec
    }

    /// Next loop-launch boundary at/after `now` (song seconds): the BAR
    /// grid — never the full loop cycle, never individual beats.
    ///
    /// The old lattice was multiples of `loopLengthSeconds` from song
    /// origin, so a pad tapped mid-cycle waited up to the whole multi-bar
    /// cycle (~6–8 s) before sounding — which read as "the pad doesn't
    /// play". Real downbeats first (the grid the user actually hears; a
    /// synthetic origin+N·bar grid walks off real music), constant-tempo
    /// bar fallback — web twin: padengine.js `_transportLaunchTime("bar")`.
    /// The phase-locked join in `trigger` starts late joiners mid-body,
    /// so a bar-boundary launch still lands every loop at the same
    /// musical position as the ones already ringing.
    func nextLoopBoundary(after now: Double) -> Double {
        if !downbeats.isEmpty || (tempoBpm ?? 0) > 0 {
            return Quantizer.nextQuantized(
                songSeconds: now, mode: .bar, beats: beats,
                downbeats: downbeats, sections: sections, tempoBpm: tempoBpm)
        }
        // No bar data at all: the shared full-cycle lattice (web
        // `_lockLaunchTime` without barSec) so locked loops still align
        // with each other.
        return Self.lockGridBoundary(after: now, spacing: loopLengthSeconds)
    }

    /// Next boundary on a self-anchored uniform lattice: multiples of
    /// `spacing` from `anchor`; a press within the grace window after a
    /// boundary fires immediately (a tap 40 ms "late" reads as on-beat,
    /// not as a full-period wait). Web twin: padengine.js `_lockLaunchTime`.
    static func lockGridBoundary(
        after now: Double, spacing: Double, anchor: Double = 0,
        graceSec: Double = 0.08
    ) -> Double {
        guard spacing > 0 else { return now }
        let elapsed = now - anchor
        // Swift's remainder keeps the dividend's sign; a pre-anchor `now`
        // folds negative and lands in the immediate-fire branch, same as
        // the web engine.
        let phase = elapsed.truncatingRemainder(dividingBy: spacing)
        if phase <= graceSec { return now }
        return anchor + ((elapsed / spacing).rounded(.down) + 1) * spacing
    }

    /// Free-run (transport stopped) lock-grid spacing: a SINGLE BAR at
    /// the song tempo, not the full loop cycle — a loop tapped mid-jam
    /// waits ≤ 1 bar instead of up to the whole ~8 s cycle (which read
    /// as "the pad doesn't play"). Full-cycle fallback when tempo is
    /// unknown; scaled by the practice rate like every other launch
    /// delay. Web twin: `_lockLaunchTime`'s barSec "bar" grid.
    var freeRunLockSpacingSec: Double {
        let rate = engine?.clock.rate ?? 1.0
        return (currentBarSeconds ?? loopLengthSeconds) / max(rate, 0.0001)
    }

    /// Shared phase-lattice anchor in HOST seconds (mach clock) — the web
    /// engine's `_lockAnchor` (AudioContext-time domain). Set at the FIRST
    /// loop launch's PRE-shift boundary; every later loop joins
    /// `(boundary − anchor) mod bodySec` seconds into its body so all
    /// same-length loops sit at the same musical position at any wall
    /// time. Host time, not song time, because the free-run lattice must
    /// keep advancing while the transport clock is frozen.
    /// internal (not private) so the timing tests can pin the anchoring
    /// contract (first launch anchors / silence re-anchors) directly.
    private(set) var loopLockAnchorHostSec: Double? = nil

    /// Buffer offset (seconds into the loop body) a phase-locked launch
    /// starts at. Measured from the PRE-shift lattice `boundary`, never
    /// the shifted start time: the per-pad onset shift is launch
    /// compensation, and measuring it back in as buffer offset cancels
    /// it — pads flam by their shift difference (up to ±60 ms each way).
    /// Negative deltas (boundary before anchor) fold into [0, body).
    static func phaseJoinSeconds(
        boundary: Double, anchor: Double, bodySec: Double
    ) -> Double {
        guard bodySec > 0 else { return 0 }
        let raw = (boundary - anchor).truncatingRemainder(dividingBy: bodySec)
        return raw < 0 ? raw + bodySec : raw
    }

    /// Shared-cycle target for a loop bake, in buffer frames: the pack
    /// cycle (`loopLengthSeconds`) rounded at the buffer rate, floored at
    /// the pad's own body (the longest region pad already fills the
    /// cycle — no tiling). 0 = keep the pad's own length. Gated on a REAL
    /// analyzer region exactly like the web (_bakePad hasRegion,
    /// padengine.js:924-931): region-less loop pads (borrow-pack loops,
    /// whole buffers) have no shared musical cycle — loopLengthSeconds
    /// would fall back to an arbitrary 8 s lattice. Rounding (not trunc)
    /// keeps the cycle frame count consistent with the rounded body
    /// counts everywhere else (see decode's trunc-per-edge note).
    static func sharedCycleFrames(
        bodyFrames: Int, cycleSec: Double, sampleRate: Double,
        hasRegion: Bool
    ) -> Int {
        guard hasRegion, bodyFrames > 0, cycleSec > 0, sampleRate > 0
        else { return 0 }
        return max(bodyFrames, Int((cycleSec * sampleRate).rounded()))
    }

    /// Bar-floor for loop launches: sub-bar quantize (1/8, 1/4, 1/2) is
    /// promoted to `.bar` when the trigger will loop. A multi-bar loop
    /// beat-quantized starts on whatever beat the tap landed near, so
    /// its bar 1 sits mid-bar — out of phase with the song AND with
    /// every other loop even though each is individually "on a beat"
    /// (the "queued pads start at random times" bug). Beat granularity
    /// keeps making sense for one-shots; `.phrase` (section) is coarser
    /// than a bar and already bar-aligned, so it passes through.
    static func loopQuantize(
        _ mode: QuantizeMode, willLoop: Bool
    ) -> QuantizeMode {
        guard willLoop else { return mode }
        switch mode {
        case .eighth, .quarter, .half: return .bar
        case .off, .bar, .phrase: return mode
        }
    }

    /// Seconds until the bar boundary that looping pads launch on, or
    /// nil when launches are instant (transport stopped or loop-lock
    /// off). Sequence pads use this so a tapped beat lands phase-locked
    /// with the sample loops already running.
    public func secondsToNextLoopLaunch() -> Double? {
        guard loopLock, engine?.clock.state == .playing else { return nil }
        let now = nowSongSeconds()
        let dt = nextLoopBoundary(after: now) - now
        return dt > 0.05 ? dt : nil
    }

    public func updateSyntheticContext(tempoBpm: Double?) {
        self.beats = []
        self.downbeats = []
        self.sections = []
        self.tempoBpm = tempoBpm
    }

    // MARK: - Pack activation

    /// Preload every pad's audio buffer for `pack` into the resident
    /// registry, without touching the active pack or any ringing
    /// voices. No-op if the pack is already loaded (revisiting a
    /// carousel page is free). For file-backed pads, `padFileURLs`
    /// supplies the local file; for song-derived pads, `stemFiles`
    /// maps stem role → local file URL (from `BundleStore.cachedStem`).
    /// Silently skips pads whose file is missing so a partially-cached
    /// pack still triggers what it can.
    /// Content fingerprint for the resident-pack guard. Borrow packs are
    /// re-arranged under an UNCHANGED packId ("borrow-{donor}-{stem}":
    /// 16↔64 relayout, session-target re-borrow), and the old
    /// already-resident early-return then left triggers resolving against
    /// the first load's stale pad layout — padNotFound silence or
    /// wrong-pad audio. Cheap identity: pad count + per-pad idx/file/url.
    private static func packFingerprint(_ pack: ResolvedSamplePack) -> Int {
        var h = Hasher()
        for p in pack.pack.pads {
            h.combine(p.padIdx)
            h.combine(p.filename ?? "")
            h.combine(p.sampleUrl ?? "")
            h.combine(p.stemSlice?.startSec ?? -1)
            h.combine(p.stemSlice?.endSec ?? -1)
        }
        return h.finalize()
    }
    private var packFingerprints: [String: Int] = [:]

    public func preloadPack(
        _ pack: ResolvedSamplePack,
        stemFiles: [String: URL]
    ) throws {
        let packId = pack.pack.packId
        let fp = Self.packFingerprint(pack)
        if loadedPacks[packId] != nil, packFingerprints[packId] == fp { return }
        loadedPacks[packId] = nil  // same id, different content: rebuild
        packFingerprints[packId] = fp

        #if canImport(AVFoundation)
        let loaded = Self.decodePackBuffers(
            pack, stemFiles: stemFiles, target: engine?.canonicalFormat,
            barSeconds: currentBarSeconds
        )
        loadedPacks[packId] = LoadedPack(
            pack: pack, buffers: loaded.buffers,
            loopBodyFrames: loaded.loopBodyFrames,
            loopShiftSec: loaded.loopShiftSec)
        #else
        loadedPacks[packId] = LoadedPack(pack: pack)
        #endif
    }

    /// Async twin of `preloadPack`: the WAV decode + resample (up to 16
    /// pads at AVAudioQuality.max, ~1–3 s cold) runs on a detached task
    /// so the main thread never stalls when a pack is first visited.
    /// This is the path the UI takes (tab switch / carousel / pinned
    /// pads); the sync `preloadPack` stays for the offline export + tests
    /// where blocking is harmless. No-op if already resident, and
    /// re-checks after the await in case a concurrent call won the race.
    /// True once `packId`'s pad buffer is resident (preload finished) —
    /// lets a press that raced the async preload know when to retry.
    public func isPadLoaded(packId: String, padIdx: Int) -> Bool {
        #if canImport(AVFoundation)
        return loadedPacks[packId]?.buffers[padIdx] != nil
        #else
        return loadedPacks[packId] != nil
        #endif
    }

    public func preloadPackAsync(
        _ pack: ResolvedSamplePack,
        stemFiles: [String: URL]
    ) async {
        let packId = pack.pack.packId
        let fp = Self.packFingerprint(pack)
        if loadedPacks[packId] != nil, packFingerprints[packId] == fp { return }
        loadedPacks[packId] = nil  // same id, different content: rebuild
        packFingerprints[packId] = fp

        #if canImport(AVFoundation)
        let target = engine?.canonicalFormat
        let bar = currentBarSeconds
        let loaded = await Task.detached(priority: .userInitiated) {
            Self.decodePackBuffers(pack, stemFiles: stemFiles, target: target,
                                   barSeconds: bar)
        }.value
        // Post-await ownership check: install only if no pack landed
        // meanwhile AND our fingerprint is still the wanted one (a
        // concurrent reload with NEWER content must not be clobbered by
        // this stale decode).
        guard loadedPacks[packId] == nil, packFingerprints[packId] == fp
        else { return }
        loadedPacks[packId] = LoadedPack(
            pack: pack, buffers: loaded.buffers,
            loopBodyFrames: loaded.loopBodyFrames,
            loopShiftSec: loaded.loopShiftSec)
        #else
        loadedPacks[packId] = LoadedPack(pack: pack)
        #endif
    }

    #if canImport(AVFoundation)
    /// Decode + canonical-format-convert every pad buffer for `pack`.
    /// Pure and `nonisolated` so it can run off the main actor (see
    /// `preloadPackAsync`).
    ///
    /// Every voice slot is connected at the canonical 48 kHz stereo
    /// format (see SampleVoicePool.attach). AVAudioPlayerNode requires
    /// the scheduled buffer's format to match that connection format
    /// exactly — otherwise scheduleBuffer throws NSException and SIGABRTs
    /// the app. Sample files ship in a mix of sample rates / channel
    /// counts (StarterPack is mono 44.1 kHz), so we convert everything
    /// here via AVAudioConverter — the SINGLE resample point of the
    /// contribution path (D-017); everything downstream runs at 48 k
    /// SRC-free. File-backed pads read from `padFileURLs`; song-derived
    /// pads slice the shared stem in `stemFiles`. Missing files are
    /// skipped so a partially-cached pack still triggers what it can.
    nonisolated static func decodePackBuffers(
        _ pack: ResolvedSamplePack,
        stemFiles: [String: URL],
        target: AVAudioFormat?,
        barSeconds: Double? = nil
    ) -> (buffers: [Int: AVAudioPCMBuffer], loopBodyFrames: [Int: Int],
          loopShiftSec: [Int: Double]) {
        var loaded: [Int: AVAudioPCMBuffer] = [:]
        var bodies: [Int: Int] = [:]
        var shifts: [Int: Double] = [:]
        for pad in pack.pack.pads {
            if let url = pack.padFileURLs[pad.padIdx] {
                if let buf = loadBuffer(from: url, slice: nil, target: target) {
                    loaded[pad.padIdx] = buf
                }
            } else if let slice = pad.stemSlice,
                      let stemURL = stemFiles[slice.stemRole] {
                let region = _loopRegion(for: pad, slice: slice,
                                         barSeconds: barSeconds)
                // Loop-capable pads read a little continuation audio PAST
                // the region end so the seam can be baked without changing
                // the loop period (SeamlessLoop.exactCrossfaded). One-shot
                // pads stay byte-identical to before.
                let mayLoop = (pad.loopable ?? false)
                    || pad.loopPointSec != nil || pad.loopStartSec != nil
                let contSec = mayLoop ? Self.loopContinuationSec : 0
                if let r = loadBufferWithContinuation(
                    from: stemURL, slice: region, target: target,
                    continuationSec: contSec
                ) {
                    loaded[pad.padIdx] = r.buffer
                    if r.continuationFrames > 0 {
                        bodies[pad.padIdx] =
                            Int(r.buffer.frameLength) - r.continuationFrames
                    }
                    if r.shiftSec != 0 { shifts[pad.padIdx] = r.shiftSec }
                }
            }
        }
        return (loaded, bodies, shifts)
    }

    /// Continuation audio read past a loop-capable region's end for the
    /// exact-length seam bake — covers the 8–30 ms crossfade clamp with
    /// margin for SRC rounding.
    nonisolated static let loopContinuationSec: Double = 0.035

    /// The stem region to preload for a pad: the analyzer's optimized loop seam
    /// [loopStartSec, loopEndSec] when the pad carries one (so a looping pad
    /// cycles that tighter, click-free region), else the full stemSlice. No 8s
    /// clamp — that cap is for user-sampled contribute chops, not auto-kit.
    ///
    /// BAR-SNAP: a loopable region's length is snapped to a whole number of
    /// bars when the tempo is known. Without this the buffers looped at their
    /// raw 8.00 s while the loop-lock grid ran at the bar-snapped cycle
    /// (e.g. 8.39 s at 143 BPM) — locked loops drifted visibly apart every
    /// pass (the "timing misalignment on the samples").
    ///
    /// EXCEPT when the pad carries an explicit [loopStartSec, loopEndSec]:
    /// the kit builder exports those on the song's REAL local downbeats,
    /// which drift a few dozen ms per bar against the constant tempo — a
    /// constant-tempo re-snap here cut the region short of the real
    /// downbeat, so the wrap landed in the pre-beat gap and the loop
    /// audibly paused (Doomsday drums: real 3 bars 7.570 s vs 7.545 s at
    /// constant BPM). Explicit regions are whole bars by construction —
    /// play them verbatim.
    private nonisolated static func _loopRegion(
        for pad: SamplePad, slice: StemSlice, barSeconds: Double? = nil
    ) -> StemSlice {
        if let ls = pad.loopStartSec, let le = pad.loopEndSec, le > ls {
            return StemSlice(stemRole: slice.stemRole, startSec: ls, endSec: le)
        }
        guard pad.loopable ?? false, let bar = barSeconds, bar > 0 else {
            return slice
        }
        let len = slice.endSec - slice.startSec
        let bars = max(1.0, (len / bar).rounded())
        return StemSlice(stemRole: slice.stemRole,
                         startSec: slice.startSec,
                         endSec: slice.startSec + bars * bar)
    }
    #endif

    /// Make `pack` the UI-fronted pack, loading its buffers if this
    /// is the first visit. Deliberately does NOT stop ringing voices —
    /// swiping the carousel layers packs instead of cutting them off
    /// (the voice pool keys slots by composite SamplePadKey, so old
    /// voices stay addressable for toggle-off/release).
    public func setActivePack(
        _ pack: ResolvedSamplePack,
        stemFiles: [String: URL]
    ) throws {
        try preloadPack(pack, stemFiles: stemFiles)
        activePackId = pack.pack.packId
    }

    /// Async twin of `setActivePack`: fronts the pack's id immediately
    /// (so the grid + carousel update this run loop) then decodes its
    /// buffers off-main via `preloadPackAsync`. First-visit taps in the
    /// brief decode window degrade to padNotFound silence rather than
    /// freezing the UI (the freeze that surfaced the iOS touch-and-hold
    /// loupe on the Contribute tab).
    public func setActivePackAsync(
        _ pack: ResolvedSamplePack,
        stemFiles: [String: URL]
    ) async {
        activePackId = pack.pack.packId
        await preloadPackAsync(pack, stemFiles: stemFiles)
    }

    /// Whether `packId`'s buffers are resident.
    public func isPackLoaded(packId: String) -> Bool {
        loadedPacks[packId] != nil
    }

    #if canImport(AVFoundation)
    /// The pad's untransformed canonical-format buffer (pack preload
    /// or local sample). P4 transform rendering + bake read the base
    /// here so chains always apply to the original audio, never to a
    /// previously transformed result.
    public func baseBuffer(packId: String, padIdx: Int) -> AVAudioPCMBuffer? {
        if packId == Self.localPackId {
            return localBuffers[padIdx]?.buffer
        }
        return loadedPacks[packId]?.buffers[padIdx]
    }

    // MARK: - Committed pad trims

    /// User-committed playback region per pad, as fractions of the
    /// transform-resolved buffer (the trimmer's Apply sets it). Every
    /// playback path AND the pad waveform read through the trimmed
    /// buffer, so pads always show exactly what plays.
    @Published public private(set) var padTrims: [SamplePadKey: ClosedRange<Double>] = [:]
    /// Bumped on every trim change — UI peak caches key on it.
    public private(set) var trimRevision: Int = 0

    public func setPadTrim(
        packId: String, padIdx: Int,
        startFraction: Double, endFraction: Double,
        preserveLength: Bool = false
    ) {
        let key = SamplePadKey(packId: packId, padIdx: padIdx)
        let lo = max(0.0, min(startFraction, endFraction))
        let hi = min(1.0, max(startFraction, endFraction))
        if lo <= 0.001 && hi >= 0.999 {
            padTrims[key] = nil  // full range = untrimmed
            padTrimPreserve.remove(key)
        } else if hi - lo > 0.005 {
            padTrims[key] = lo...hi
            if preserveLength {
                padTrimPreserve.insert(key)
            } else {
                padTrimPreserve.remove(key)
            }
        }
        trimRevision += 1
        persistTrims()
    }

    /// "Preserve length": the trim GATES the audio but the pad keeps its
    /// original duration — the kept region plays at its original position
    /// in the cycle with silence around it, so a looping pad still fires
    /// on its musical grid instead of retriggering every trimmed-length
    /// seconds. Off = the trim is a cut (loop cycle shrinks with it, the
    /// stutter effect). Field-requested: "sometimes I want that, sometimes
    /// not" — hence per-pad.
    public private(set) var padTrimPreserve: Set<SamplePadKey> = []

    public func padTrimPreservesLength(packId: String, padIdx: Int) -> Bool {
        padTrimPreserve.contains(SamplePadKey(packId: packId, padIdx: padIdx))
    }

    private static let trimsDefaultsKey = "toneforge.padTrims"

    private func persistTrims() {
        // Wire shape: [lo, hi] (legacy) or [lo, hi, preserve(0/1)] — the
        // third element is additive so pre-preserve installs restore fine.
        var raw: [String: [Double]] = [:]
        for (k, v) in padTrims {
            let preserve: Double = padTrimPreserve.contains(k) ? 1 : 0
            raw["\(k.packId)#\(k.padIdx)"] = [v.lowerBound, v.upperBound, preserve]
        }
        UserDefaults.standard.set(raw, forKey: Self.trimsDefaultsKey)
    }

    /// Restore committed trims (call once at startup — trims survive
    /// relaunch; keys are pack-scoped so stale packs are inert).
    public func restorePersistedTrims() {
        guard let raw = UserDefaults.standard.dictionary(
            forKey: Self.trimsDefaultsKey) as? [String: [Double]]
        else { return }
        for (key, bounds) in raw {
            guard bounds.count >= 2,
                  let hash = key.lastIndex(of: "#"),
                  let padIdx = Int(key[key.index(after: hash)...])
            else { continue }
            let packId = String(key[..<hash])
            let lo = max(0.0, min(bounds[0], bounds[1]))
            let hi = min(1.0, max(bounds[0], bounds[1]))
            if hi - lo > 0.005 && (lo > 0.001 || hi < 0.999) {
                let k = SamplePadKey(packId: packId, padIdx: padIdx)
                padTrims[k] = lo...hi
                if bounds.count >= 3, bounds[2] > 0.5 {
                    padTrimPreserve.insert(k)
                }
            }
        }
        trimRevision += 1
    }

    public func padTrim(packId: String, padIdx: Int) -> ClosedRange<Double>? {
        padTrims[SamplePadKey(packId: packId, padIdx: padIdx)]
    }

    #if canImport(AVFoundation)
    /// Transform + committed-trim resolution — the ONE seam every
    /// trigger/waveform path goes through so audio and UI can't
    /// disagree. Slicing is a plain frame copy (a few ms at trigger
    /// rate; the UI caches peaks above this, keyed on trimRevision).
    private func resolvedBuffer(
        _ base: AVAudioPCMBuffer, packId: String, padIdx: Int
    ) -> AVAudioPCMBuffer {
        let transformed = transformResolver?(base, packId, padIdx) ?? base
        guard let trim = padTrim(packId: packId, padIdx: padIdx) else {
            return transformed
        }
        // Preserve-length: the trim GATES instead of cutting — output stays
        // the ORIGINAL duration with the kept region at its original offset
        // and silence around it. A looping pad keeps its musical cycle
        // (fires "at the right time"); a plain cut shrinks the cycle to the
        // trimmed length (retriggers every second on a 1 s trim — sometimes
        // the desired stutter, hence the checkbox).
        if padTrimPreserve.contains(
            SamplePadKey(packId: packId, padIdx: padIdx)),
           let gated = Self.sliceGated(
               transformed, from: trim.lowerBound, to: trim.upperBound) {
            return gated
        }
        guard let sliced = Self.slice(
            transformed, from: trim.lowerBound, to: trim.upperBound)
        else { return transformed }
        return sliced
    }

    /// Zero-filled copy at the source's full length with only
    /// [from, to) (fractions) carrying audio — the gate behind
    /// preserve-length trims.
    private static func sliceGated(
        _ buffer: AVAudioPCMBuffer, from: Double, to: Double
    ) -> AVAudioPCMBuffer? {
        let total = Int(buffer.frameLength)
        let start = max(0, min(total - 1, Int(Double(total) * from)))
        let end = max(start + 1, min(total, Int(Double(total) * to)))
        guard end - start > 32,
              let out = AVAudioPCMBuffer(
                  pcmFormat: buffer.format,
                  frameCapacity: AVAudioFrameCount(total)),
              let src = buffer.floatChannelData,
              let dst = out.floatChannelData
        else { return nil }
        for ch in 0..<Int(buffer.format.channelCount) {
            dst[ch].update(repeating: 0, count: total)
            dst[ch].advanced(by: start)
                .update(from: src[ch] + start, count: end - start)
        }
        out.frameLength = AVAudioFrameCount(total)
        return out
    }

    private static func slice(
        _ buffer: AVAudioPCMBuffer, from: Double, to: Double
    ) -> AVAudioPCMBuffer? {
        let total = Int(buffer.frameLength)
        let start = max(0, min(total - 1, Int(Double(total) * from)))
        let end = max(start + 1, min(total, Int(Double(total) * to)))
        let frames = end - start
        guard frames > 32,
              let out = AVAudioPCMBuffer(
                  pcmFormat: buffer.format,
                  frameCapacity: AVAudioFrameCount(frames)),
              let src = buffer.floatChannelData,
              let dst = out.floatChannelData
        else { return nil }
        for ch in 0..<Int(buffer.format.channelCount) {
            dst[ch].update(from: src[ch] + start, count: frames)
        }
        out.frameLength = AVAudioFrameCount(frames)
        return out
    }
    #endif

    /// Playback length (seconds) of a pad's one-shot buffer, for UI
    /// auto-reset after a preview. Returns nil for looping pads (no
    /// natural end — they stop only on manual release) or when the pad
    /// isn't loaded.
    public func oneShotDurationSec(packId: String, padIdx: Int) -> Double? {
        guard let entry = loadedPacks[packId],
              let pad = entry.pack.pack.pads.first(where: { $0.padIdx == padIdx }),
              let buffer = entry.buffers[padIdx]
        else { return nil }
        if pad.loopPointSec != nil || (loopResolver?(packId, padIdx) ?? false) {
            return nil
        }
        let sr = buffer.format.sampleRate
        guard sr > 0 else { return nil }
        return Double(buffer.frameLength) / sr
    }

    /// Downsampled peak envelope + duration for the waveform trimmer.
    /// Reads the same (transform-resolved) buffer `previewTrimmed`
    /// plays, so the drawn waveform matches what auditions. Peaks are
    /// normalized to 0–1 (ChopWaveformView's contract). nil when the
    /// pad's buffer isn't resident.
    public func padWaveform(
        packId: String, padIdx: Int, binCount: Int = 100,
        includeTrim: Bool = true
    ) -> (peaks: [Float], durationSec: Double)? {
        guard binCount > 0,
              let base = baseBuffer(packId: packId, padIdx: padIdx)
        else { return nil }
        // includeTrim:false = the trimmer sheet's FULL waveform (its
        // handles express the trim); true = pads showing what plays.
        let buffer = includeTrim
            ? resolvedBuffer(base, packId: packId, padIdx: padIdx)
            : (transformResolver?(base, packId, padIdx) ?? base)
        let frames = Int(buffer.frameLength)
        let sr = buffer.format.sampleRate
        guard frames > 0, sr > 0,
              let channels = buffer.floatChannelData
        else { return nil }

        let channelCount = Int(buffer.format.channelCount)
        let framesPerBin = max(1, frames / binCount)
        var peaks = [Float](repeating: 0, count: binCount)
        for bin in 0..<binCount {
            let start = bin * framesPerBin
            guard start < frames else { break }
            let end = min(frames, start + framesPerBin)
            var peak: Float = 0
            for ch in 0..<channelCount {
                let data = channels[ch]
                for i in start..<end where abs(data[i]) > peak {
                    peak = abs(data[i])
                }
            }
            peaks[bin] = peak
        }
        if let maxPeak = peaks.max(), maxPeak > 0 {
            for i in peaks.indices { peaks[i] /= maxPeak }
        }
        return (peaks, Double(frames) / sr)
    }
    #endif

    /// Release any ringing voices belonging to `packId` and drop its
    /// buffers. Called by AppState for stale song-derived packs when
    /// a different bundle loads.
    public func unloadPack(packId: String) {
        guard let entry = loadedPacks.removeValue(forKey: packId) else { return }
        for pad in entry.pack.pack.pads {
            let key = SamplePadKey(packId: packId, padIdx: pad.padIdx)
            if pool.isActive(padKey: key) {
                pool.release(padKey: key)
            }
            padLoopOverrides.removeValue(forKey: key)
        }
        if activePackId == packId {
            activePackId = nil
        }
    }

    /// Drop every loaded pack + free all preloaded buffers. Called on
    /// song unload / tab teardown.
    public func unloadAllPacks() {
        pool.stopAll()
        loadedPacks.removeAll()
        padLoopOverrides.removeAll()
        activePackId = nil
    }

    // MARK: - Local samples (P3 mic pipeline)

    #if canImport(AVFoundation)
    /// Assign a locally-recorded sample to a grid pad. Converts to
    /// the canonical connection format here, once — the voice pool
    /// requires an exact format match and must never SRC (D-017).
    /// Replaces any previous local assignment at that index.
    public func setLocalBuffer(
        _ buffer: AVAudioPCMBuffer, meta: PadSampleMetadata, for padIdx: Int
    ) {
        var resolved = buffer
        if let target = engine?.canonicalFormat, !buffer.format.isEqual(target) {
            guard let converted = Self.convert(buffer, to: target) else { return }
            resolved = converted
        }
        // Mic samples arrive already normalized to -1 dBFS by
        // RecordingProcessor; this is a ~no-op for them but gives
        // vocoded/chopped sources the same pack-parity loudness.
        Self.normalizePeak(resolved)
        SeamlessLoop.applyEdgeFades(resolved)
        localBuffers[padIdx] = (meta, resolved)
    }

    /// Remove the local assignment at `padIdx`, releasing its voice
    /// if it is still ringing. The underlying pad (if the active pack
    /// has one at this index) becomes triggerable again.
    public func clearLocalBuffer(for padIdx: Int) {
        guard localBuffers.removeValue(forKey: padIdx) != nil else { return }
        let key = SamplePadKey(packId: Self.localPackId, padIdx: padIdx)
        if pool.isActive(padKey: key) {
            pool.release(padKey: key)
        }
    }

    /// Drop every local assignment (mode switch, delete-all).
    public func clearAllLocalBuffers() {
        for padIdx in localBuffers.keys {
            let key = SamplePadKey(packId: Self.localPackId, padIdx: padIdx)
            if pool.isActive(padKey: key) {
                pool.release(padKey: key)
            }
        }
        localBuffers.removeAll()
    }

    /// Metadata for the local sample at `padIdx`, if any. The grid
    /// layout uses this for pad visuals (colorHint, class badge).
    public func localMetadata(for padIdx: Int) -> PadSampleMetadata? {
        localBuffers[padIdx]?.meta
    }
    #endif

    // MARK: - Triggering

    /// Handle a pad touch-down. Returns what happened so the UI can
    /// paint / haptic accordingly.
    ///
    /// `packId` names the pack the pad belongs to; the UI passes the
    /// carousel page's own packId explicitly so a mid-press swipe
    /// (which changes the active pack) can't misroute the gesture.
    /// nil falls back to the active pack.
    @discardableResult
    public func trigger(padIdx: Int, packId: String? = nil) -> TriggerResult {
        assert(
            contributionGuard?() ?? true,
            "SampleScheduler.trigger must be reached via ContributionEventBus → ModeCoordinator"
        )
        #if canImport(AVFoundation)
        // A stopped engine (config change, media reset) accepts player
        // schedules silently — pads "work" but make no sound. Revive
        // before every audible trigger.
        engine?.ensureEngineRunning()
        // Local samples shadow pack pads at the same index: consulted
        // BEFORE pack lookup. One-shot unless the transform chain
        // contains `.loop` (loopResolver).
        if packId == nil || packId == Self.localPackId,
           let local = localBuffers[padIdx] {
            return triggerLocal(padIdx: padIdx, local: local)
        }
        #endif
        guard let pid = packId ?? activePackId,
              let entry = loadedPacks[pid],
              let pad = entry.pack.pack.pads.first(where: { $0.padIdx == padIdx })
        else { return .padNotFound }

        let padKey = SamplePadKey(packId: pid, padIdx: padIdx)

        // Toggle mode: second tap stops NOW, with the 20 ms release fade.
        // Web parity (kit.js padDown latch branch → engine.release →
        // padengine.js:1296): a latched clip toggled off goes silent at
        // the tap, not at the end of the loop pass. The old iOS-only
        // "musical stop" (releaseAtLoopEnd) deferred up to a full cycle —
        // on a 4-bar loop the pad kept ringing ~7 s after the user
        // stopped it, a divergence no other surface has.
        if holdMode == .toggle, pool.isActive(padKey: padKey) {
            pool.release(padKey: padKey)
            onEvent?(LayerEvent(
                kind: .sampleOff,
                songTimeSec: nowSongSeconds(),
                params: LayerEvent.Params(padIdx: padIdx, packIdOverride: pid)
            ))
            return .toggledOff
        }

        let nowSong = nowSongSeconds()

        // Section gate.
        if !SectionResolver.isAllowed(t: nowSong, in: sections, allowed: allowedSections) {
            return .gated
        }

        // Quantize the target song-time — but only when the transport
        // is actually running. If the song isn't playing (auditioning
        // pads, no bundle loaded, paused), snapping to "the next beat"
        // uses stale beats/tempo from a previously-loaded song and
        // pushes the trigger seconds into the future, where a
        // subsequent tap will steal the same slot and cancel it. That
        // manifested as StarterPack pads (all of which have a
        // manifest-level `defaultQuantize`) being silent while
        // shoegaze-textures pads (no defaultQuantize) played fine.
        let transportRunning = (engine?.clock.state == .playing)
        let isOneShot = (pad.loopPointSec == nil)
        // Session-view launch quantization: when a quantize grid is set
        // (pad default or global) and the transport is running, EVERY
        // pad — one-shots included — waits for the boundary so triggers
        // land together. Quantize `.off` (the default) keeps the instant
        // drum-machine feel, so this only changes behavior when the user
        // has explicitly asked for a grid.
        // TAP MODE IS INSTANT: an unlatched one-shot tap (hold mode,
        // nothing that will loop, no explicit global grid) fires NOW —
        // the drum-machine feel wins over the pad's manifest
        // defaultQuantize. Loops and latched pads keep quantize so
        // they land on the grid.
        let tapModeInstant = holdMode == .hold
            && quantize == .off
            && !loopOverride
            && pad.loopPointSec == nil
            && !(pad.loopable ?? false)
            && !(padLoopOverrides[padKey] ?? false)
            && !(loopResolver?(pid, padIdx) ?? false)
        // `forceInstantLaunch` (Jam Tap) fires NOW regardless of the pad's
        // manifest defaultQuantize or a global grid — a Tap is zero-latency
        // by contract even on a loop-capable pad (which tapModeInstant, gated
        // on non-loop, can't cover).
        let effectiveQuantize: QuantizeMode = (transportRunning && !tapModeInstant && !forceInstantLaunch)
            ? (pad.defaultQuantize ?? quantize)
            : .off
        // Will this trigger loop? (Same predicate as the SampleTrigger below.)
        let toggleLoop = (holdMode == .toggle)
            && (pad.loopPointSec != nil || effectiveQuantize == .off)
        let naturalLoop = toggleLoop || loopOverride || pad.loopPointSec != nil
            || (pad.loopable ?? false) || (loopResolver?(pid, padIdx) ?? false)
        // Per-pad radial override wins over manifest/transform behavior.
        let willLoop = padLoopOverrides[padKey] ?? naturalLoop
        // Loop lock: a looping pad starts on the next BAR boundary so all
        // loops phase-lock and stack coherently (the phase-locked join
        // below puts a late joiner mid-body, so a ≤1-bar wait is enough
        // to stay in unison — loops no longer wait out the full cycle);
        // otherwise normal quantize, with sub-bar grids promoted to the
        // bar for looping pads (loopQuantize) so a loop can never start
        // mid-bar.
        // A Tap loops the voice but must NOT arm to the bar lattice — the
        // loop-lock wait is exactly the hourglass the Tap contract forbids.
        // The willLoop-gated phase JOIN below still runs, so a rolling Tap
        // loop still lands mid-body in unison, it just fires now.
        let willLoopLock = loopLock && willLoop && !forceInstantLaunch
        let targetSong: Double = (willLoopLock && transportRunning)
            ? nextLoopBoundary(after: nowSong)
            : Quantizer.nextQuantized(
                songSeconds: nowSong,
                mode: Self.loopQuantize(effectiveQuantize, willLoop: willLoop),
                beats: beats,
                downbeats: downbeats,
                sections: sections,
                tempoBpm: tempoBpm
            )

        #if canImport(AVFoundation)
        guard let baseBuffer = entry.buffers[padIdx] else { return .padNotFound }
        let buffer = resolvedBuffer(baseBuffer, packId: pid, padIdx: padIdx)

        // Hold mode retrigger: stop any existing voice for this pad before
        // firing a new one. This gives "self-choke" behavior — rapid taps
        // restart the sample instead of stacking voices.
        if holdMode == .hold, pool.isActive(padKey: padKey) {
            pool.release(padKey: padKey)
        }

        let effects = effectsResolver?(pid, padIdx, pad.effects)
            ?? pad.effects
            ?? .neutral
        // Seamless crossfade for auto-kit loops: prefer the analyzer's
        // per-seam measurement (pad.crossfadeMs) when present; otherwise fall
        // back to the coarse loopScore→ms map. A worse seam → a longer fade;
        // clamped musical 8..30 ms. 0 ⇒ voice pool applies its default floor.
        let crossfadeMs: Double = pad.crossfadeMs.map { max(8.0, min(30.0, $0)) }
            ?? pad.loopScore.map { max(8.0, min(30.0, (1.0 - $0) * 45.0)) }
            ?? 0
        // Continuation audio for the exact-length seam bake is only valid on
        // the UNTRANSFORMED, UNTRIMMED base buffer — a transform/trim output
        // no longer lines up with the decode-time loop-body split, so those
        // fall back to the edge-ramp seam (still exact length).
        let loopBodyFrames = (buffer === baseBuffer)
            ? (entry.loopBodyFrames[padIdx] ?? 0) : 0

        // Shared-cycle lock (web _bakePad, padengine.js:924-931): a pad
        // with a real analyzer region loops over the PACK's common cycle,
        // not its own length — the voice pool tiles the seam-baked body up
        // to this. The cycle is ALSO the loop period the phase join below
        // must divide by: joining mod the un-tiled body put a 1-bar pad at
        // the right sub-bar phase but the wrong bar of the 4-bar cycle.
        // Transform/trim outputs no longer align with the decode-time
        // region, so they keep their own length (same guard as
        // loopBodyFrames above).
        let hasRegion = pad.loopStartSec != nil && pad.loopEndSec != nil
        let bodyFrames = loopBodyFrames > 0
            ? min(loopBodyFrames, Int(buffer.frameLength))
            : Int(buffer.frameLength)
        let loopCycleFrames = (willLoop && buffer === baseBuffer)
            ? Self.sharedCycleFrames(
                bodyFrames: bodyFrames,
                cycleSec: loopLengthSeconds(packId: pid),
                sampleRate: buffer.format.sampleRate,
                hasRegion: hasRegion)
            : 0

        let rate = engine?.clock.rate ?? 1.0
        let nowHost = Self.nowHostSeconds()

        // Free-run re-anchor: song stopped and nothing (other than this
        // pad's own about-to-be-choked voice) sounding or armed →
        // abandon the stale lattice, so a fresh jam's first loop fires
        // immediately at phase 0 instead of waiting up to a bar for a
        // grid nobody can hear. `soundingPadKeys` includes still-audible
        // one-shots — the web keeps `_lockAnchor` while ANY voice sounds
        // (padengine.js:1154 checks `_voices.size`), so a ringing stab
        // must hold the lattice a loop is about to join.
        if willLoopLock, !transportRunning,
           pool.soundingPadKeys.subtracting([padKey]).isEmpty,
           pool.pendingPadKeys.subtracting([padKey]).isEmpty {
            loopLockAnchorHostSec = nil
        }

        // The PRE-shift lattice boundary this launch snapped to, in host
        // seconds. The phase-lock join below must measure from THIS, not
        // from the shifted start time — see phaseJoinSeconds.
        var boundaryHost = nowHost + max(0, TransportTimeMath.scaledDelaySeconds(
            targetSong: targetSong, nowSong: nowSong, rate: rate))
        var freeRunWaitSec = 0.0
        if willLoopLock, !transportRunning {
            // Free-run: the transport clock is frozen, so the lattice
            // lives on the host clock — spacing a single bar (was: the
            // full loop cycle, an up-to-~8 s wait that read as "pad
            // doesn't play"). No anchor yet ⇒ fire now, anchor below.
            boundaryHost = Self.lockGridBoundary(
                after: nowHost, spacing: freeRunLockSpacingSec,
                anchor: loopLockAnchorHostSec ?? nowHost)
            freeRunWaitSec = max(0, boundaryHost - nowHost)
        }

        // Anchor the shared phase lattice at the PRE-shift boundary of
        // the FIRST loop launch: anchoring at the shifted start time
        // would bake that pad's own onset shift into the grid and skew
        // every later join by it. Gated on willLoop, NOT willLoopLock —
        // the web anchors/joins EVERY looping trigger, quantized or not
        // (padengine.js:1166 anchor, :1269-1273 join): an unquantized
        // loop starts NOW (boundary = nowHost) but still mid-body, so it
        // sits at the same musical position as the loops already ringing
        // instead of restarting bar 1 against them.
        if willLoop, loopLockAnchorHostSec == nil {
            loopLockAnchorHostSec = boundaryHost
        }

        // Phase-locked join: a loop tapped mid-jam starts `phase`
        // seconds INTO its body — at any wall time every same-length
        // loop then sits at the same musical position, while its attack
        // still lands on the quantized bar. The first loop (boundary ==
        // anchor) begins at phase 0; one-shots always start at 0. The
        // period is the SHARED CYCLE when the pad tiles to one (web:
        // entry.bodySec IS the cycle after _bakePad) — mod the un-tiled
        // body a short pad joined at the right sub-bar phase but the
        // wrong bar of the cycle.
        var phaseSec = 0.0
        if willLoop, !forceZeroPhase, let anchor = loopLockAnchorHostSec,
           buffer.format.sampleRate > 0 {
            let cycleFrames = max(bodyFrames, loopCycleFrames)
            phaseSec = Self.phaseJoinSeconds(
                boundary: boundaryHost, anchor: anchor,
                bodySec: Double(cycleFrames) / buffer.format.sampleRate)
        }

        let req = SampleTrigger(
            padKey: padKey,
            // A Riley auto-kit pad is "seamlessly loopable" via loopable+loopScore
            // but carries NO loopPointSec, so it was silently one-shot. Honor
            // loopable so it actually loops (with the loopScore crossfade above).
            loop: willLoop,
            chokeGroup: pad.chokeGroup,
            gainDb: pad.gainDb,
            effects: effects,
            crossfadeMs: crossfadeMs,
            loopBodyFrames: loopBodyFrames,
            loopCycleFrames: loopCycleFrames,
            phaseSec: phaseSec
        )
        // Launch compensation for the onset-phase snap: the decode moved
        // the region so its cut sits just before the attack — delay the
        // launch by the same amount so the content's downbeat still lands
        // ON the quantize grid (pads snap by different amounts; without
        // this they armed to the same boundary but sounded offset).
        let launchShift = (willLoop && buffer === baseBuffer)
            ? (entry.loopShiftSec[padIdx] ?? 0) : 0
        let audioTime: AVAudioTime?
        if willLoopLock, !transportRunning {
            // Host-clock wait (song time is frozen; the song-domain
            // conversion below would collapse it to "now").
            let waitSec = freeRunWaitSec + max(0, launchShift)
            audioTime = waitSec > 0.001
                ? AVAudioTime(hostTime: mach_absolute_time()
                    &+ UInt64(waitSec * TransportClock.ticksPerSecond()))
                : nil
        } else {
            audioTime = self.audioTime(
                forSongSeconds: targetSong + max(0, launchShift),
                nowSong: nowSong)
        }
        Diag.padsync("trigger pad=\(padIdx) pack=\(pid.suffix(8)) loop=\(willLoop) lock=\(willLoopLock) transport=\(transportRunning) targetSong=\(String(format: "%.3f", targetSong)) freeRunWait=\(String(format: "%.3f", freeRunWaitSec)) phase=\(String(format: "%.3f", phaseSec)) cycleFrames=\(loopCycleFrames) future=\(audioTime != nil)")
        pool.trigger(req, buffer: buffer, at: audioTime)
        #endif

        onEvent?(LayerEvent(
            kind: .sampleOn,
            songTimeSec: targetSong,
            params: LayerEvent.Params(
                padIdx: padIdx,
                velocity: 1.0,
                packIdOverride: pid
            )
        ))
        return .scheduled(atSongTime: targetSong)
    }

    #if canImport(AVFoundation)
    /// Local-sample trigger. Local pads fire immediately, ignoring
    /// quantize (drum-machine convention); they loop only when the
    /// transform chain contains `.loop` (loopResolver). The only
    /// shared bookkeeping with the pack path is toggle-off and the
    /// section gate.
    private func triggerLocal(
        padIdx: Int, local: (meta: PadSampleMetadata, buffer: AVAudioPCMBuffer)
    ) -> TriggerResult {
        let padKey = SamplePadKey(packId: Self.localPackId, padIdx: padIdx)

        if holdMode == .toggle, pool.isActive(padKey: padKey) {
            // Immediate release, same as pack pads (kit.js latch parity).
            pool.release(padKey: padKey)
            onEvent?(LayerEvent(
                kind: .sampleOff,
                songTimeSec: nowSongSeconds(),
                params: LayerEvent.Params(
                    padIdx: padIdx, packIdOverride: Self.localPackId
                )
            ))
            return .toggledOff
        }

        let nowSong = nowSongSeconds()
        if !SectionResolver.isAllowed(t: nowSong, in: sections, allowed: allowedSections) {
            return .gated
        }

        let buffer = resolvedBuffer(
            local.buffer, packId: Self.localPackId, padIdx: padIdx)

        // Hold mode retrigger: stop any existing voice for this pad before
        // firing a new one (self-choke).
        if holdMode == .hold, pool.isActive(padKey: padKey) {
            pool.release(padKey: padKey)
        }

        let effects = effectsResolver?(Self.localPackId, padIdx, nil) ?? .neutral
        let req = SampleTrigger(
            padKey: padKey,
            loop: loopResolver?(Self.localPackId, padIdx) ?? false,
            chokeGroup: nil,
            gainDb: 0,
            effects: effects
        )
        pool.trigger(req, buffer: buffer, at: nil)

        onEvent?(LayerEvent(
            kind: .sampleOn,
            songTimeSec: nowSong,
            params: LayerEvent.Params(
                padIdx: padIdx,
                velocity: 1.0,
                packIdOverride: Self.localPackId
            )
        ))
        return .scheduled(atSongTime: nowSong)
    }
    #endif

    /// Replay-path trigger: fire the pad's buffer immediately with no
    /// quantize, no section gate, and no `onEvent` emission. Used by
    /// LayerPlayer — the saved timeline already contains the exact
    /// song-times each hit was intended to fire at, so the scheduler
    /// must not re-interpret them. Feeding a replay through the live
    /// `trigger` path caused every event to be snapped to the next
    /// `defaultQuantize` boundary (StarterPack pads have 1/4 or 1/8)
    /// and then to lose its slot to the following event's snap; net
    /// result was a silent replay.
    ///
    /// Also suppresses the `onEvent` callback so a layer being
    /// replayed while another is being recorded doesn't double-record
    /// the same hits into the new take.
    @discardableResult
    public func triggerRaw(
        padIdx: Int, packId: String? = nil, pan: Float = 0
    ) -> TriggerResult {
        #if canImport(AVFoundation)
        // Replayed local pads: best-effort — fires only while the
        // local sample is still assigned at this index.
        if packId == nil || packId == Self.localPackId,
           let local = localBuffers[padIdx] {
            let padKey = SamplePadKey(packId: Self.localPackId, padIdx: padIdx)
            let buffer = resolvedBuffer(
                local.buffer, packId: Self.localPackId, padIdx: padIdx)
            let effects = effectsResolver?(Self.localPackId, padIdx, nil) ?? .neutral
            let req = SampleTrigger(
                padKey: padKey,
                loop: loopResolver?(Self.localPackId, padIdx) ?? false,
                chokeGroup: nil,
                gainDb: 0,
                pan: pan,
                effects: effects
            )
            pool.trigger(req, buffer: buffer, at: nil)
            return .scheduled(atSongTime: nowSongSeconds())
        }
        #endif
        guard let pid = packId ?? activePackId,
              let entry = loadedPacks[pid],
              let pad = entry.pack.pack.pads.first(where: { $0.padIdx == padIdx })
        else { return .padNotFound }

        let padKey = SamplePadKey(packId: pid, padIdx: padIdx)

        #if canImport(AVFoundation)
        guard let baseBuffer = entry.buffers[padIdx] else { return .padNotFound }
        let buffer = resolvedBuffer(baseBuffer, packId: pid, padIdx: padIdx)
        let effects = effectsResolver?(pid, padIdx, pad.effects)
            ?? pad.effects
            ?? .neutral
        // Same exact-length seam threading as the live trigger path: the
        // continuation split only holds for the untransformed base buffer.
        let loopBodyFrames = (buffer === baseBuffer)
            ? (entry.loopBodyFrames[padIdx] ?? 0) : 0
        // Predicate parity with trigger()'s naturalLoop AND web's padengine
        // mayLoop: the loopable flag was omitted here (manifest-loopable
        // borrow pads fired as one-shots through the raw path only), and
        // padLoopOverrides was ignored — so the Tap-mode one-shot force and
        // the radial Loop override had no effect on raw triggers.
        let rawNatural = pad.loopPointSec != nil
            || (pad.loopable ?? false)
            || (loopResolver?(pid, padIdx) ?? false)
        let rawLoop = loopOverride || (padLoopOverrides[padKey] ?? rawNatural)
        // Shared-cycle lock applies on the raw path too: the web tiles at
        // BAKE time (_bakePad), so a replayed/instant-groove loop plays
        // the same cycle-length buffer as a live-triggered one — a raw
        // loop at its own shorter period would drift off live pads.
        let hasRegion = pad.loopStartSec != nil && pad.loopEndSec != nil
        let rawBodyFrames = loopBodyFrames > 0
            ? min(loopBodyFrames, Int(buffer.frameLength))
            : Int(buffer.frameLength)
        let loopCycleFrames = (rawLoop && buffer === baseBuffer)
            ? Self.sharedCycleFrames(
                bodyFrames: rawBodyFrames,
                cycleSec: loopLengthSeconds(packId: pid),
                sampleRate: buffer.format.sampleRate,
                hasRegion: hasRegion)
            : 0
        let req = SampleTrigger(
            padKey: padKey,
            loop: rawLoop,
            chokeGroup: pad.chokeGroup,
            gainDb: pad.gainDb,
            pan: pan,
            effects: effects,
            loopBodyFrames: loopBodyFrames,
            loopCycleFrames: loopCycleFrames
        )
        pool.trigger(req, buffer: buffer, at: nil)
        #endif

        return .scheduled(atSongTime: nowSongSeconds())
    }

    /// Handle a pad touch-up. In `.hold` this stops the pad with the
    /// 20 ms release fade; in `.toggle` it's a no-op (the second tap
    /// stops instead).
    ///
    /// One-shot pads (no `loopPointSec`) intentionally ignore the
    /// release: percussive hits and stabs should play to completion
    /// once triggered, mirroring drum-machine convention ("you can't
    /// un-hit a drum"). The prior behavior interacted badly with
    /// per-pad `defaultQuantize`: a normal ~150 ms tap on a starter
    /// pad with 1/4 quantize (up to 500 ms wait at 120 BPM) would
    /// fire the release fade + player.stop() before the deferred
    /// play() dispatched, leaving the pad silent. Only looping pads
    /// need touch-up-to-release semantics.
    /// `force` bypasses the `padLoops` intrinsic-loop guard: a Jam gate
    /// (Tap/Loop finger-lift) forces a voice to loop via the transient
    /// scheduler-wide `loopOverride`, which is already restored by the
    /// time padUp fires — so the pad's INTRINSIC loop state reads false
    /// and the guard refused to release the still-looping voice (chord/
    /// section pads played their whole clip on finger-lift). A force
    /// release stops whatever is actually sounding on the pad.
    public func release(padIdx: Int, packId: String? = nil, force: Bool = false) {
        #if canImport(AVFoundation)
        // Local pads are one-shots (play to completion, no touch-up
        // release) UNLESS their transform chain contains `.loop` —
        // looping local pads follow the same hold-to-sustain
        // semantics as pack pads with a loop point. Either way,
        // return here so the fallthrough doesn't release the *pack*
        // pad shadowed at the same index.
        if packId == nil || packId == Self.localPackId,
           localBuffers[padIdx] != nil {
            let localKey = SamplePadKey(
                packId: Self.localPackId, padIdx: padIdx
            )
            if holdMode == .hold,
               force || (loopResolver?(Self.localPackId, padIdx) ?? false),
               pool.isActive(padKey: localKey) {
                pool.release(padKey: localKey)
                onEvent?(LayerEvent(
                    kind: .sampleOff,
                    songTimeSec: nowSongSeconds(),
                    params: LayerEvent.Params(
                        padIdx: padIdx,
                        packIdOverride: Self.localPackId
                    )
                ))
            }
            return
        }
        #endif
        guard holdMode == .hold,
              let pid = packId ?? activePackId,
              loadedPacks[pid] != nil
        else { return }
        // Effective loop state (incl. the radial per-pad override and
        // kit `loopable` pads, which loop without a loopPointSec). A
        // forced gate release skips this — the voice is looping via the
        // transient loopOverride, not the pad's intrinsic flags.
        guard force || padLoops(packId: pid, padIdx: padIdx) else { return }
        let padKey = SamplePadKey(packId: pid, padIdx: padIdx)
        if pool.isActive(padKey: padKey) {
            pool.release(padKey: padKey)
            onEvent?(LayerEvent(
                kind: .sampleOff,
                songTimeSec: nowSongSeconds(),
                params: LayerEvent.Params(padIdx: padIdx, packIdOverride: pid)
            ))
        }
    }

    /// Musical release for a Tap-mode held loop: let the CURRENT loop pass
    /// finish, then stop (so a quick tap plays exactly one clean pass and a
    /// hold sustains until the next boundary after finger-up). Ungated by
    /// holdMode/padLoops — the Tap padUp contract already decided this pad
    /// loops; the pool releases a non-looping or still-armed voice
    /// immediately, but Tap only routes looping voices here.
    public func releaseAtLoopEnd(padIdx: Int, packId: String) {
        #if canImport(AVFoundation)
        let key = SamplePadKey(packId: packId, padIdx: padIdx)
        if pool.isActive(padKey: key) {
            pool.releaseAtLoopEnd(padKey: key)
        }
        #endif
    }

    /// Unconditionally stop every active voice for a pad. Unlike
    /// `release`, this ignores hold-mode + loop gating — used when a pad
    /// is deleted/hidden so a looping (or toggle-latched) voice can't
    /// keep ringing after the pad is gone from the grid.
    public func stopVoices(padIdx: Int, packId: String) {
        #if canImport(AVFoundation)
        let key = SamplePadKey(packId: packId, padIdx: padIdx)
        if pool.isActive(padKey: key) {
            pool.release(padKey: key)
        }
        #endif
    }

    // MARK: - Preview with trim bounds

    /// Preview a pad sample with trim bounds. Used by the waveform trimmer
    /// to audition the selected region. Bypasses quantize and contribution
    /// guard — this is a UI preview, not a contribution event.
    #if canImport(AVFoundation)
    public func previewTrimmed(
        padIdx: Int,
        packId: String,
        startFraction: Double,
        endFraction: Double
    ) {
        guard let entry = loadedPacks[packId],
              let baseBuffer = entry.buffers[padIdx]
        else { return }

        let buffer = transformResolver?(baseBuffer, packId, padIdx) ?? baseBuffer
        let pad = entry.pack.pack.pads.first { $0.padIdx == padIdx }
        let effects = effectsResolver?(packId, padIdx, pad?.effects)
            ?? pad?.effects ?? .neutral

        let req = SampleTrigger(
            padKey: SamplePadKey(packId: packId, padIdx: padIdx),
            loop: false,
            chokeGroup: nil,
            gainDb: pad?.gainDb ?? 0,
            effects: effects
        )
        pool.triggerSegment(
            req,
            buffer: buffer,
            startFraction: startFraction,
            endFraction: endFraction
        )
    }
    #endif

    // MARK: - One-shot file playback (sequencer delegate)

    #if canImport(AVFoundation)
    /// Canonical-format buffers for sequencer one-shots, keyed by a
    /// caller-supplied identity (chop ref, sample id, URL). Sliced
    /// stems and whole local samples are loaded + converted once here
    /// and reused across every step that fires them.
    private var oneShotCache: [String: AVAudioPCMBuffer] = [:]

    /// Play a slice of an audio file as a gated one-shot voice. This is
    /// the audio path behind `SequencerPlayerDelegate` — bundleChop /
    /// localSample / customURL sequencer tracks don't route through the
    /// pad bus, so they land here instead. Loads + caches the
    /// canonical-format buffer on first use (slicing `startSec..endSec`
    /// when given), then fires immediately through the voice pool.
    ///
    /// - Parameters:
    ///   - url: source audio file (stem or sample).
    ///   - startSec/endSec: slice window; pass nil/nil to play the whole
    ///     file. loadBuffer clamps `endSec` to the file length.
    ///   - gainDb: base voice gain.
    ///   - velocity: 0–1 step velocity, folded into gain (0 dB at 1.0).
    ///   - pan: stereo pan (-1…+1) applied to the voice.
    ///   - cacheKey: stable identity so repeated triggers reuse the buffer.
    public func triggerFileOneShot(
        url: URL,
        startSec: Double?,
        endSec: Double?,
        gainDb: Double = 0,
        velocity: Float = 1.0,
        pan: Float = 0,
        cacheKey: String
    ) {
        let buffer: AVAudioPCMBuffer
        if let cached = oneShotCache[cacheKey] {
            buffer = cached
        } else {
            let slice: StemSlice? = (startSec != nil || endSec != nil)
                ? StemSlice(
                    stemRole: "",
                    startSec: startSec ?? 0,
                    endSec: endSec ?? .greatestFiniteMagnitude
                )
                : nil
            guard let loaded = Self.loadBuffer(
                from: url, slice: slice, target: engine?.canonicalFormat
            ) else { return }
            oneShotCache[cacheKey] = loaded
            buffer = loaded
        }

        // Velocity → gain trim (0 dB at full velocity, floored at -60).
        let clampedVel = max(0, min(1, velocity))
        let velDb = clampedVel > 0.001 ? 20.0 * log10(Double(clampedVel)) : -60.0
        let padKey = SamplePadKey(
            packId: "__seq__", padIdx: abs(cacheKey.hashValue % 1_000_000)
        )
        let req = SampleTrigger(
            padKey: padKey,
            loop: false,
            chokeGroup: nil,
            gainDb: gainDb + velDb,
            pan: pan,
            effects: .neutral
        )
        pool.trigger(req, buffer: buffer, at: nil)
    }
    #endif

    // MARK: - Private helpers

    private func nowSongSeconds() -> Double {
        engine?.clock.nowSongSeconds ?? 0
    }

    #if canImport(AVFoundation)
    /// Song-time → AVAudioTime translation. Uses mach hostTime so the
    /// player node can honour the schedule even when the engine's
    /// output clock hasn't started (before first render) — the
    /// alternative (`lastRenderTime.sampleTime`) is nil at that point.
    ///
    /// Returns nil when the delay is negligible (< 1 ms), which cues
    /// the pool to schedule immediately with `at: nil`.
    ///
    /// Delay is scaled by the transport rate (D-022 practice speed):
    /// at 0.5x a 1-beat song-time delta spans twice the wall-clock.
    /// Mach host clock in seconds — the phase-lattice time base (the
    /// web engine's `ctx.currentTime` analogue). Monotonic across
    /// transport stop/seek, which song time is not.
    private static func nowHostSeconds() -> Double {
        Double(mach_absolute_time()) / TransportClock.ticksPerSecond()
    }

    private func audioTime(forSongSeconds target: Double, nowSong: Double) -> AVAudioTime? {
        let rate = engine?.clock.rate ?? 1.0
        guard let delayTicks = TransportTimeMath.hostDelayTicks(
            targetSong: target, nowSong: nowSong,
            rate: rate, ticksPerSecond: TransportClock.ticksPerSecond()
        ) else { return nil }
        let hostTime = mach_absolute_time() &+ delayTicks
        return AVAudioTime(hostTime: hostTime)
    }

    /// Target peak level for normalized sample buffers, in linear
    /// amplitude. -3 dBFS = 0.708 keeps a safety margin below full
    /// scale so per-voice EQ + delay AU processing can add a couple
    /// of dB of resonance without clipping.
    ///
    /// A flat pre-amp doesn't work here because the StarterPack files
    /// vary by ~13 dB (shimmer pad at -29.8 dBFS vs snare at -17.3
    /// dBFS); one gain either leaves the pads inaudible or clips the
    /// snare. Normalizing each buffer to a fixed peak on load gives
    /// every pad a consistent, healthy listening level regardless of
    /// how loud the source file was rendered.
    ///
    /// Silence-guard: buffers with no signal (peak < 1e-6) are left
    /// untouched — dividing to reach the target would multiply noise
    /// floor by ~700 000×, producing an audible burst on tap.
    ///
    /// Bumped from 0.708 (-3 dBFS) to 0.891 (-1 dBFS). Peak
    /// normalization is a poor proxy for perceived loudness — the
    /// sub-heavy StarterPack pads (Sub Kick, Bass Hit, Kick, Drone)
    /// sit at the same peak as the brighter shoegaze / lo-fi packs
    /// but sound quieter due to Fletcher-Munson. +2 dB gives the
    /// StarterPack more punch while leaving 1 dB inter-sample
    /// headroom before the mixer chain.
    /// Lowered -1 → -4 dBFS: with every pad normalized to -1 dBFS, stacking
    /// three or four locked loops drove the master PeakLimiter into constant
    /// heavy limiting — audible pumping/grind ("raspy") on dense mixes. -4
    /// leaves ~9 dB of stack headroom before the limiter works hard, at the
    /// cost of a modestly lower per-pad level.
    private nonisolated static let normalizeTargetPeak: Float = 0.63   // -4 dBFS

    /// Normalize `buf` in-place so its peak sample sits at
    /// `normalizeTargetPeak`. Skipped for effectively-silent buffers.
    /// Handles planar-Float32 layout — the default for the canonical
    /// `standardFormat` the contribution graph speaks (D-017).
    private nonisolated static func normalizePeak(_ buf: AVAudioPCMBuffer) {
        guard let channels = buf.floatChannelData else { return }
        let frameCount = Int(buf.frameLength)
        let channelCount = Int(buf.format.channelCount)
        guard frameCount > 0, channelCount > 0 else { return }

        // Scan for peak absolute amplitude across all channels.
        var peak: Float = 0
        for c in 0..<channelCount {
            let ptr = channels[c]
            for i in 0..<frameCount {
                let v = abs(ptr[i])
                if v > peak { peak = v }
            }
        }
        guard peak > 1e-6 else { return }
        // Boost CAP (+12 dB): un-capped normalize dragged whisper-quiet
        // slices — mostly separation bleed — up ~30 dB into audible
        // mush ("fuzzy samples"). Quiet slices stay quiet; attenuation
        // (peak above target) remains uncapped.
        let gain = min(normalizeTargetPeak / peak, 4.0)
        // Skip if the file was already near target — avoids wasting
        // cycles multiplying every sample by ~1.0.
        guard abs(gain - 1.0) > 0.01 else { return }
        for c in 0..<channelCount {
            let ptr = channels[c]
            for i in 0..<frameCount {
                ptr[i] *= gain
            }
        }
    }

    /// Load an audio file into an in-memory PCM buffer, optionally
    /// restricted to `slice` (start/end seconds), optionally converted
    /// to `target` so it matches the voice-pool's connection format
    /// (see SampleVoicePool.attach). Returns nil on any I/O or format
    /// error — the caller handles missing buffers as a silent-pad case
    /// rather than a hard failure.
    private nonisolated static func loadBuffer(
        from url: URL,
        slice: StemSlice?,
        target: AVAudioFormat?
    ) -> AVAudioPCMBuffer? {
        loadBufferWithContinuation(
            from: url, slice: slice, target: target, continuationSec: 0
        )?.buffer
    }

    /// `loadBuffer` core, extended for the exact-length loop seam: when
    /// `continuationSec > 0` and the source has audio past the slice's end,
    /// up to that much extra is read onto the buffer's tail. The returned
    /// `continuationFrames` is that extra length in OUTPUT (post-convert)
    /// frames — the loop body is `frameLength - continuationFrames`, and
    /// SeamlessLoop.exactCrossfaded bakes the seam from it without ever
    /// shortening the loop period. 0 continuation = plain region load.
    private nonisolated static func loadBufferWithContinuation(
        from url: URL,
        slice: StemSlice?,
        target: AVAudioFormat?,
        continuationSec: Double
    ) -> (buffer: AVAudioPCMBuffer, continuationFrames: Int, shiftSec: Double)? {
        do {
            let file = try AVAudioFile(forReading: url)
            let format = file.processingFormat
            let sampleRate = format.sampleRate

            var startFrame: AVAudioFramePosition
            let bodyCount: AVAudioFrameCount
            var extraCount: AVAudioFrameCount = 0
            var shiftSec: Double = 0
            if let slice = slice {
                startFrame = AVAudioFramePosition(max(0, slice.startSec) * sampleRate)
                // Body length from the region LENGTH with round — NOT
                // trunc-per-edge (trunc(end·sr) − trunc(start·sr)), which
                // lands on N or N+1 frames depending on each edge's
                // fractional phase. Loop periods are compared/tiled against
                // round(length·sr) elsewhere (SRC bodyOut below, cycle
                // bar-snap); a pad whose body trunc'd one frame long loops
                // ~23 µs/cycle behind every pad at N — phase-locked loops
                // audibly walk apart over minutes.
                let lengthSec = max(0, slice.endSec - slice.startSec)
                let requested = AVAudioFramePosition((lengthSec * sampleRate).rounded())
                let clipped = min(requested, max(0, file.length - startFrame))
                bodyCount = AVAudioFrameCount(clipped)
                if continuationSec > 0, sampleRate > 0 {
                    // Onset-phase snap (loop regions only): the grid's
                    // downbeat timestamps land tens of ms AFTER the audible
                    // attack, so a grid-cut region starts just past its own
                    // kick — the wrap plays tail → kickless head, an audible
                    // pause even with an exact period. Shift BOTH edges
                    // (period kept) so the cut sits ~5 ms before the
                    // strongest nearby onset; sustained heads shift 0.
                    let search = AVAudioFramePosition((0.060 * sampleRate).rounded())
                    let lo = max(0, startFrame - search)
                    let scanLen = AVAudioFrameCount(
                        max(0, min(file.length - lo, (startFrame - lo) + search)))
                    if scanLen > 0,
                       let scan = AVAudioPCMBuffer(pcmFormat: format,
                                                   frameCapacity: scanLen) {
                        file.framePosition = lo
                        try file.read(into: scan, frameCount: scanLen)
                        let shift = SeamlessLoop.onsetAlignedShift(
                            scan, centerFrame: Int(startFrame - lo),
                            searchFrames: Int(search),
                            prerollFrames: Int(0.005 * sampleRate))
                        let s2 = startFrame + AVAudioFramePosition(shift)
                        if s2 >= 0,
                           s2 + AVAudioFramePosition(bodyCount) <= file.length {
                            startFrame = s2
                            shiftSec = Double(shift) / sampleRate
                        }
                    }
                    let want = AVAudioFramePosition(continuationSec * sampleRate)
                    let avail = max(0, file.length - startFrame
                                       - AVAudioFramePosition(bodyCount))
                    extraCount = AVAudioFrameCount(min(want, avail))
                }
            } else {
                startFrame = 0
                bodyCount = AVAudioFrameCount(file.length)
            }
            let frameCount = bodyCount + extraCount
            guard frameCount > 0,
                  let srcBuf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount)
            else { return nil }

            file.framePosition = startFrame
            try file.read(into: srcBuf, frameCount: frameCount)

            // No target ⇒ tests / unwired boot — hand back the raw
            // buffer. On-device we always have a target and any file
            // whose format differs is converted below.
            guard let target = target, !format.isEqual(target) else {
                normalizePeak(srcBuf)
                SeamlessLoop.applyEdgeFades(srcBuf)
                return (srcBuf, max(0, Int(srcBuf.frameLength) - Int(bodyCount)),
                        shiftSec)
            }
            guard let dstBuf = convert(srcBuf, to: target) else { return nil }
            normalizePeak(dstBuf)
            // Micro-fade the slice edges so one-shots/stabs don't click on
            // attack or tail (grid-boundary slices aren't zero-crossings);
            // shorter than any loop crossfade, so seam quality is unaffected.
            // (With continuation the release fade lands on the continuation
            // tail, which the seam bake only uses at near-zero gain.)
            SeamlessLoop.applyEdgeFades(dstBuf)
            // Loop body length in the CONVERTED domain: SRC scales frame
            // counts, so recompute from the ratio rather than trusting the
            // converter's exact output length (±a frame of jitter lands in
            // the continuation, never in the body).
            let ratio = target.sampleRate / sampleRate
            let bodyOut = min(Int(dstBuf.frameLength),
                              Int((Double(bodyCount) * ratio).rounded()))
            return (dstBuf, Int(dstBuf.frameLength) - bodyOut, shiftSec)
        } catch {
            return nil
        }
    }

    /// One-shot whole-buffer format conversion (rate + channel
    /// layout). Best SRC quality — this is the single resample point
    /// of the contribution path (D-017), it runs off the audio
    /// thread, once per pack load / local assignment, so the CPU
    /// cost is irrelevant next to the fidelity win.
    private nonisolated static func convert(
        _ srcBuf: AVAudioPCMBuffer, to target: AVAudioFormat
    ) -> AVAudioPCMBuffer? {
        guard let converter = AVAudioConverter(from: srcBuf.format, to: target) else {
            return nil
        }
        converter.sampleRateConverterQuality = AVAudioQuality.max.rawValue
        let ratio = target.sampleRate / srcBuf.format.sampleRate
        // +32 frames of slack for the converter's internal state,
        // same margin the offline renderer uses.
        let outCapacity = AVAudioFrameCount(Double(srcBuf.frameLength) * ratio) + 32
        guard let dstBuf = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: outCapacity)
        else { return nil }
        var provided = false
        var convError: NSError?
        _ = converter.convert(to: dstBuf, error: &convError) { _, outStatus in
            if provided {
                outStatus.pointee = .endOfStream
                return nil
            }
            provided = true
            outStatus.pointee = .haveData
            return srcBuf
        }
        if convError != nil { return nil }
        return dstBuf
    }
    #endif
}
