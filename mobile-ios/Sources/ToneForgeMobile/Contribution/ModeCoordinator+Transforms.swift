// ModeCoordinator+Transforms.swift
//
// P4 pad transforms: persisting + arming per-pad transform chains
// against the scheduler's resident base buffers (via PadTransformHost)
// and baking a chain into a new local sample. Split from
// ModeCoordinator.swift.

import AVFoundation
import Foundation
import ToneForgeEngine

extension ModeCoordinator {

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
    func renderTransforms(slot: PadSlot, gridRaw: Int) {
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
    func clearHostedTransforms(packId: String, padIdx: Int) {
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
        case .sequence:
            // Sequences have no scheduler buffer; they are handled by
            // SequencePadManager. Return a sentinel that matches nothing.
            return ("__sequence__", gridRaw)
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
}
