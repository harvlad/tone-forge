// ModeCoordinator+LocalSamples.swift
//
// P3 local-sample lifecycle: mic capture → conditioned → classified →
// saved → assigned → playable, plus assignment management (assign /
// clear / hide / delete / classify-override) and the scheduler
// local-buffer sync that keeps grid-keyed buffers in step with the
// per-mode assignment store. Split from ModeCoordinator.swift.

import AVFoundation
import Foundation
import ToneForgeEngine

extension ModeCoordinator {

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

    /// Point a grid pad at a saved sequencer pattern. Pressing the pad
    /// runs the whole sequence (see SequencePadManager). Persists +
    /// repaints. No audio buffer is loaded — the pad drives other pads.
    public func assignSequence(targetRow: Int, targetCol: Int, patternId: UUID) {
        let grid = PadIndex.at(row: targetRow, col: targetCol)
        guard grid.isValid else { return }
        app.padAssignmentStore.assign(
            PadSlot(ref: .sequence(patternId: patternId)),
            mode: appMode, padIdx: grid.rawValue
        )
        rebuildLayout()
    }

    /// Un-assign a pad (the sample stays in the store; the pad falls
    /// back to the pack layout).
    public func clearLocalAssignment(gridPad gridRaw: Int) {
        // A running sequence pad (D-023) keeps its SequencerPlayer
        // looping — re-publishing padDown to the bus every step — so it
        // MUST be stopped here or the pattern plays on after the pad is
        // gone from the grid.
        sequencePadManager.stop(padIdx: gridRaw)
        app.padAssignmentStore.assign(nil, mode: appMode, padIdx: gridRaw)
        app.sampleScheduler.clearLocalBuffer(for: gridRaw)
        clearHostedTransforms(
            packId: SampleScheduler.localPackId, padIdx: gridRaw
        )
        rebuildLayout()
    }

    /// Hide a pack pad from the grid. The pad can be restored via
    /// unhidePackPad or by switching packs.
    public func hidePackPad(row: Int, col: Int) {
        let grid = PadIndex.at(row: row, col: col)
        // Stop any running sequence on this pad regardless of pad type.
        sequencePadManager.stop(padIdx: grid.rawValue)
        guard let binding = padBindings[grid.rawValue],
              binding.packId != SampleScheduler.localPackId
        else {
            // It's a local sample (or a sequence pad), use
            // clearLocalAssignment instead.
            clearLocalAssignment(gridPad: grid.rawValue)
            return
        }
        // Kill any voice still ringing for this pad (looping / toggle-
        // latched) before it vanishes from the grid — otherwise the
        // sample plays on with no pad to stop it. Also drop the armed
        // loop flag/render and any replay binding so a recorded layer
        // can't re-trigger the now-gone pad on the next loop.
        app.sampleScheduler.stopVoices(padIdx: binding.padIdx, packId: binding.packId)
        clearHostedTransforms(packId: binding.packId, padIdx: binding.padIdx)
        replayBindings[grid.rawValue] = nil
        app.sampleSettings.hidePad(packId: binding.packId, padIdx: binding.padIdx)
        rebuildLayout()
    }

    /// Unhide a previously hidden pack pad.
    public func unhidePackPad(packId: String, padIdx: Int) {
        app.sampleSettings.unhidePad(packId: packId, padIdx: padIdx)
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
    func loadLocalBuffer(id: UUID, gridRaw: Int) {
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
}
