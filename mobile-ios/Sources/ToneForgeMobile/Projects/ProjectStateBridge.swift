// ProjectStateBridge.swift
//
// The store⇄snapshot translation layer for Projects (v1). Pure with
// respect to AppState: every store it reads/writes is a parameter, so
// the capture→mutate→restore round-trip is unit-testable with
// UserDefaults(suiteName:)-backed stores and no audio engine. The
// ProjectCoordinator owns WHEN this runs (debounce, song-load hook);
// this type owns WHAT a snapshot is made of.
//
// What is deliberately NOT here:
//   * borrows — runtime state (activeBorrowContext + the mounted
//     pack), captured by ProjectCoordinator and passed in.
//   * chopEdits — iOS has no chop-edit store yet (ChopEditorSheet is
//     preview-only by design); the snapshot field stays nil on
//     capture and is ignored on restore. The contract field exists so
//     desktop (which persists ChopEdits per analysisId/presetKey) and
//     a future iOS store restore boundaries losslessly.

import Foundation
import ToneForgeEngine

enum ProjectStateBridge {

    // MARK: - Capture

    /// Read the current per-song workspace out of the stores.
    @MainActor
    static func capture(
        analysisId: String,
        padAssignments: PadAssignmentStore,
        sampleSettings: SampleSettingsStore,
        patternStore: SequencerPatternStore,
        jamSettings: JamSettingsStore,
        arrangementStore: ArrangementStore,
        borrows: [BorrowRef]
    ) -> ProjectSnapshot {
        // AppMode.rawValue → String(PadIndex.rawValue) → PadSlot.
        var assignments: [String: [String: PadSlot]] = [:]
        for (mode, slots) in padAssignments.assignmentsByMode where !slots.isEmpty {
            assignments[mode] = Dictionary(
                uniqueKeysWithValues: slots.map { (String($0.key), $0.value) })
        }

        // Patterns: only those this workspace's pads actually reference
        // (.sequence slots) — the pattern LIBRARY is global, the
        // workspace snapshots just what it needs to resolve its pads.
        var patternIds: Set<UUID> = []
        for slots in padAssignments.assignmentsByMode.values {
            for slot in slots.values {
                if case .sequence(let pid) = slot.ref { patternIds.insert(pid) }
            }
        }
        let patterns = patternIds.compactMap { patternStore.pattern(id: $0) }
            .sorted { $0.id.uuidString < $1.id.uuidString }

        // TRI-STATE preserved verbatim: nil = allow all (key stays
        // absent), empty = deny all, else allowlist (sorted for stable
        // JSON).
        let gates = sampleSettings.sectionGates(for: analysisId)
            .map { Array($0).sorted() }

        let arrMap = arrangementStore.captured(analysisId: analysisId)
        let arrangement: [String: [Int]]? = arrMap.isEmpty ? nil
            : Dictionary(uniqueKeysWithValues:
                arrMap.map { (String($0.key), $0.value) })

        return ProjectSnapshot(
            padAssignments: assignments,
            padFX: sampleSettings.padEffectsByKey,
            hiddenPads: sampleSettings.hiddenPadKeys,
            sectionGates: gates,
            sequencerPatterns: patterns,
            chopEdits: nil,          // no iOS chop-edit store yet (see header)
            arrangement: arrangement,
            launchpad: LaunchpadSnapshot(
                padCount: jamSettings.launchpadPadCount,
                sampleTriggerMode: jamSettings.sampleTriggerMode.rawValue
            ),
            borrows: borrows
        )
    }

    // MARK: - Restore

    /// Write a snapshot back into the stores. Whole-value writes:
    /// restore REPLACES the assignment table / FX map / hidden set —
    /// a workspace is the complete pad-surface state, not a patch.
    /// Borrow re-derivation happens in ProjectCoordinator (network);
    /// this only restores store-backed state.
    @MainActor
    static func restore(
        _ snapshot: ProjectSnapshot,
        analysisId: String,
        padAssignments: PadAssignmentStore,
        sampleSettings: SampleSettingsStore,
        patternStore: SequencerPatternStore,
        jamSettings: JamSettingsStore,
        arrangementStore: ArrangementStore
    ) {
        // Patterns FIRST so .sequence slots resolve the moment the
        // assignments land (upsert by id — idempotent).
        for pattern in snapshot.sequencerPatterns {
            patternStore.save(pattern)
        }

        var byMode: [String: [Int: PadSlot]] = [:]
        for (mode, slots) in snapshot.padAssignments {
            var forMode: [Int: PadSlot] = [:]
            for (key, slot) in slots {
                guard let padIdx = Int(key) else { continue }
                forMode[padIdx] = slot
            }
            if !forMode.isEmpty { byMode[mode] = forMode }
        }
        padAssignments.replaceAll(byMode)

        sampleSettings.padEffectsByKey = snapshot.padFX
        sampleSettings.hiddenPadKeys = snapshot.hiddenPads
        // Tri-state: nil clears the song's gate entirely (allow all).
        sampleSettings.setSectionGates(
            snapshot.sectionGates.map(Set.init), for: analysisId)

        var arrMap: [Int: [Int]] = [:]
        for (key, pads) in snapshot.arrangement ?? [:] {
            guard let block = Int(key) else { continue }
            arrMap[block] = pads
        }
        // Empty map removes the persisted entry — a snapshot without an
        // arrangement means the workspace HAD none.
        arrangementStore.save(arrMap, analysisId: analysisId)

        if let lp = snapshot.launchpad {
            let count = lp.padCount == 64 ? 64 : 16
            if jamSettings.launchpadPadCount != count {
                jamSettings.launchpadPadCount = count
            }
            // Unknown/legacy raw values degrade through the same
            // migration the store itself uses.
            if let mode = SampleTriggerMode
                .migratedFromLegacy(lp.sampleTriggerMode),
               jamSettings.sampleTriggerMode != mode {
                jamSettings.sampleTriggerMode = mode
            }
        }
    }

    // MARK: - Reset

    /// Song-activation swap for a song with NO saved workspace: empty
    /// the GLOBAL stores (assignment table, pad FX map, hidden set).
    /// These are UserDefaults singletons, so without this swap a song
    /// simply INHERITED whatever the previous song's workspace (or an
    /// old session) left in them — phantom pack-pad tiles on every
    /// song that lacked a project of its own. The per-song stores
    /// (section gates, arrangement) are keyed by analysisId and cannot
    /// leak across songs — and clearing them here would delete
    /// pre-Projects state — so unlike `reset` (the explicit user
    /// action) they are deliberately untouched. Ported from the
    /// desktop fix (D-034/D-035, 8e56570c).
    @MainActor
    static func activateFresh(
        padAssignments: PadAssignmentStore,
        sampleSettings: SampleSettingsStore
    ) {
        padAssignments.replaceAll([:])
        sampleSettings.padEffectsByKey = [:]
        sampleSettings.hiddenPadKeys = []
    }

    /// Clear the workspace state for `analysisId` back to the song's
    /// defaults. The sequencer pattern LIBRARY is untouched — patterns
    /// are a global collection the user may use in other songs; only
    /// the pad surface resets.
    @MainActor
    static func reset(
        analysisId: String,
        padAssignments: PadAssignmentStore,
        sampleSettings: SampleSettingsStore,
        arrangementStore: ArrangementStore
    ) {
        padAssignments.replaceAll([:])
        sampleSettings.padEffectsByKey = [:]
        sampleSettings.hiddenPadKeys = []
        sampleSettings.setSectionGates(nil, for: analysisId)
        arrangementStore.save([:], analysisId: analysisId)
    }
}
