// ArrangementController.swift
//
// Live-capture arrangement runtime for the desktop Launchpad — the native
// twin of the kit.js arrangement runtime. Rec through a song to capture which
// pads are ON per section block; Play replays them hands-free at block
// boundaries. All the capture/replay math lives in the shared, tested
// ToneForgeEngine `ArrangementRuntime`; this controller only wires it to the
// LaunchpadController (pad arm/release + live activePads), the transport
// (time / isPlaying), and the per-song ArrangementStore.

import Foundation
import Observation
import ToneForgeEngine

@Observable
@MainActor
public final class ArrangementController {

    /// Capture/replay state machine (shared with iOS + web semantics).
    @ObservationIgnored private var runtime = ArrangementRuntime(blocks: [])

    /// Strip highlight + playhead, refreshed each tick for the view.
    public private(set) var activeBlock = -1
    public private(set) var playheadFrac: Double? = nil

    public var blocks: [ArrangementBlock] { runtime.blocks }
    public private(set) var recording = false
    public private(set) var playing = false
    /// Block indices that have at least one captured pad (fills the strip).
    public private(set) var filledBlocks: Set<Int> = []

    @ObservationIgnored private let launchpad: LaunchpadController
    @ObservationIgnored private let store: ArrangementStore
    @ObservationIgnored private var analysisId: String = ""

    public init(launchpad: LaunchpadController, store: ArrangementStore) {
        self.launchpad = launchpad
        self.store = store
    }

    /// Rebuild for a newly loaded song: collapse its sections into blocks and
    /// restore any saved capture. Releases whatever a prior replay held.
    public func loadSong(analysisId: String, sections: [ArrangementSectionInput]) {
        stopAllHeld()
        self.analysisId = analysisId
        let blocks = Arrangement.collapseSections(sections)
        let captured = analysisId.isEmpty ? [:] : store.captured(analysisId: analysisId)
        runtime = ArrangementRuntime(blocks: blocks, captured: captured)
        recording = false
        playing = false
        activeBlock = -1
        playheadFrac = nil
        refreshFilled()
    }

    // MARK: - Controls

    public func toggleRecording() {
        guard !blocks.isEmpty else { return }
        if runtime.recording {
            runtime.stopRecording()
        } else {
            runtime.startRecording().forEach { launchpad.replayRelease($0) }
        }
        recording = runtime.recording
        playing = runtime.playing
    }

    public func togglePlaying() {
        guard !blocks.isEmpty else { return }
        if runtime.playing {
            runtime.stopReplay().forEach { launchpad.replayRelease($0) }
        } else {
            runtime.startPlaying()
        }
        recording = runtime.recording
        playing = runtime.playing
    }

    public func clear() {
        guard !blocks.isEmpty else { return }
        runtime.clear().forEach { launchpad.replayRelease($0) }
        if !analysisId.isEmpty { store.save(runtime.captured, analysisId: analysisId) }
        refreshFilled()
    }

    // MARK: - Tick (driven by the panel's animation timer)

    /// One poll: record the pads currently ON into the active block, or replay
    /// the captured set at block boundaries. Safe to call at any cadence — the
    /// runtime is idempotent within a block.
    public func tick(time: Double, isPlaying: Bool) {
        guard !blocks.isEmpty else { return }
        let active = launchpad.activePads.map { $0.row * 8 + $0.col }
        let r = runtime.tick(time: time, isPlaying: isPlaying, activePads: active)
        activeBlock = r.activeBlock
        playheadFrac = r.playheadFrac
        r.toRelease.forEach { launchpad.replayRelease($0) }
        r.toArm.forEach { launchpad.replayArm($0) }
        if r.capturedChanged {
            if !analysisId.isEmpty { store.save(runtime.captured, analysisId: analysisId) }
            refreshFilled()
        }
    }

    // MARK: - Private

    private func refreshFilled() {
        var s = Set<Int>()
        for (bi, pads) in runtime.captured where !pads.isEmpty { s.insert(bi) }
        filledBlocks = s
    }

    private func stopAllHeld() {
        runtime.stopReplay().forEach { launchpad.replayRelease($0) }
    }
}
