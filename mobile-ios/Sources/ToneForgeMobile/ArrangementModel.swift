// ArrangementModel.swift
//
// Live-capture arrangement for the iOS Launchpad (Samples surface) — the
// mobile twin of jam-desktop's ArrangementController and the web kit.js
// arrangement runtime. Rec through a playing song to capture which pads
// are ON in each section block; Play replays that captured set hands-free
// at block boundaries; Clear forgets it. Persisted per song.
//
// All capture/replay math lives in the shared, tested ToneForgeEngine
// `ArrangementRuntime` (port-parity with kit.js). This model only wires
// that runtime to the mobile side effects:
//   • active grid indices — read passively from the SampleVoicePool
//     (ringing/pending) inverted through ModeCoordinator.padBindings, so
//     Rec is a pure observer that never perturbs the pad-trigger path;
//   • arm/release — ModeCoordinator.triggerJamSample(latch:true) /
//     releaseJamSample, resolved from padBindings;
//   • persistence — ArrangementStore, one web-compatible serialized blob
//     under jamn.arrangement.<analysisId> (matches web localStorage and
//     AppState's jamn.session.* keys), so a capture round-trips across
//     surfaces via Arrangement.serialize / .parse.

import Foundation
import Combine
import ToneForgeEngine

/// UserDefaults-backed per-song persistence for the live-capture
/// arrangement, keyed `jamn.arrangement.<analysisId>`. Stores the
/// web-compatible serialized map (`{blockIndex:[padIdx…]}` JSON) so a
/// capture round-trips loss-free with web (localStorage) and desktop.
@MainActor
final class ArrangementStore {
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    private func key(_ analysisId: String) -> String {
        "jamn.arrangement.\(analysisId)"
    }

    /// The captured map for a song ({} when none saved).
    func captured(analysisId: String) -> [Int: [Int]] {
        guard let json = defaults.string(forKey: key(analysisId)) else { return [:] }
        return Arrangement.parse(json) ?? [:]
    }

    /// Persist a song's capture. An empty/degenerate map removes the entry
    /// (serialize returns nil), matching the web's removeItem.
    func save(_ map: [Int: [Int]], analysisId: String) {
        if let json = Arrangement.serialize(map) {
            defaults.set(json, forKey: key(analysisId))
        } else {
            defaults.removeObject(forKey: key(analysisId))
        }
    }
}

/// Observable controller wiring the shared `ArrangementRuntime` to the
/// mobile Launchpad. Mirrors jam-desktop's `ArrangementController`.
@MainActor
final class ArrangementModel: ObservableObject {

    /// Capture/replay state machine (shared with desktop + web semantics).
    private var runtime = ArrangementRuntime(blocks: [])

    /// Collapsed section blocks for the current song (drives the strip).
    @Published private(set) var blocks: [ArrangementBlock] = []
    @Published private(set) var recording = false
    @Published private(set) var playing = false
    /// Strip highlight + playhead, refreshed each tick for the view.
    @Published private(set) var activeBlock = -1
    @Published private(set) var playheadFrac: Double? = nil
    /// Block indices that have at least one captured pad (fills the strip).
    @Published private(set) var filledBlocks: Set<Int> = []

    /// Unowned: AppState owns this model; identical lifetimes (same
    /// pattern as ModeCoordinator).
    private unowned let app: AppState
    private let store: ArrangementStore
    private var analysisId: String = ""

    init(app: AppState, store: ArrangementStore? = nil) {
        self.app = app
        // Default constructed in-body: ArrangementStore's init is
        // @MainActor-isolated, so a default-argument call (nonisolated
        // context) won't compile.
        self.store = store ?? ArrangementStore()
    }

    /// Rebuild for a newly loaded song: collapse its sections into blocks
    /// and restore any saved capture. Releases whatever a prior replay held.
    func loadSong(analysisId: String, sections: [ArrangementSectionInput]) {
        stopAllHeld()
        self.analysisId = analysisId
        let blocks = Arrangement.collapseSections(sections)
        let captured = analysisId.isEmpty ? [:] : store.captured(analysisId: analysisId)
        runtime = ArrangementRuntime(blocks: blocks, captured: captured)
        self.blocks = blocks
        recording = false
        playing = false
        activeBlock = -1
        playheadFrac = nil
        refreshFilled()
    }

    // MARK: - Controls

    func toggleRecording() {
        guard !blocks.isEmpty else { return }
        if runtime.recording {
            runtime.stopRecording()
        } else {
            // Rec and Play are mutually exclusive — startRecording stops
            // any replay and hands back its held pads to release.
            runtime.startRecording().forEach { release(grid: $0) }
        }
        recording = runtime.recording
        playing = runtime.playing
    }

    func togglePlaying() {
        guard !blocks.isEmpty else { return }
        if runtime.playing {
            runtime.stopReplay().forEach { release(grid: $0) }
        } else {
            runtime.startPlaying()
        }
        recording = runtime.recording
        playing = runtime.playing
    }

    func clear() {
        guard !blocks.isEmpty else { return }
        runtime.clear().forEach { release(grid: $0) }
        if !analysisId.isEmpty { store.save(runtime.captured, analysisId: analysisId) }
        refreshFilled()
    }

    // MARK: - Tick (driven by AppState.tick, 30 Hz)

    /// One poll: record the pads currently ON into the active block, or
    /// replay the captured set at block boundaries. Idempotent within a
    /// block, so safe to call every UI frame.
    func tick(time: Double, isPlaying: Bool) {
        guard !blocks.isEmpty else { return }
        let active = activeGridIndices()
        let r = runtime.tick(time: time, isPlaying: isPlaying, activePads: active)
        if activeBlock != r.activeBlock { activeBlock = r.activeBlock }
        if playheadFrac != r.playheadFrac { playheadFrac = r.playheadFrac }
        r.toRelease.forEach { release(grid: $0) }
        r.toArm.forEach { arm(grid: $0) }
        if r.capturedChanged {
            if !analysisId.isEmpty { store.save(runtime.captured, analysisId: analysisId) }
            refreshFilled()
        }
    }

    // MARK: - Private

    /// Grid indices currently sounding — the passive Rec observer. A grid
    /// pad is "active" if its bound (packId, padIdx) is ringing or pending
    /// in the voice pool. Reads state only; never triggers a pad.
    private func activeGridIndices() -> [Int] {
        let ringing = app.sampleVoicePool.ringingPadKeys
        let pending = app.sampleVoicePool.pendingPadKeys
        guard !ringing.isEmpty || !pending.isEmpty else { return [] }
        var out: [Int] = []
        for (grid, binding) in app.modeCoordinator.padBindings {
            let key = SamplePadKey(packId: binding.packId, padIdx: binding.padIdx)
            if ringing.contains(key) || pending.contains(key) { out.append(grid) }
        }
        return out
    }

    /// Arm a captured grid pad for replay — latch:true so a held loop
    /// sustains until the next boundary releases it. Unbound grid indices
    /// are skipped.
    private func arm(grid: Int) {
        guard let b = app.modeCoordinator.padBindings[grid] else { return }
        app.modeCoordinator.triggerJamSample(padIdx: b.padIdx, packId: b.packId, latch: true)
    }

    private func release(grid: Int) {
        guard let b = app.modeCoordinator.padBindings[grid] else { return }
        app.modeCoordinator.releaseJamSample(padIdx: b.padIdx, packId: b.packId)
    }

    private func stopAllHeld() {
        runtime.stopReplay().forEach { release(grid: $0) }
    }

    private func refreshFilled() {
        var s = Set<Int>()
        for (bi, pads) in runtime.captured where !pads.isEmpty { s.insert(bi) }
        filledBlocks = s
    }
}
