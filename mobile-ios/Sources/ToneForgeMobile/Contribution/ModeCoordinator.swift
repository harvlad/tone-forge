// ModeCoordinator.swift
//
// The contribution engine's single executor. Subscribes to the
// ContributionEventBus, routes every event through ModeRouter, and
// executes the resulting AudioAction against SampleScheduler /
// WavetableSynthNode. NOTHING else may reach those executors (legacy
// LayerPlayer.triggerRaw replay is the one documented exception,
// D-015) — SampleScheduler's `contributionGuard` assertion enforces
// this in debug builds.
//
// Also owns everything the 8×8 grid needs to paint:
//   - appMode        — persisted via SampleSettingsStore.appModeRaw
//   - padVisuals     — 64 precomputed PadVisuals from the mode layout
//   - pressedPads    — lit pads, updated from the BUS (not the touch
//                      overlay) so hardware presses (P2) light the
//                      on-screen grid identically
//
// Grid context (D-016): a loaded bundle supplies the analysed
// beats/sections + persisted section gates; no bundle degrades to the
// sketch settings' synthetic tempo grid — "sketch" is just sample
// mode without a song.
//
// Pack pads occupy the TOP-LEFT 4×4 quadrant of the 8×8 grid (rows
// 8..5 top-down, cols 1..4) in both implemented modes; in hybrid the
// bottom half (rows 1–4) is the chromatic note surface, so the sample
// quadrant is identical across the two modes and muscle memory holds.

import AVFoundation
import Combine
import Foundation
import SwiftUI
import ToneForgeEngine

/// Sheet target for the pad-effects editor, produced by a long-press
/// on a bound sample pad. `Identifiable` so `.sheet(item:)` presents
/// a fresh editor per long-press.
struct PadEffectsTarget: Identifiable {
    let id = UUID()
    let packId: String
    let padIdx: Int
    let padName: String
    let manifestBaseline: SamplePadEffects?
    /// Grid position (PadIndex convention) so the editor's Preview
    /// button can re-fire the pad through the contribution bus.
    let gridRow: Int
    let gridCol: Int
}

/// Sheet target for the pad SOURCE sheet (P3): record a mic sample
/// onto an empty pad, assign an existing local sample, or manage the
/// one already assigned (classify override, remove, delete).
struct PadSourceTarget: Identifiable {
    let id = UUID()
    let gridRow: Int
    let gridCol: Int
    /// Non-nil when the pad already carries a local sample.
    let sample: PadSampleMetadata?

    var gridRaw: Int { gridRow * 10 + gridCol }
}

/// Union of the two long-press sheets — `.sheet(item:)` needs one
/// Identifiable value.
enum PadSheetTarget: Identifiable {
    case effects(PadEffectsTarget)
    case source(PadSourceTarget)

    var id: UUID {
        switch self {
        case .effects(let t): return t.id
        case .source(let t):  return t.id
        }
    }
}

@MainActor
public final class ModeCoordinator: ObservableObject {

    // MARK: - Published grid state

    /// Current contribution mode. Set via `setMode` (persists +
    /// rebuilds the layout).
    @Published public private(set) var appMode: AppMode
    /// Precomputed visuals for the 8×8 grid, indexed
    /// `(row-1)*8 + (col-1)` (PadIndex convention, row 1 = bottom).
    @Published public private(set) var padVisuals: [PadVisual]
    /// PadIndex rawValues currently held down — fed from the BUS so
    /// every input surface lights the on-screen grid.
    @Published public private(set) var pressedPads: Set<Int> = []

    // MARK: - Private

    /// Unowned: AppState owns the coordinator; identical lifetimes.
    private unowned let app: AppState

    /// The immutable layout snapshot both ModeRouter and the painter
    /// consume. Rebuilt on mode/pack/song/chord change.
    private var layout: any GridLayoutProviding = EmptyLayout()

    /// PadIndex.rawValue → (packId, padIdx-within-pack) for the bound
    /// sample quadrant. Unbound grid pads no-op.
    private var padBindings: [Int: (packId: String, padIdx: Int)] = [:]

    /// Transient session-replay overlay (P6, D-015): the recorded
    /// session's padMapping resolved to scheduler keys. Consulted
    /// ONLY for `isReplay` events, so a loaded session plays through
    /// the pads it was recorded with even after the user re-binds the
    /// live grid. Cleared when replay stops.
    private var replayBindings: [Int: (packId: String, padIdx: Int)] = [:]

    /// True only while this coordinator is executing a routed
    /// AudioAction — the scheduler's contributionGuard reads it.
    private var isExecuting = false

    private var busToken: ContributionEventBus.Token?

    /// P4: owns rendered transform buffers + loop flags; fills the
    /// scheduler's transformResolver/loopResolver seams.
    let transformHost = PadTransformHost()

    public init(app: AppState) {
        self.app = app
        self.appMode = AppMode(rawValue: app.sampleSettings.appModeRaw) ?? .sample
        self.padVisuals = Array(repeating: .off, count: 64)
        // Build an initial layout from whatever state exists so a
        // never-booted AppState (snapshot tests assign currentBundle
        // directly) still paints a correct grid.
        rebuildLayout()
    }

    // MARK: - Lifecycle

    /// Wire the bus subscription + scheduler guard and push the grid
    /// context. Called once from `AppState.bootAudio` after the audio
    /// graph is up. Idempotent.
    public func start() {
        guard busToken == nil else { return }
        busToken = app.contributionBus.subscribe { [weak self] event in
            self?.handle(event)
        }
        app.sampleScheduler.contributionGuard = { [weak self] in
            self?.isExecuting ?? true
        }
        #if canImport(AVFoundation)
        app.sampleScheduler.transformResolver = { [weak self] base, packId, padIdx in
            self?.transformHost.resolve(
                base: base, packId: packId, padIdx: padIdx
            ) ?? base
        }
        #endif
        app.sampleScheduler.loopResolver = { [weak self] packId, padIdx in
            self?.transformHost.loops(packId: packId, padIdx: padIdx) ?? false
        }
        applyGridContext()
        syncLocalBuffers()
        syncTransforms()
        rebuildLayout()
    }

    // MARK: - Mode

    /// Switch modes. Unimplemented modes are ignored (the menu
    /// disables them, this is the backstop). Persists to the settings
    /// blob and silences held synth notes so nothing rings across the
    /// mode change.
    public func setMode(_ mode: AppMode) {
        guard mode.isImplemented, mode != appMode else { return }
        appMode = mode
        app.sampleSettings.appModeRaw = mode.rawValue
        app.wavetableSynthNode.allNotesOff()
        // Local assignments are per-mode but scheduler buffers are
        // keyed by grid pad alone — swap them with the mode. Same
        // for armed transform renders.
        syncLocalBuffers()
        syncTransforms()
        rebuildLayout()
    }

    // MARK: - Touch input (on-screen grid adapter)

    /// On-screen grid touch-down. Stamps hostTime BEFORE publishing so
    /// the LatencyProbe (P7) measures the true touch→attack path.
    public func touchPadDown(row: Int, col: Int) {
        app.contributionBus.publish(ContributionEvent(
            source: .touch,
            kind: .padDown(row: row, col: col),
            timestamp: app.audioEngine.clock.nowSongSeconds,
            hostTime: mach_absolute_time()
        ))
    }

    /// On-screen grid touch-up.
    public func touchPadUp(row: Int, col: Int) {
        app.contributionBus.publish(ContributionEvent(
            source: .touch,
            kind: .padUp(row: row, col: col),
            timestamp: app.audioEngine.clock.nowSongSeconds,
            hostTime: mach_absolute_time()
        ))
    }

    // MARK: - Bus handling + execution

    private func handle(_ event: ContributionEvent) {
        // Pressed-pad mirror for the grid painter — every source,
        // including replays, lights the pads it plays.
        switch event.kind {
        case .padDown(let row, let col):
            let pad = PadIndex.at(row: row, col: col)
            if pad.isValid { pressedPads.insert(pad.rawValue) }
        case .padUp(let row, let col):
            let pad = PadIndex.at(row: row, col: col)
            if pad.isValid { pressedPads.remove(pad.rawValue) }
        case .midiNote, .gap:
            break
        }

        let action = ModeRouter.resolve(event, mode: appMode, layout: layout)
        execute(action, for: event)
    }

    private func execute(_ action: AudioAction, for event: ContributionEvent) {
        // Sketch-record count-in: while the transport runs the
        // negative lead bar, live input is suppressed entirely so
        // nothing sounds or gets captured before songTime 0. Replays
        // pass through (a saved layer playing back is not the user
        // jumping the gun).
        if app.isCountingIn, !event.isReplay { return }

        isExecuting = true
        defer { isExecuting = false }

        switch action {
        case .triggerSample(let raw):
            guard let binding = binding(for: raw, isReplay: event.isReplay)
            else { return }
            if event.isReplay {
                // Replays bypass quantize/section gates — the live
                // trigger would re-snap already-snapped timestamps
                // (or gate them out entirely), the exact silent-
                // replay bug LayerPlayer hit (see makeLayerPlayer).
                _ = app.sampleScheduler.triggerRaw(
                    padIdx: binding.padIdx, packId: binding.packId
                )
            } else {
                app.sampleScheduler.trigger(
                    padIdx: binding.padIdx, packId: binding.packId
                )
            }

        case .releaseSample(let raw):
            guard let binding = binding(for: raw, isReplay: event.isReplay)
            else { return }
            app.sampleScheduler.release(
                padIdx: binding.padIdx, packId: binding.packId
            )

        case .synthNoteOn(let midi, let velocity, let isChordTone):
            // Chord tones get a small accent so the bright pads sound
            // bright too.
            let vel = min(1.0, velocity + (isChordTone ? 0.15 : 0))
            app.wavetableSynthNode.noteOn(midi: midi, velocity: vel)

        case .synthNoteOff(let midi):
            app.wavetableSynthNode.noteOff(midi: midi)

        case .none:
            break
        }
    }

    /// Scheduler key for a routed pad event. Live events use the
    /// live grid bindings; replay events prefer the session overlay
    /// and fall back to the live binding (a session saved before its
    /// overlay concept existed, or a mapping entry that failed to
    /// restore, still plays whatever the grid holds now).
    private func binding(
        for raw: Int, isReplay: Bool
    ) -> (packId: String, padIdx: Int)? {
        isReplay ? (replayBindings[raw] ?? padBindings[raw]) : padBindings[raw]
    }

    // MARK: - Session capture + replay (P6, D-015)

    /// Snapshot of the current grid → sample bindings in SessionCapture
    /// terms, taken by the recorder at arm time. Pack pads reference
    /// (packId, padIdx); local pads resolve back to their sample UUID
    /// via the assignment store so the mapping survives re-binds.
    public func currentPadMapping() -> [PadAddress: PadSampleReference] {
        var mapping: [PadAddress: PadSampleReference] = [:]
        for (raw, binding) in padBindings {
            let pad = PadIndex(raw)
            guard pad.isValid else { continue }
            let addr = PadAddress(mode: appMode, pad: pad)
            if binding.packId == SampleScheduler.localPackId {
                guard case .localSample(let id)? = app.padAssignmentStore
                    .slot(mode: appMode, padIdx: raw)?.ref
                else { continue }
                mapping[addr] = .localSample(id: id)
            } else {
                mapping[addr] = .packPad(
                    packId: binding.packId, padIdx: binding.padIdx
                )
            }
        }
        return mapping
    }

    /// Install a loaded session's padMapping as the replay overlay.
    /// Pack pads map straight to scheduler keys (the caller preloads
    /// their packs); local samples decode from the store into the
    /// scheduler's local-buffer table under their grid index —
    /// samples deleted since the recording are skipped (their events
    /// fall back to whatever the live grid holds).
    public func applyReplayOverlay(_ session: SessionCapture) {
        replayBindings = [:]
        for (addr, ref) in session.padMapping where addr.mode == session.appMode {
            let raw = addr.pad.rawValue
            switch ref {
            case .packPad(let packId, let padIdx):
                replayBindings[raw] = (packId: packId, padIdx: padIdx)
            case .localSample(let id):
                guard let meta = app.padSampleStore.metadata(id: id)
                else { continue }
                replayBindings[raw] = (
                    packId: SampleScheduler.localPackId, padIdx: raw
                )
                Task { [weak self] in
                    guard let self,
                          let buffer = try? await self.app.padSampleStore
                              .loadBuffer(id: id),
                          self.replayBindings[raw]?.packId
                              == SampleScheduler.localPackId
                    else { return }
                    self.app.sampleScheduler.setLocalBuffer(
                        buffer, meta: meta, for: raw
                    )
                }
            }
        }
    }

    /// Drop the replay overlay and restore the LIVE mode's local
    /// buffers (the overlay may have loaded session samples into
    /// grid slots the live assignments don't own).
    public func clearReplayOverlay() {
        guard !replayBindings.isEmpty else { return }
        replayBindings = [:]
        syncLocalBuffers()
    }

    // MARK: - Grid context (D-016)

    /// Push the quantize/section context matching the loaded-song
    /// state into the scheduler. Bundle loaded → analysed grid +
    /// persisted gates; no bundle → sketch settings' synthetic tempo
    /// grid (sketch = sample mode without a song).
    public func applyGridContext() {
        if let bundle = app.currentBundle {
            app.sampleScheduler.quantize = app.sampleSettings.quantizeMode
            app.sampleScheduler.updateBundle(
                timeline: bundle.timeline, meta: bundle.meta
            )
            app.sampleScheduler.allowedSections =
                app.sampleSettings.sectionGates(for: bundle.analysisId)
        } else {
            app.sampleScheduler.quantize = app.sketchSettings.quantizeMode
            app.sampleScheduler.updateSyntheticContext(
                tempoBpm: app.sketchSettings.tempoBpm
            )
            app.sampleScheduler.allowedSections = nil
        }
    }

    // MARK: - Layout

    /// Rebuild the layout snapshot + pad bindings + visuals. Call on
    /// mode change, pack activation, and bundle load/clear. Also
    /// re-arms transform renders: pack activation makes new base
    /// buffers resident and bundle load/clear changes the tempo the
    /// synced transforms (stutter/gate) rendered against.
    public func refreshLayout() {
        rebuildLayout()
        syncTransforms()
    }

    /// Chord advanced (hybrid brightens the sounding chord's tones).
    /// Cheap no-op in sample mode.
    public func chordChanged() {
        guard appMode == .hybrid else { return }
        rebuildLayout()
    }

    private func rebuildLayout() {
        let content = sampleQuadrantContent()
        switch appMode {
        case .sample:
            layout = SampleModeLayout(content: content)
        case .hybrid:
            layout = HybridModeLayout(
                keyLabel: app.currentBundle?.meta.detectedKey,
                chordPitchClasses: currentChordPitchClasses(),
                sampleContent: content
            )
        default:
            layout = EmptyLayout()
        }
        var visuals = [PadVisual](repeating: .off, count: 64)
        for row in 1...8 {
            for col in 1...8 {
                visuals[(row - 1) * 8 + (col - 1)] =
                    layout.visual(at: PadIndex.at(row: row, col: col))
            }
        }
        padVisuals = visuals
    }

    /// Grid pads (PadIndex rawValues) whose bound sample pad has a
    /// ringing looping voice — drives the on-screen "still sounding"
    /// outline. Only the active pack's quadrant is bound, so ringing
    /// voices from *other* packs don't light the grid; the Play tab's
    /// stop-all button covers those.
    func ringingGridPads(from keys: Set<SamplePadKey>) -> Set<Int> {
        guard !keys.isEmpty else { return [] }
        var out: Set<Int> = []
        for (raw, binding) in padBindings
        where keys.contains(
            SamplePadKey(packId: binding.packId, padIdx: binding.padIdx)
        ) {
            out.insert(raw)
        }
        return out
    }

    /// Active pack's 16 pads mapped into the top-left 4×4 quadrant:
    /// pack padIdx p (row-major from the pack's top row) → grid
    /// row 8 - p/4, col p%4 + 1. Local sample assignments (P3) then
    /// overlay the pack content — matching the audio path, where a
    /// local buffer shadows the pack pad at the same grid index.
    /// Also refreshes `padBindings`.
    private func sampleQuadrantContent() -> [Int: PadContent] {
        padBindings = [:]
        var content: [Int: PadContent] = [:]
        if let active = app.activeSamplePack {
            let packId = active.pack.packId
            for pad in active.pack.pads where (0..<16).contains(pad.padIdx) {
                let grid = PadIndex.at(
                    row: 8 - pad.padIdx / 4,
                    col: pad.padIdx % 4 + 1
                )
                padBindings[grid.rawValue] = (packId: packId, padIdx: pad.padIdx)
                content[grid.rawValue] = PadContent(
                    label: pad.name,
                    colorHint: Self.familyColor(pad.family),
                    loops: pad.loopPointSec != nil
                )
            }
        }
        for (gridRaw, slot) in app.padAssignmentStore.assignments(for: appMode) {
            switch slot.ref {
            case .localSample(let id):
                guard let meta = app.padSampleStore.metadata(id: id)
                else { continue }
                // Local pads bind to the scheduler's synthetic pack
                // under their GRID index — the same key
                // setLocalBuffer used.
                padBindings[gridRaw] = (
                    packId: SampleScheduler.localPackId, padIdx: gridRaw
                )
                content[gridRaw] = PadContent(
                    label: Self.classLabel(meta.effectiveClass),
                    colorHint: meta.colorHint != 0
                        ? meta.colorHint
                        : Self.localColor(meta.source),
                    badge: Self.transformBadge(slot.transforms)
                        ?? Self.localBadge(meta.source),
                    loops: slot.transforms.contains(.loop)
                )

            case .packPad(let packId, let padIdx):
                // Pack-pad slots exist only to carry a transform
                // chain (P4). Overlay the badge on the pack's own
                // content — but only while that pack is actually the
                // one bound at this grid position.
                guard let badge = Self.transformBadge(slot.transforms),
                      let binding = padBindings[gridRaw],
                      binding.packId == packId,
                      binding.padIdx == padIdx,
                      var existing = content[gridRaw]
                else { continue }
                existing.badge = badge
                existing.loops =
                    existing.loops || slot.transforms.contains(.loop)
                content[gridRaw] = existing
            }
        }
        return content
    }

    /// Grid badge for a pad carrying a transform chain: `.loop` when
    /// the chain loops, `.transformed` otherwise, nil for no chain.
    static func transformBadge(_ chain: [PadTransform]) -> PadBadge? {
        guard !chain.isEmpty else { return nil }
        return chain.contains(.loop) ? .loop : .transformed
    }

    /// Provenance color (0xRRGGBB) for local samples whose metadata
    /// carries no colorHint. Mic = warm orange, vocoded = purple.
    static func localColor(_ source: PadSampleMetadata.Source) -> UInt32 {
        switch source {
        case .mic:      return 0xFF8C3A
        case .vocoded:  return 0x9B4DFF
        case .songChop: return 0x9CA3AF
        }
    }

    static func localBadge(_ source: PadSampleMetadata.Source) -> PadBadge {
        switch source {
        case .mic:      return .mic
        case .vocoded:  return .vocoded
        case .songChop: return .transformed
        }
    }

    /// Short pad label for a classified local sample.
    static func classLabel(_ cls: SampleClass) -> String {
        switch cls {
        case .vocalChop:     return "Vocal"
        case .percussion:    return "Perc"
        case .sustainedNote: return "Note"
        case .texture:       return "Texture"
        case .phrase:        return "Phrase"
        case .speechWord:    return "Word"
        case .unknown:       return "Sample"
        }
    }

    /// Family → 0xRRGGBB grid color. Pack manifests carry a *named*
    /// colorHint string ("purple") aimed at the web UI; the grid keys
    /// off family instead so all packs get a consistent palette.
    static func familyColor(_ family: SampleFamily) -> UInt32 {
        switch family {
        case .pads:       return 0xA855F7
        case .percussion: return 0xF97316
        case .textures:   return 0x14B8A6
        case .stabs:      return 0xEC4899
        case .bass:       return 0x3B82F6
        case .fx:         return 0xEAB308
        case .vocals:     return 0x22C55E
        case .mixed:      return 0x9CA3AF
        }
    }

    /// Pitch classes of the currently sounding chord — hybrid mode
    /// brightens these pads.
    private func currentChordPitchClasses() -> Set<Int> {
        guard let sym = app.currentChord?.symbol else { return [] }
        return Self.pitchClasses(for: sym)
    }

    /// Chord symbol → chord-tone pitch classes. Delegates to the
    /// engine's shared voicing table (redesign Phase 4) so grid
    /// highlights, Jam pads and Chord Pads all agree on chord
    /// spelling. Richer than the old inline table for maj7/min7
    /// (adds the 7th) and sus (4th instead of 3rd); identical for
    /// maj/min/dom7/dim/aug/other. Empty for unparseable symbols.
    static func pitchClasses(for symbol: String) -> Set<Int> {
        ChordVoicing.pitchClassSet(symbol: symbol)
    }

    // MARK: - Local samples (P3 mic pipeline)

    public enum MicCaptureError: Error, LocalizedError {
        /// The conditioner trimmed the whole take away (silence).
        case silentCapture

        public var errorDescription: String? {
            switch self {
            case .silentCapture:
                return "Nothing was picked up — try recording closer to the mic."
            }
        }
    }

    /// Load every local assignment for the current mode into the
    /// scheduler's local-buffer table. Buffers are keyed by grid pad
    /// alone (not per-mode), so this re-runs on every mode change.
    public func syncLocalBuffers() {
        app.sampleScheduler.clearAllLocalBuffers()
        for (gridRaw, slot) in app.padAssignmentStore.assignments(for: appMode) {
            guard case .localSample(let id) = slot.ref else { continue }
            loadLocalBuffer(id: id, gridRaw: gridRaw)
        }
    }

    /// Mic capture → conditioned → classified → saved → assigned →
    /// playable. Called by the pad source sheet with the finished
    /// 48 kHz mono capture; returns the saved metadata so the sheet
    /// can show the verdict (class + confidence).
    @discardableResult
    public func saveMicCapture(
        _ capture: [Float], toGridPad gridRaw: Int
    ) async throws -> PadSampleMetadata {
        let rate = AudioEngine.canonicalSampleRate
        let processed = RecordingProcessor.process(capture, sampleRate: rate)
        guard !processed.samples.isEmpty else {
            throw MicCaptureError.silentCapture
        }
        let (cls, confidence) = HeuristicClassifier().classify(
            samples: processed.samples, sampleRate: rate
        )
        let meta = try await app.padSampleStore.save(
            samples: processed.samples,
            sampleRate: rate,
            metadata: PadSampleMetadata(
                source: .mic,
                classification: cls,
                confidence: confidence,
                durationSec: 0,   // authoritative values filled by the
                sampleRate: 0,    // store from the payload itself
                channels: 1,
                colorHint: Self.localColor(.mic)
            )
        )
        assignLocalSample(id: meta.id, toGridPad: gridRaw)
        return meta
    }

    /// Point a grid pad at an existing local sample (persists, wires
    /// the scheduler, repaints).
    public func assignLocalSample(id: UUID, toGridPad gridRaw: Int) {
        app.padAssignmentStore.assign(
            PadSlot(ref: .localSample(id: id)), mode: appMode, padIdx: gridRaw
        )
        loadLocalBuffer(id: id, gridRaw: gridRaw)
        rebuildLayout()
    }

    /// Un-assign a pad (the sample stays in the store; the pad falls
    /// back to the pack layout).
    public func clearLocalAssignment(gridPad gridRaw: Int) {
        app.padAssignmentStore.assign(nil, mode: appMode, padIdx: gridRaw)
        app.sampleScheduler.clearLocalBuffer(for: gridRaw)
        clearHostedTransforms(
            packId: SampleScheduler.localPackId, padIdx: gridRaw
        )
        rebuildLayout()
    }

    /// Delete a sample everywhere: disk, every mode's assignments,
    /// and any live scheduler buffer in the current mode.
    public func deleteLocalSample(id: UUID) {
        for (gridRaw, slot) in app.padAssignmentStore.assignments(for: appMode)
        where slot.ref == .localSample(id: id) {
            app.sampleScheduler.clearLocalBuffer(for: gridRaw)
            clearHostedTransforms(
                packId: SampleScheduler.localPackId, padIdx: gridRaw
            )
        }
        app.padAssignmentStore.removeAll(referencing: id)
        app.padSampleStore.delete(id: id)
        rebuildLayout()
    }

    /// User classify-override from the pad sheet. nil = trust the
    /// classifier again.
    public func setClassOverride(_ cls: SampleClass?, sampleId: UUID) {
        guard var meta = app.padSampleStore.metadata(id: sampleId) else { return }
        meta.userClassOverride = cls
        try? app.padSampleStore.updateMetadata(meta)
        rebuildLayout()
    }

    /// Async WAV decode → scheduler ingest. Re-checks the assignment
    /// after the load so a clear-during-load can't resurrect the pad.
    /// Once the buffer is resident, any persisted transform chain is
    /// (re-)rendered against it — at syncTransforms time the base may
    /// not have been loaded yet.
    private func loadLocalBuffer(id: UUID, gridRaw: Int) {
        guard let meta = app.padSampleStore.metadata(id: id) else { return }
        Task { [weak self] in
            guard let self,
                  let buffer = try? await self.app.padSampleStore.loadBuffer(id: id),
                  let slot = self.app.padAssignmentStore
                      .slot(mode: self.appMode, padIdx: gridRaw),
                  slot.ref == .localSample(id: id)
            else { return }
            self.app.sampleScheduler.setLocalBuffer(buffer, meta: meta, for: gridRaw)
            if !slot.transforms.isEmpty {
                self.renderTransforms(slot: slot, gridRaw: gridRaw)
            }
        }
    }

    // MARK: - Vocoder capture (P5)

    /// Finished vocoder take → conditioned → classified → saved
    /// (source .vocoded, purple, neverUpload enforced by the metadata
    /// init) → assigned → playable. Mirrors `saveMicCapture` but
    /// conditions the PROCESSED audio and stamps the mode used.
    @discardableResult
    public func saveVocoderTake(
        _ take: VocoderCaptureSession.Take, toGridPad gridRaw: Int
    ) async throws -> PadSampleMetadata {
        let rate = AudioEngine.canonicalSampleRate
        let processed = RecordingProcessor.process(
            take.processed, sampleRate: rate
        )
        guard !processed.samples.isEmpty else {
            throw MicCaptureError.silentCapture
        }
        let (cls, confidence) = HeuristicClassifier().classify(
            samples: processed.samples, sampleRate: rate
        )
        // Song-derived carriers (chord grid / stem) note their song;
        // the take is mic audio either way → never uploaded.
        let songId: String?
        switch take.mode {
        case .song, .stem:
            songId = app.currentBundle?.analysisId
        case .classic, .harmony, .texture:
            songId = nil
        }
        let meta = try await app.padSampleStore.save(
            samples: processed.samples,
            sampleRate: rate,
            metadata: PadSampleMetadata(
                source: .vocoded,
                classification: cls,
                confidence: confidence,
                durationSec: 0,   // authoritative values filled by the
                sampleRate: 0,    // store from the payload itself
                channels: 1,
                colorHint: Self.localColor(.vocoded),
                vocoderMode: take.mode.rawValue,
                sourceSongId: songId
            )
        )
        assignLocalSample(id: meta.id, toGridPad: gridRaw)
        return meta
    }

    /// Build the carrier program for one capture. Deterministic given
    /// the app state at arm time (loaded song, transport position,
    /// active pack); every missing audio source degrades one step —
    /// stem → chord grid → drone — so a capture always sounds.
    public func vocoderProgram(for mode: VocoderMode) async -> VocoderProgram {
        let rate = AudioEngine.canonicalSampleRate
        let dur = VocoderCaptureSession.maxDurationSec
        switch mode {
        case .classic:
            // Current chord voiced low (C3 octave); no chord → the
            // builder's own drone fallback.
            let notes = currentChordPitchClasses().sorted().map { 48 + $0 }
            return VocoderProgram(
                mode: .classic,
                carrier: VocoderCarriers.sawStack(
                    notes: notes, durationSec: dur, sampleRate: rate
                )
            )

        case .song:
            return VocoderProgram(
                mode: .song,
                carrier: VocoderCarriers.chordGrid(
                    spans: chordSpans(midiBase: 48),
                    durationSec: dur, sampleRate: rate
                )
            )

        case .stem:
            if let url = app.vocoderStemURL {
                let now = app.audioEngine.clock.nowSongSeconds
                let carrier = await Task
                    .detached(priority: .userInitiated) {
                        let source = Self.monoSamples(
                            url: url, fromSec: max(0, now),
                            maxSec: 16, targetRate: rate
                        )
                        return VocoderCarriers.loopedStem(
                            source, sampleRate: rate, durationSec: dur
                        )
                    }.value
                if carrier.contains(where: { $0 != 0 }) {
                    return VocoderProgram(mode: .stem, carrier: carrier)
                }
            }
            // No stem on disk yet → the song's chord carrier.
            return VocoderProgram(
                mode: .stem,
                carrier: VocoderCarriers.chordGrid(
                    spans: chordSpans(midiBase: 48),
                    durationSec: dur, sampleRate: rate
                )
            )

        case .harmony:
            // No spectral carrier — PSOLA voice-leads against the
            // chord grid (middle-C octave, the transform convention).
            return VocoderProgram(
                mode: .harmony,
                carrier: [],
                chordSpans: chordSpans(midiBase: 60)
            )

        case .texture:
            let source = textureCarrierSource()
            guard !source.isEmpty else {
                return VocoderProgram(
                    mode: .texture,
                    carrier: VocoderCarriers.sawStack(
                        notes: [], durationSec: dur, sampleRate: rate
                    )
                )
            }
            return VocoderProgram(
                mode: .texture,
                carrier: VocoderCarriers.texture(
                    source, sampleRate: rate, durationSec: dur
                )
            )
        }
    }

    /// Chord grid for the capture window: the loaded song's chords
    /// from the CURRENT transport position (span times re-based to
    /// capture-relative seconds), else the single sounding chord,
    /// else empty (the carrier builders drone / PSOLA falls back to
    /// nearest-tone).
    private func chordSpans(midiBase: Int) -> [VocoderCarriers.ChordSpan] {
        if let chords = app.currentBundle?.timeline.chords, !chords.isEmpty {
            let now = app.audioEngine.clock.nowSongSeconds
            var spans: [VocoderCarriers.ChordSpan] = []
            for chord in chords where chord.end > now {
                let pcs = Self.pitchClasses(for: chord.symbol).sorted()
                guard !pcs.isEmpty else { continue }
                spans.append(VocoderCarriers.ChordSpan(
                    startSec: max(0, chord.start - now),
                    midiNotes: pcs.map { midiBase + $0 }
                ))
            }
            if !spans.isEmpty { return spans }
        }
        let current = currentChordPitchClasses().sorted().map { midiBase + $0 }
        return current.isEmpty
            ? []
            : [VocoderCarriers.ChordSpan(startSec: 0, midiNotes: current)]
    }

    /// M5's carrier audio: the active pack's most texture-like pad
    /// that has a resident base buffer (textures, then pads, then
    /// anything). Empty when no pack audio is loaded.
    private func textureCarrierSource() -> [Float] {
        guard let active = app.activeSamplePack else { return [] }
        let pads = active.pack.pads.sorted { $0.padIdx < $1.padIdx }
        let ordered = pads.filter { $0.family == .textures }
            + pads.filter { $0.family == .pads }
            + pads
        for pad in ordered {
            guard let buffer = app.sampleScheduler.baseBuffer(
                packId: active.pack.packId, padIdx: pad.padIdx
            ) else { continue }
            let mono = Self.monoSamples(of: buffer)
            if mono.contains(where: { $0 != 0 }) { return mono }
        }
        return []
    }

    /// Decode up to `maxSec` of an audio file starting at `fromSec`
    /// (clamped so a position past the end still yields audio) into
    /// 48 kHz mono. Empty on any read failure.
    private nonisolated static func monoSamples(
        url: URL, fromSec: Double, maxSec: Double, targetRate: Double
    ) -> [Float] {
        guard let file = try? AVAudioFile(forReading: url) else { return [] }
        let nativeRate = file.processingFormat.sampleRate
        let want = AVAudioFrameCount(maxSec * nativeRate)
        var start = AVAudioFramePosition(fromSec * nativeRate)
        if file.length - start < AVAudioFramePosition(want) {
            start = max(0, file.length - AVAudioFramePosition(want))
        }
        file.framePosition = start
        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: file.processingFormat, frameCapacity: want
        ), (try? file.read(into: buffer, frameCount: want)) != nil
        else { return [] }
        let mono = monoSamples(of: buffer)
        return nativeRate == targetRate
            ? mono
            : MicRecorder.resample(mono, from: nativeRate, to: targetRate)
    }

    /// Channel-average mono copy of a PCM buffer.
    private nonisolated static func monoSamples(
        of buffer: AVAudioPCMBuffer
    ) -> [Float] {
        guard let channels = buffer.floatChannelData else { return [] }
        let frames = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)
        guard frames > 0, channelCount > 0 else { return [] }
        if channelCount == 1 {
            return Array(UnsafeBufferPointer(start: channels[0], count: frames))
        }
        var mono = [Float](repeating: 0, count: frames)
        for i in 0..<frames {
            var sum: Float = 0
            for ch in 0..<channelCount { sum += channels[ch][i] }
            mono[i] = sum / Float(channelCount)
        }
        return mono
    }

    // MARK: - Pad transforms (P4)

    public enum BakeError: Error, LocalizedError {
        /// The pad has no transform chain to bake.
        case nothingToBake
        /// The pad's base buffer isn't resident (pack not loaded,
        /// local sample still decoding) or rendering produced silence.
        case noAudio

        public var errorDescription: String? {
            switch self {
            case .nothingToBake:
                return "This pad has no transforms to bake."
            case .noAudio:
                return "The pad's audio isn't ready yet — try again in a moment."
            }
        }
    }

    /// The persisted transform chain for a grid pad in the current
    /// mode (empty when the pad has no slot).
    public func transformChain(gridPad gridRaw: Int) -> [PadTransform] {
        app.padAssignmentStore
            .slot(mode: appMode, padIdx: gridRaw)?.transforms ?? []
    }

    /// Persist + arm a transform chain for a grid pad. Local-sample
    /// pads keep their slot; pack pads get a `.packPad` slot created
    /// on first chain (and dropped again when the chain empties, so
    /// the store only carries slots that mean something). No-op for
    /// unbound pads.
    public func setTransformChain(
        _ chain: [PadTransform], gridPad gridRaw: Int
    ) {
        let existing = app.padAssignmentStore.slot(
            mode: appMode, padIdx: gridRaw
        )
        let ref: PadSampleReference
        if let existing {
            ref = existing.ref
        } else if let binding = padBindings[gridRaw],
                  binding.packId != SampleScheduler.localPackId {
            ref = .packPad(
                packId: binding.packId, padIdx: binding.padIdx
            )
        } else {
            return
        }

        var slot = existing ?? PadSlot(ref: ref)
        slot.transforms = chain
        if chain.isEmpty, case .packPad = ref {
            app.padAssignmentStore.assign(nil, mode: appMode, padIdx: gridRaw)
        } else {
            app.padAssignmentStore.assign(slot, mode: appMode, padIdx: gridRaw)
        }
        renderTransforms(slot: slot, gridRaw: gridRaw)
        rebuildLayout()
    }

    /// Bake the pad's transform chain into a NEW local sample:
    /// render (mono) → classify → save → reassign the pad to the
    /// baked sample with a cleared chain. Non-destructive — the
    /// original sample/pack pad is untouched. Output is clamped to
    /// the 8 s compliance cap (a 4× stretch of an 8 s take would
    /// otherwise exceed it and be rejected by the store).
    @discardableResult
    public func bakeTransforms(
        gridPad gridRaw: Int
    ) async throws -> PadSampleMetadata {
        guard let slot = app.padAssignmentStore.slot(
            mode: appMode, padIdx: gridRaw
        ), !slot.transforms.isEmpty else {
            throw BakeError.nothingToBake
        }
        let key = Self.schedulerKey(for: slot.ref, gridRaw: gridRaw)
        guard let base = app.sampleScheduler.baseBuffer(
            packId: key.packId, padIdx: key.padIdx
        ) else { throw BakeError.noAudio }

        var mono = await PadTransformHost.renderMono(
            slot.transforms,
            base: base,
            tempoBpm: transformTempo(slot.timing),
            chord: currentChordMidi()
        )
        guard !mono.isEmpty else { throw BakeError.noAudio }
        let rate = base.format.sampleRate
        let maxSamples = Int(MicRecorder.maxDurationSec * rate)
        if mono.count > maxSamples {
            mono = Array(mono.prefix(maxSamples))
        }

        // Provenance: a baked local sample keeps its source (a baked
        // mic take is still mic audio → still never uploaded); pack
        // audio becomes a songChop (licensed content, device-local).
        var source: PadSampleMetadata.Source = .songChop
        if case .localSample(let id) = slot.ref,
           let baseMeta = app.padSampleStore.metadata(id: id) {
            source = baseMeta.source
        }
        let (cls, confidence) = HeuristicClassifier().classify(
            samples: mono, sampleRate: rate
        )
        let meta = try await app.padSampleStore.save(
            samples: mono,
            sampleRate: rate,
            metadata: PadSampleMetadata(
                source: source,
                classification: cls,
                confidence: confidence,
                durationSec: 0,   // authoritative values filled by the
                sampleRate: 0,    // store from the payload itself
                channels: 1,
                colorHint: Self.localColor(source)
            )
        )
        // assignLocalSample writes a FRESH PadSlot (empty chain) and
        // loads the baked buffer; drop the now-obsolete armed render.
        assignLocalSample(id: meta.id, toGridPad: gridRaw)
        clearHostedTransforms(packId: key.packId, padIdx: key.padIdx)
        return meta
    }

    /// Re-arm every persisted chain for the current mode from
    /// scratch. Cheap when nothing changed — identical (audio, chain,
    /// tempo) triples are TransformCache hits.
    public func syncTransforms() {
        transformHost.clearAll()
        for (gridRaw, slot) in app.padAssignmentStore.assignments(for: appMode)
        where !slot.transforms.isEmpty {
            renderTransforms(slot: slot, gridRaw: gridRaw)
        }
    }

    /// Render one pad's chain against its resident base buffer. A
    /// still-loading local sample renders later, from
    /// `loadLocalBuffer`'s completion.
    private func renderTransforms(slot: PadSlot, gridRaw: Int) {
        let key = Self.schedulerKey(for: slot.ref, gridRaw: gridRaw)
        transformHost.setChain(
            slot.transforms,
            packId: key.packId,
            padIdx: key.padIdx,
            base: app.sampleScheduler.baseBuffer(
                packId: key.packId, padIdx: key.padIdx
            ),
            tempoBpm: transformTempo(slot.timing),
            chord: currentChordMidi()
        )
    }

    /// Drop a pad's armed render + loop flag from the host.
    private func clearHostedTransforms(packId: String, padIdx: Int) {
        transformHost.setChain(
            [], packId: packId, padIdx: padIdx,
            base: nil, tempoBpm: 120, chord: []
        )
    }

    /// The (packId, padIdx) key the SCHEDULER uses for this slot —
    /// local samples live in the synthetic local pack under their
    /// grid index; pack pads under their own pack coordinates.
    static func schedulerKey(
        for ref: PadSampleReference, gridRaw: Int
    ) -> (packId: String, padIdx: Int) {
        switch ref {
        case .localSample:
            return (SampleScheduler.localPackId, gridRaw)
        case .packPad(let packId, let padIdx):
            return (packId, padIdx)
        }
    }

    /// Tempo for tempo-synced transforms (stutter/gate): the slot's
    /// pinned BPM if set, else the loaded song's analysed tempo, else
    /// the sketch grid tempo.
    private func transformTempo(_ timing: TransformTiming) -> Double {
        timing.fixedBpm
            ?? app.currentBundle?.meta.tempoBpm
            ?? app.sketchSettings.tempoBpm
    }

    /// Currently sounding chord as MIDI notes around middle C — feeds
    /// the harmony transform's voice leading. Empty = no chord info
    /// (nominal intervals).
    private func currentChordMidi() -> [Int] {
        currentChordPitchClasses().sorted().map { 60 + $0 }
    }

    // MARK: - Long-press sheet routing

    /// What the long-press sheet shows for a grid pad:
    ///   - local sample assigned → source sheet (manage/override)
    ///   - bound pack pad        → effects editor
    ///   - empty sample slot     → source sheet (record/assign)
    ///   - hybrid note rows      → nothing
    func padSheetTarget(row: Int, col: Int) -> PadSheetTarget? {
        let grid = PadIndex.at(row: row, col: col)
        guard grid.isValid, appMode.isImplemented else { return nil }
        if case .localSample(let id)? =
            app.padAssignmentStore.slot(mode: appMode, padIdx: grid.rawValue)?.ref,
           let meta = app.padSampleStore.metadata(id: id) {
            return .source(PadSourceTarget(
                gridRow: row, gridCol: col, sample: meta
            ))
        }
        if let effects = padEffectsTarget(row: row, col: col) {
            return .effects(effects)
        }
        if appMode == .hybrid && HybridModeLayout.isNoteRow(row) { return nil }
        return .source(PadSourceTarget(gridRow: row, gridCol: col, sample: nil))
    }

    // MARK: - Pad effects sheet

    /// Long-press target for a grid pad: resolves the bound pack pad
    /// so the editor knows what it's editing. nil for unbound pads
    /// (empty slots, note rows) and for local-sample shadows (the
    /// binding's packId is the scheduler's synthetic "local" pack,
    /// which never matches the active pack).
    func padEffectsTarget(row: Int, col: Int) -> PadEffectsTarget? {
        let grid = PadIndex.at(row: row, col: col)
        guard let binding = padBindings[grid.rawValue],
              let active = app.activeSamplePack,
              active.pack.packId == binding.packId,
              let pad = active.pack.pads.first(where: { $0.padIdx == binding.padIdx })
        else { return nil }
        return PadEffectsTarget(
            packId: binding.packId,
            padIdx: binding.padIdx,
            padName: pad.name,
            manifestBaseline: pad.effects,
            gridRow: row,
            gridCol: col
        )
    }
}
