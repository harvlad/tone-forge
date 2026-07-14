// SequencerPatternStore.swift
//
// UserDefaults-backed persistence for saved sequencer patterns. A saved
// pattern can be assigned to a pad (PadSampleReference.sequence) so that
// pressing the pad plays the whole sequence.
//
// Follows the PadAssignmentStore pattern: one JSON blob under one
// namespaced key, storeVersion tag, corrupt blobs replaced on next write.
//
// Wire shape: [String(UUID): SequencerPattern] — string keys because JSON
// dictionaries require them.

import Foundation
import ToneForgeEngine

@MainActor
public final class SequencerPatternStore: ObservableObject {

    /// Saved patterns keyed by pattern id. Auto-saved on mutation.
    @Published public private(set) var patterns: [UUID: SequencerPattern] = [:]

    private static let defaultsKey = "toneforge.sequencerPatterns"
    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.patterns = Self.load(from: defaults)
    }

    // MARK: - Queries

    /// The saved pattern with this id, or nil.
    public func pattern(id: UUID) -> SequencerPattern? {
        patterns[id]
    }

    /// All saved patterns, sorted by name (case-insensitive).
    public func all() -> [SequencerPattern] {
        patterns.values.sorted {
            $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
        }
    }

    // MARK: - Mutations

    /// Save (insert or update, keyed by the pattern's id). Persists immediately.
    public func save(_ pattern: SequencerPattern) {
        patterns[pattern.id] = pattern
        persist()
    }

    /// Delete a saved pattern. Persists immediately.
    public func delete(id: UUID) {
        guard patterns.removeValue(forKey: id) != nil else { return }
        persist()
    }

    // MARK: - Persistence

    private struct Persisted: Codable {
        var storeVersion: Int
        /// String(UUID) → pattern.
        var patterns: [String: SequencerPattern]
    }

    private static func load(from defaults: UserDefaults) -> [UUID: SequencerPattern] {
        guard let data = defaults.data(forKey: defaultsKey),
              let persisted = try? JSONDecoder().decode(Persisted.self, from: data)
        else { return [:] }
        var out: [UUID: SequencerPattern] = [:]
        for (key, pattern) in persisted.patterns {
            guard let id = UUID(uuidString: key) else { continue }
            out[id] = pattern
        }
        return out
    }

    private func persist() {
        let wire = Dictionary(
            uniqueKeysWithValues: patterns.map { ($0.key.uuidString, $0.value) }
        )
        let payload = Persisted(storeVersion: 1, patterns: wire)
        if let data = try? JSONEncoder().encode(payload) {
            defaults.set(data, forKey: Self.defaultsKey)
        }
    }
}
