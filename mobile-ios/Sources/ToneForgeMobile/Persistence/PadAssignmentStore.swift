// PadAssignmentStore.swift
//
// UserDefaults-backed persistence for which sample each grid pad
// plays, per AppMode. Follows the SampleSettingsStore pattern: one
// JSON blob under one namespaced key, storeVersion tag, corrupt
// blobs replaced with defaults on next write.
//
// Wire shape: [AppMode.rawValue: [String(PadIndex.rawValue): PadSlot]]
// — string keys because JSON dictionaries require them, and PadIndex
// rawValues (11…88) round-trip losslessly through String(Int).
//
// Only pads the user has explicitly assigned appear here. Unassigned
// pads fall through to the active pack's layout (ModeCoordinator's
// resolution order: assignment → pack pad → empty).

import Foundation
import ToneForgeEngine

@MainActor
public final class PadAssignmentStore: ObservableObject {

    /// AppMode.rawValue → PadIndex.rawValue → slot. Auto-saved.
    @Published public private(set) var assignmentsByMode: [String: [Int: PadSlot]] = [:]

    private static let defaultsKey = "toneforge.padAssignments"
    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.assignmentsByMode = Self.load(from: defaults)
    }

    // MARK: - Queries

    /// The slot assigned to `(mode, padIdx)`, or nil (fall through to
    /// the pack layout). `padIdx` is a PadIndex rawValue (11…88).
    public func slot(mode: AppMode, padIdx: Int) -> PadSlot? {
        assignmentsByMode[mode.rawValue]?[padIdx]
    }

    /// All assignments for a mode, keyed by PadIndex rawValue.
    public func assignments(for mode: AppMode) -> [Int: PadSlot] {
        assignmentsByMode[mode.rawValue] ?? [:]
    }

    // MARK: - Mutations

    /// Assign (or clear, with nil) a pad. Persists immediately.
    public func assign(_ slot: PadSlot?, mode: AppMode, padIdx: Int) {
        var forMode = assignmentsByMode[mode.rawValue] ?? [:]
        if let slot {
            forMode[padIdx] = slot
        } else {
            forMode.removeValue(forKey: padIdx)
        }
        if forMode.isEmpty {
            assignmentsByMode.removeValue(forKey: mode.rawValue)
        } else {
            assignmentsByMode[mode.rawValue] = forMode
        }
        save()
    }

    /// Drop every assignment pointing at a deleted local sample —
    /// called by the delete flow so pads never dangle.
    public func removeAll(referencing id: UUID) {
        var changed = false
        for (mode, slots) in assignmentsByMode {
            let kept = slots.filter { $0.value.ref != .localSample(id: id) }
            if kept.count != slots.count {
                changed = true
                if kept.isEmpty {
                    assignmentsByMode.removeValue(forKey: mode)
                } else {
                    assignmentsByMode[mode] = kept
                }
            }
        }
        if changed { save() }
    }

    // MARK: - Persistence

    private struct Persisted: Codable {
        var storeVersion: Int
        /// Mode rawValue → String(PadIndex rawValue) → slot.
        var assignments: [String: [String: PadSlot]]
    }

    private static func load(from defaults: UserDefaults) -> [String: [Int: PadSlot]] {
        guard let data = defaults.data(forKey: defaultsKey),
              let persisted = try? JSONDecoder().decode(Persisted.self, from: data)
        else { return [:] }
        var out: [String: [Int: PadSlot]] = [:]
        for (mode, slots) in persisted.assignments {
            var forMode: [Int: PadSlot] = [:]
            for (key, slot) in slots {
                guard let padIdx = Int(key) else { continue }
                forMode[padIdx] = slot
            }
            if !forMode.isEmpty { out[mode] = forMode }
        }
        return out
    }

    private func save() {
        var wire: [String: [String: PadSlot]] = [:]
        for (mode, slots) in assignmentsByMode {
            wire[mode] = Dictionary(
                uniqueKeysWithValues: slots.map { (String($0.key), $0.value) }
            )
        }
        let payload = Persisted(storeVersion: 1, assignments: wire)
        if let data = try? JSONEncoder().encode(payload) {
            defaults.set(data, forKey: Self.defaultsKey)
        }
    }
}
