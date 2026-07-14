// ModeCoordinator+PadEditing.swift
//
// Long-press pad editing surfaces (source / effects / trimmer sheet
// routing), pad preview + reset, cross-pack assignment, and the
// Arrange drag-to-swap machinery. Split from ModeCoordinator.swift.

import Foundation
import ToneForgeEngine

extension ModeCoordinator {

    // MARK: - Long-press sheet routing

    /// What the long-press sheet shows for a grid pad:
    ///   - local sample assigned → source sheet (manage/override)
    ///   - bound pack pad        → effects editor
    ///   - empty sample slot     → source sheet (record/assign)
    ///   - hybrid note rows      → nothing
    ///   - jam in key            → nothing (the whole grid is notes)
    func padSheetTarget(row: Int, col: Int) -> PadSheetTarget? {
        let grid = PadIndex.at(row: row, col: col)
        guard grid.isValid, appMode.isImplemented else { return nil }
        if appMode == .jamInKey { return nil }
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

    /// Preview a trimmed portion of a pad sample. Used by the waveform
    /// trimmer to audition the selected region.
    func previewTrimmed(
        packId: String,
        padIdx: Int,
        startFraction: Double,
        endFraction: Double
    ) {
        app.sampleScheduler.previewTrimmed(
            padIdx: padIdx,
            packId: packId,
            startFraction: startFraction,
            endFraction: endFraction
        )
    }

    /// Reset a pad to its default state: clear effects override, trim, loop.
    func resetPadToDefault(row: Int, col: Int) {
        let grid = PadIndex.at(row: row, col: col)
        guard let binding = padBindings[grid.rawValue] else { return }

        // Clear effects override (reverts to manifest baseline)
        app.sampleSettings.setPadEffectsOverride(
            nil,
            packId: binding.packId,
            padIdx: binding.padIdx
        )

        // Clear loop and other transforms (nil base clears the render)
        transformHost.setChain(
            [],
            packId: binding.packId,
            padIdx: binding.padIdx,
            base: nil,
            tempoBpm: 120,
            chord: []
        )

        // FUTURE: Clear trim settings once SampleTrimStore is implemented
    }

    // MARK: - Pad preview (pack browser)

    /// Preview a pad from any pack without switching the active pack.
    func previewPadFromPack(packId: String, padIdx: Int) {
        // Stop any currently playing preview first
        stopPreviewPad()

        // Ensure the pack is loaded (without switching active pack)
        app.ensurePackLoaded(packId: packId)

        // Use triggerRaw to bypass contributionGuard assertion (this is a preview, not a contribution)
        _ = app.sampleScheduler.triggerRaw(padIdx: padIdx, packId: packId)

        // Track what's previewing so we can stop it
        previewingPad = (packId, padIdx)
    }

    /// Stop the currently previewing pad.
    func stopPreviewPad() {
        guard let previewing = previewingPad else { return }
        app.sampleScheduler.release(padIdx: previewing.padIdx, packId: previewing.packId)
        previewingPad = nil
    }

    /// Assign a pack pad to a specific grid position.
    /// Unhides the pad and switches packs if needed.
    func assignPadFromPack(
        targetRow: Int,
        targetCol: Int,
        sourcePackId: String,
        sourcePadIdx: Int
    ) {
        let grid = PadIndex.at(row: targetRow, col: targetCol)
        guard grid.isValid else { return }

        // Unhide the pad in case it was hidden.
        app.sampleSettings.unhidePad(packId: sourcePackId, padIdx: sourcePadIdx)

        // Pin just this one pack pad to the target grid cell — the grid
        // can mix pads from multiple packs. Ensure the source pack's
        // buffers are loaded so the pad triggers regardless of which
        // pack is currently fronted, then persist + repaint.
        app.ensurePackLoaded(packId: sourcePackId)
        app.padAssignmentStore.assign(
            PadSlot(ref: .packPad(packId: sourcePackId, padIdx: sourcePadIdx)),
            mode: appMode, padIdx: grid.rawValue
        )
        rebuildLayout()
    }

    // MARK: - Arrange (drag-to-swap)

    /// Swap the full assignment of two grid cells (Arrange mode on the
    /// 8×8 grid). Handles every combination — active-pack quadrant
    /// pads, cross-pack pins, local samples, sequences, and empties —
    /// by capturing each cell's effective slot, clearing both, then
    /// re-placing swapped. Persists via PadAssignmentStore + hidden
    /// flags; one rebuild + transform resync reconciles the rest.
    public func swapPads(from a: Int, to b: Int) {
        guard a != b, PadIndex(a).isValid, PadIndex(b).isValid else { return }
        let capA = captureCell(gridRaw: a)
        let capB = captureCell(gridRaw: b)

        // Clear both cells (store slot + any local buffer). Hidden
        // flags are settled by placeCell. padBindings stays at its
        // pre-swap state until rebuildLayout, so native-home detection
        // below still sees the original pack layout.
        app.sampleScheduler.clearLocalBuffer(for: a)
        app.sampleScheduler.clearLocalBuffer(for: b)
        app.padAssignmentStore.assign(nil, mode: appMode, padIdx: a)
        app.padAssignmentStore.assign(nil, mode: appMode, padIdx: b)

        placeCell(capA, at: b)
        placeCell(capB, at: a)

        rebuildLayout()
        syncTransforms()
    }

    /// A cell's effective content for a swap: the slot it currently
    /// resolves to (explicit assignment, or the active pack's native
    /// pad synthesized as a `.packPad` slot), plus whether that pad is
    /// the active pack on its own native home cell.
    private func captureCell(
        gridRaw: Int
    ) -> (slot: PadSlot?, nativeHome: Bool) {
        if let slot = app.padAssignmentStore.slot(mode: appMode, padIdx: gridRaw) {
            return (slot, false)
        }
        if let binding = padBindings[gridRaw],
           binding.packId != SampleScheduler.localPackId {
            let native = app.activeSamplePack?.pack.packId == binding.packId
                && nativeHomeCell(padIdx: binding.padIdx) == gridRaw
            return (
                PadSlot(ref: .packPad(
                    packId: binding.packId, padIdx: binding.padIdx
                )),
                native
            )
        }
        return (nil, false)
    }

    /// Place a captured slot into `cell`, settling hidden flags and
    /// local buffers so the pad renders (and sounds) exactly once.
    private func placeCell(
        _ cap: (slot: PadSlot?, nativeHome: Bool), at cell: Int
    ) {
        guard let slot = cap.slot else {
            // Landing empty: if the active pack natively paints a pad
            // here, hide it so the cell reads truly empty.
            if let binding = padBindings[cell],
               binding.packId == app.activeSamplePack?.pack.packId,
               nativeHomeCell(padIdx: binding.padIdx) == cell {
                app.sampleSettings.hidePad(
                    packId: binding.packId, padIdx: binding.padIdx
                )
            }
            return
        }
        switch slot.ref {
        case .localSample(let id):
            app.padAssignmentStore.assign(slot, mode: appMode, padIdx: cell)
            loadLocalBuffer(id: id, gridRaw: cell)
        case .sequence:
            app.padAssignmentStore.assign(slot, mode: appMode, padIdx: cell)
        case .packPad(let pk, let pi):
            let isActive = app.activeSamplePack?.pack.packId == pk
            if isActive, nativeHomeCell(padIdx: pi) == cell, slot.transforms.isEmpty {
                // Back on its own home cell, plain — render natively.
                app.sampleSettings.unhidePad(packId: pk, padIdx: pi)
            } else {
                app.padAssignmentStore.assign(slot, mode: appMode, padIdx: cell)
                // Suppress the active pack's native auto-paint so the
                // pad doesn't duplicate at its home cell.
                if isActive {
                    app.sampleSettings.hidePad(packId: pk, padIdx: pi)
                }
            }
        }
    }

    /// The native grid cell (gridRaw) an active-pack pad auto-paints to
    /// (padIdx 0..15 fill the top-left 4×4 quadrant, rows 8→5).
    private func nativeHomeCell(padIdx: Int) -> Int? {
        guard (0..<16).contains(padIdx) else { return nil }
        return PadIndex.at(row: 8 - padIdx / 4, col: padIdx % 4 + 1).rawValue
    }

    /// Trimmer target for a grid pad: provides sample info for waveform trimming.
    func padTrimmerTarget(row: Int, col: Int) -> SampleTrimmerTarget? {
        let grid = PadIndex.at(row: row, col: col)
        guard let binding = padBindings[grid.rawValue],
              let active = app.activeSamplePack,
              active.pack.packId == binding.packId,
              let pad = active.pack.pads.first(where: { $0.padIdx == binding.padIdx })
        else { return nil }

        // Real waveform from the resident buffer (same transform-
        // resolved audio previewTrimmed plays). No buffer loaded means
        // nothing to trim — don't open the sheet on a fake waveform.
        guard let waveform = app.sampleScheduler.padWaveform(
            packId: binding.packId, padIdx: binding.padIdx
        ) else { return nil }

        return SampleTrimmerTarget(
            packId: binding.packId,
            padIdx: binding.padIdx,
            padName: pad.name,
            gridRow: row,
            gridCol: col,
            durationSec: waveform.durationSec,
            peaks: waveform.peaks
        )
    }
}
