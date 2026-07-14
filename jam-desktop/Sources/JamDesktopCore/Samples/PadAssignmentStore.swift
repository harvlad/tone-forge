// PadAssignmentStore.swift
//
// UserDefaults-backed persistence for custom pad assignments.
// Tracks which pads have been overridden with sequences, local
// samples, or pack pads (vs the default bundle chop grid).
//
// Wire format matches iOS PadAssignmentStore for cross-platform
// compatibility if sync is needed later.

import Foundation
import Observation

/// What a pad slot points to (when not using default bundle chops).
public enum PadSlotReference: Codable, Hashable, Sendable {
    /// A saved sequencer pattern.
    case sequence(patternId: UUID)

    /// A locally recorded sample (future).
    case localSample(id: UUID)

    /// A sample pack pad (future).
    case packPad(packId: String, padIdx: Int)

    // MARK: - Codable

    private enum CodingKeys: String, CodingKey {
        case type, patternId, id, packId, padIdx
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(String.self, forKey: .type)
        switch type {
        case "sequence":
            let patternId = try container.decode(UUID.self, forKey: .patternId)
            self = .sequence(patternId: patternId)
        case "localSample":
            let id = try container.decode(UUID.self, forKey: .id)
            self = .localSample(id: id)
        case "packPad":
            let packId = try container.decode(String.self, forKey: .packId)
            let padIdx = try container.decode(Int.self, forKey: .padIdx)
            self = .packPad(packId: packId, padIdx: padIdx)
        default:
            throw DecodingError.dataCorruptedError(
                forKey: .type,
                in: container,
                debugDescription: "Unknown PadSlotReference type: \(type)"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .sequence(let patternId):
            try container.encode("sequence", forKey: .type)
            try container.encode(patternId, forKey: .patternId)
        case .localSample(let id):
            try container.encode("localSample", forKey: .type)
            try container.encode(id, forKey: .id)
        case .packPad(let packId, let padIdx):
            try container.encode("packPad", forKey: .type)
            try container.encode(packId, forKey: .packId)
            try container.encode(padIdx, forKey: .padIdx)
        }
    }
}

@Observable
@MainActor
public final class PadAssignmentStore {

    /// Assignments keyed by pad index (0..<64 for 8x8 grid).
    public private(set) var assignments: [Int: PadSlotReference] = [:]

    private static let defaultsKey = "jamdesktop.padAssignments"
    @ObservationIgnored private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.assignments = Self.load(from: defaults)
    }

    // MARK: - Queries

    /// Get the assignment for a pad, or nil if using default.
    public func slot(padIdx: Int) -> PadSlotReference? {
        assignments[padIdx]
    }

    /// All pads with sequence assignments.
    public func sequencePads() -> [(padIdx: Int, patternId: UUID)] {
        assignments.compactMap { padIdx, ref in
            if case .sequence(let patternId) = ref {
                return (padIdx, patternId)
            }
            return nil
        }
    }

    // MARK: - Mutations

    /// Assign a reference to a pad, or nil to clear.
    public func assign(_ ref: PadSlotReference?, padIdx: Int) {
        if let ref {
            assignments[padIdx] = ref
        } else {
            assignments.removeValue(forKey: padIdx)
        }
        persist()
    }

    /// Clear all assignments.
    public func clearAll() {
        assignments.removeAll()
        persist()
    }

    // MARK: - Persistence

    private struct Persisted: Codable {
        var storeVersion: Int
        /// String(padIdx) -> reference.
        var assignments: [String: PadSlotReference]
    }

    private static func load(from defaults: UserDefaults) -> [Int: PadSlotReference] {
        guard let data = defaults.data(forKey: defaultsKey),
              let persisted = try? JSONDecoder().decode(Persisted.self, from: data)
        else { return [:] }

        var out: [Int: PadSlotReference] = [:]
        for (key, ref) in persisted.assignments {
            guard let padIdx = Int(key) else { continue }
            out[padIdx] = ref
        }
        return out
    }

    private func persist() {
        let wire = Dictionary(
            uniqueKeysWithValues: assignments.map { (String($0.key), $0.value) }
        )
        let payload = Persisted(storeVersion: 1, assignments: wire)
        do {
            let data = try JSONEncoder().encode(payload)
            defaults.set(data, forKey: Self.defaultsKey)
        } catch {
            NSLog("[PadAssignmentStore] Encode failed: %@", error.localizedDescription)
        }
    }
}
