// ProjectStateBridge.swift
//
// The store⇄snapshot translation layer for Projects on desktop — the
// jam-desktop twin of the iOS ProjectStateBridge, against the SAME
// shared contract (ToneForgeEngine/Projects/ProjectSnapshot.swift).
// Pure with respect to SessionController: every store it reads/writes
// is a parameter, so the capture→restore round-trip is unit-testable
// with UserDefaults(suiteName:)/temp-dir stores and no audio engine.
//
// COORDINATE TRANSLATION (the desktop-specific part): the snapshot's
// padAssignments wire is AppMode.rawValue → String(PadIndex 11..88,
// row 1 = BOTTOM) → PadSlot. Desktop has ONE Launchpad surface with
// no mode axis and row-major 0..63 indexing from the TOP-LEFT
// (LaunchpadPad). Only the "sample" axis is translated + owned here;
// every OTHER mode's entries (hybrid, jamInKey, …) round-trip
// verbatim through the `preserved` snapshot so an iOS-authored
// workspace loses nothing when re-saved on a Mac. The slot mapping is
// the exact formula iOS itself uses to place pack pads
// (ModeCoordinator+Layout: PadIndex.at(row: 8 - idx/8, col: idx%8+1)),
// so grid POSITIONS agree visually across surfaces.
//
// PRESERVE-ON-ROUND-TRIP details this bridge owns:
//   * PadSlot.transforms/.timing — desktop's PadAssignmentStore holds
//     only the ref; a sample-axis slot whose ref is UNCHANGED keeps
//     its preserved PadSlot verbatim so iOS transform chains survive.
//   * hiddenPads — desktop has no per-pad hide surface; the preserved
//     set passes through untouched (documented ignore).
//   * sectionGates — LIVE on desktop: restore sets
//     LaunchpadController.sectionGate and padDown consults
//     SectionResolver.isAllowed (iOS SampleScheduler parity), so the
//     tri-state is honored, not just preserved.
//   * chopEdits — desktop's own per-(analysisId, presetKey) store IS
//     the canonical source for this field (iOS captures nil today).

import Foundation
import ToneForgeEngine

public enum ProjectStateBridge {

    /// The one grid axis desktop owns (AppMode.sample on iOS).
    public static let sampleModeKey = AppMode.sample.rawValue

    // MARK: - Index translation (0..63 row-major top-left ⇄ 11..88)

    /// Desktop grid slot (row-major from the TOP-LEFT, 0..63) → engine
    /// PadIndex rawValue (row*10+col, row 1 = BOTTOM). nil outside the
    /// 8×8 grid. Bijective with `gridSlot(fromPadIndexRaw:)`.
    public static func padIndexRaw(fromGridSlot slot: Int) -> Int? {
        guard (0..<64).contains(slot) else { return nil }
        return (8 - slot / 8) * 10 + (slot % 8 + 1)
    }

    /// Engine PadIndex rawValue → desktop grid slot. nil for anything
    /// outside the valid 11..88 window (row/col must be 1..8) — such
    /// keys are never dropped from the snapshot, only skipped by the
    /// live desktop store.
    public static func gridSlot(fromPadIndexRaw raw: Int) -> Int? {
        let index = PadIndex(raw)
        guard index.isValid else { return nil }
        return (8 - index.row) * 8 + (index.col - 1)
    }

    // MARK: - Ref conversion (identical wire keys, two Swift types)

    /// Desktop PadSlotReference → the frozen engine wire enum. Case
    /// map is total — desktop expresses all three ref kinds.
    public static func engineRef(_ ref: PadSlotReference) -> PadSampleReference {
        switch ref {
        case .sequence(let patternId): return .sequence(patternId: patternId)
        case .localSample(let id): return .localSample(id: id)
        case .packPad(let packId, let padIdx):
            return .packPad(packId: packId, padIdx: padIdx)
        }
    }

    /// Engine wire enum → desktop PadSlotReference. Also total; a
    /// `.localSample` whose payload only exists on the authoring
    /// device restores as an inert slot (padDown resolves no file),
    /// never a dropped one.
    public static func desktopRef(_ ref: PadSampleReference) -> PadSlotReference {
        switch ref {
        case .sequence(let patternId): return .sequence(patternId: patternId)
        case .localSample(let id): return .localSample(id: id)
        case .packPad(let packId, let padIdx):
            return .packPad(packId: packId, padIdx: padIdx)
        }
    }

    // MARK: - Capture

    /// Read the current per-song workspace out of the desktop stores.
    /// `preserved` is the last snapshot restored for this song (nil =
    /// fresh) — the source for everything desktop can't express live:
    /// other modes' pad axes, hiddenPads, and the transform chain of
    /// unchanged sample-axis slots.
    @MainActor
    public static func capture(
        analysisId: String,
        padAssignments: PadAssignmentStore,
        padFX: PadFXStore,
        patternStore: SequencerPatternStore,
        chopEditStore: ChopEditStore,
        arrangementStore: ArrangementStore,
        launchpad: LaunchpadController,
        preserved: ProjectSnapshot?,
        borrows: [BorrowRef]
    ) -> ProjectSnapshot {
        // Sample axis from the live desktop store, translated to the
        // engine's PadIndex keys. Unchanged refs keep the preserved
        // PadSlot verbatim (transforms/timing survive the Mac).
        let preservedSample = preserved?.padAssignments[sampleModeKey] ?? [:]
        var sampleAxis: [String: PadSlot] = [:]
        for (slot, ref) in padAssignments.assignments {
            guard let raw = padIndexRaw(fromGridSlot: slot) else { continue }
            let key = String(raw)
            let engRef = engineRef(ref)
            if let kept = preservedSample[key], kept.ref == engRef {
                sampleAxis[key] = kept
            } else {
                sampleAxis[key] = PadSlot(ref: engRef)
            }
        }
        var assignments = preserved?.padAssignments ?? [:]
        if sampleAxis.isEmpty {
            assignments.removeValue(forKey: sampleModeKey)
        } else {
            assignments[sampleModeKey] = sampleAxis
        }

        // Patterns: those referenced by .sequence slots on ANY axis —
        // the live desktop grid plus the preserved modes (restore
        // upserted their patterns into this store, so they resolve).
        var patternIds: Set<UUID> = []
        for ref in padAssignments.assignments.values {
            if case .sequence(let pid) = ref { patternIds.insert(pid) }
        }
        for (mode, slots) in assignments where mode != sampleModeKey {
            for slot in slots.values {
                if case .sequence(let pid) = slot.ref { patternIds.insert(pid) }
            }
        }
        let patterns = patternIds.compactMap { patternStore.pattern(id: $0) }
            .sorted { $0.id.uuidString < $1.id.uuidString }

        // TRI-STATE preserved verbatim: nil = allow all (key absent),
        // empty = deny all, else allowlist (sorted for stable JSON).
        let gates = launchpad.sectionGate.map { Array($0).sorted() }

        let edits = chopEditStore.edits(analysisId: analysisId)

        let arrMap = arrangementStore.captured(analysisId: analysisId)
        let arrangement: [String: [Int]]? = arrMap.isEmpty ? nil
            : Dictionary(uniqueKeysWithValues:
                arrMap.map { (String($0.key), $0.value) })

        return ProjectSnapshot(
            padAssignments: assignments,
            padFX: padFX.effectsByKey,
            hiddenPads: preserved?.hiddenPads ?? [],
            sectionGates: gates,
            sequencerPatterns: patterns,
            chopEdits: edits.isEmpty ? nil : edits,
            arrangement: arrangement,
            launchpad: LaunchpadSnapshot(
                padCount: launchpad.padCount,
                sampleTriggerMode: launchpad.playbackMode.rawValue
            ),
            borrows: borrows
        )
    }

    // MARK: - Restore

    /// Write a snapshot back into the desktop stores. Whole-value
    /// writes: restore REPLACES the assignment table / FX map / the
    /// song's chop edits + arrangement — a workspace is the complete
    /// pad-surface state, not a patch. Borrow re-derivation happens in
    /// ProjectCoordinator (network); this only restores store-backed
    /// state.
    @MainActor
    public static func restore(
        _ snapshot: ProjectSnapshot,
        analysisId: String,
        padAssignments: PadAssignmentStore,
        padFX: PadFXStore,
        patternStore: SequencerPatternStore,
        chopEditStore: ChopEditStore,
        arrangementStore: ArrangementStore,
        launchpad: LaunchpadController
    ) {
        // Patterns FIRST so .sequence slots resolve the moment the
        // assignments land (upsert by id — idempotent).
        for pattern in snapshot.sequencerPatterns {
            patternStore.save(pattern)
        }

        var slots: [Int: PadSlotReference] = [:]
        for (key, slot) in snapshot.padAssignments[sampleModeKey] ?? [:] {
            guard let raw = Int(key),
                  let grid = gridSlot(fromPadIndexRaw: raw) else { continue }
            slots[grid] = desktopRef(slot.ref)
        }
        padAssignments.replaceAll(slots)

        padFX.replaceAll(snapshot.padFX)

        // Tri-state lands LIVE: padDown gates on it via SectionResolver.
        launchpad.sectionGate = snapshot.sectionGates.map(Set.init)

        // The song's chop edits become exactly the snapshot's (empty
        // clears them → analyzer boundaries). onEditsChanged fires so
        // the grid + sequencer re-resolve when a song is live.
        chopEditStore.replaceAll(snapshot.chopEdits ?? [:],
                                 analysisId: analysisId)

        var arrMap: [Int: [Int]] = [:]
        for (key, pads) in snapshot.arrangement ?? [:] {
            guard let block = Int(key) else { continue }
            arrMap[block] = pads
        }
        // Empty map removes the persisted entry — a snapshot without an
        // arrangement means the workspace HAD none.
        arrangementStore.save(arrMap, analysisId: analysisId)

        if let lp = snapshot.launchpad {
            // Contract: readers clamp anything but 64 to 16? Desktop's
            // surface supports exactly {16, 64}; the engine doc says
            // clamp unknown to 16 — match iOS's reader verbatim.
            let count = lp.padCount == 64 ? 64 : 16
            if launchpad.padCount != count {
                launchpad.padCount = count
            }
            // Unknown/legacy raw values degrade through the same
            // migration the controller itself uses (tap/loop → follow).
            if let mode = LaunchpadController.PadPlaybackMode
                .migratedFromLegacy(lp.sampleTriggerMode),
               launchpad.playbackMode != mode {
                launchpad.playbackMode = mode
            }
        }
    }

    // MARK: - Reset

    /// Clear the workspace state for `analysisId` back to the song's
    /// defaults: no custom assignments, no FX, no gate, analyzer chop
    /// boundaries, no arrangement. The sequencer pattern LIBRARY is
    /// untouched (patterns are a global collection); launchpad surface
    /// settings (16/64, trigger mode) are user prefs, not workspace
    /// state, and stay (iOS parity).
    @MainActor
    public static func reset(
        analysisId: String,
        padAssignments: PadAssignmentStore,
        padFX: PadFXStore,
        chopEditStore: ChopEditStore,
        arrangementStore: ArrangementStore,
        launchpad: LaunchpadController
    ) {
        padAssignments.replaceAll([:])
        padFX.replaceAll([:])
        launchpad.sectionGate = nil
        chopEditStore.replaceAll([:], analysisId: analysisId)
        arrangementStore.save([:], analysisId: analysisId)
    }

    // MARK: - Borrow helpers (pure, pinned by tests)

    /// Content-addressed refs for a mounted borrow. BorrowRef.init?
    /// drops pads without a content address (host "initial" pads,
    /// span-less pads) — identity NEVER falls back to response padIdx.
    public static func borrowRefs(
        pads: [SamplePad], donorId: String, donorName: String?
    ) -> [BorrowRef] {
        pads.compactMap {
            BorrowRef(pad: $0, donorSongId: donorId, donorName: donorName)
        }
    }

    /// The refs a fresh borrow response could NOT satisfy — matched by
    /// CONTENT (BorrowRef.matches: assetId, else span ±2 ms + stem),
    /// never by response index.
    public static func missingRefs(
        _ refs: [BorrowRef], in pads: [SamplePad]
    ) -> [BorrowRef] {
        refs.filter { ref in !pads.contains { ref.matches(pad: $0) } }
    }

    /// Concrete stem role → the logical family the borrow endpoint
    /// takes as its `stem` param (backend `_logical_stem` twin; iOS
    /// ProjectCoordinator.logicalStem parity). The borrow response
    /// carries BOTH songs' full curated kits whatever the param, so
    /// this only needs to be a valid family.
    public static func logicalStem(_ role: String) -> String {
        let r = role.lowercased()
        if r.contains("drum") { return "drums" }
        if r == "bass" { return "bass" }
        if r == "vocals" || r == "vocal" { return "vocals" }
        return "other"
    }
}
