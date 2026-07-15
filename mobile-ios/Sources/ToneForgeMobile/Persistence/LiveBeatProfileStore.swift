// LiveBeatProfileStore.swift
//
// UserDefaults-backed persistence for Live Beat calibration profiles.
// Each profile contains templates mapping physical sounds to drum roles.
//
// Follows the SequencerPatternStore pattern: one JSON blob under one
// namespaced key, storeVersion tag, corrupt blobs replaced on next write.

import Foundation
import ToneForgeEngine

@MainActor
public final class LiveBeatProfileStore: ObservableObject {

    /// Saved profiles keyed by profile id. Auto-saved on mutation.
    @Published public private(set) var profiles: [UUID: LiveBeatProfile] = [:]

    /// Currently active profile id.
    @Published public var activeProfileId: UUID?

    private static let defaultsKey = "toneforge.liveBeatProfiles"
    private static let activeKey = "toneforge.liveBeatActiveProfile"
    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.profiles = Self.load(from: defaults)
        self.activeProfileId = Self.loadActiveId(from: defaults)

        // Ensure default profile exists
        if profiles.isEmpty {
            let defaultProfile = LiveBeatProfile.heuristicDefault
            profiles[defaultProfile.id] = defaultProfile
            activeProfileId = defaultProfile.id
            persist()
        }
    }

    // MARK: - Queries

    /// The saved profile with this id, or nil.
    public func profile(id: UUID) -> LiveBeatProfile? {
        profiles[id]
    }

    /// The currently active profile, or nil.
    public var activeProfile: LiveBeatProfile? {
        activeProfileId.flatMap { profiles[$0] }
    }

    /// All saved profiles, sorted by name (case-insensitive).
    public func all() -> [LiveBeatProfile] {
        profiles.values.sorted {
            $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
        }
    }

    // MARK: - Mutations

    /// Save (insert or update, keyed by the profile's id). Persists immediately.
    public func save(_ profile: LiveBeatProfile) {
        profiles[profile.id] = profile
        persist()
    }

    /// Delete a saved profile. Persists immediately.
    /// If deleting the active profile, clears the selection.
    public func delete(id: UUID) {
        guard profiles.removeValue(forKey: id) != nil else { return }
        if activeProfileId == id {
            activeProfileId = profiles.keys.first
        }
        persist()
    }

    /// Set the active profile.
    public func setActive(id: UUID?) {
        activeProfileId = id
        defaults.set(id?.uuidString, forKey: Self.activeKey)
    }

    /// Create a new empty profile with the given name.
    public func createProfile(name: String) -> LiveBeatProfile {
        let profile = LiveBeatProfile(name: name)
        profiles[profile.id] = profile
        persist()
        return profile
    }

    /// Duplicate an existing profile.
    public func duplicate(_ profile: LiveBeatProfile, newName: String) -> LiveBeatProfile {
        var copy = profile
        copy = LiveBeatProfile(
            id: UUID(),
            name: newName,
            templates: profile.templates,
            sensitivity: profile.sensitivity
        )
        profiles[copy.id] = copy
        persist()
        return copy
    }

    // MARK: - Persistence

    private struct Persisted: Codable {
        var storeVersion: Int
        /// String(UUID) → profile.
        var profiles: [String: LiveBeatProfile]
    }

    private static func load(from defaults: UserDefaults) -> [UUID: LiveBeatProfile] {
        guard let data = defaults.data(forKey: defaultsKey),
              let persisted = try? JSONDecoder().decode(Persisted.self, from: data)
        else { return [:] }
        var out: [UUID: LiveBeatProfile] = [:]
        for (key, profile) in persisted.profiles {
            guard let id = UUID(uuidString: key) else { continue }
            out[id] = profile
        }
        return out
    }

    private static func loadActiveId(from defaults: UserDefaults) -> UUID? {
        defaults.string(forKey: activeKey).flatMap { UUID(uuidString: $0) }
    }

    private func persist() {
        let wire = Dictionary(
            uniqueKeysWithValues: profiles.map { ($0.key.uuidString, $0.value) }
        )
        let persisted = Persisted(storeVersion: 1, profiles: wire)
        if let data = try? JSONEncoder().encode(persisted) {
            defaults.set(data, forKey: Self.defaultsKey)
        }
    }
}
