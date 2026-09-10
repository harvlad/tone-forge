// ArrangementStore.swift
//
// UserDefaults-backed persistence for the live-capture arrangement, keyed by
// analysisId — the desktop twin of the web's localStorage
// `jamn.arrangement.<id>` (kit.js). One blob under "jamdesktop.arrangements"
// holds analysisId → the web-compatible serialized map
// (Arrangement.serialize / .parse), so a capture round-trips identically
// across surfaces.
//
// The engine ships the pure Arrangement helpers; this store is a desktop
// original mirroring ChopEditStore's versioned-blob pattern.

import Foundation
import Observation
import ToneForgeEngine

@Observable
@MainActor
public final class ArrangementStore {

    /// analysisId → serialized capture JSON (the web `jamn.arrangement.<id>` blob).
    public private(set) var songs: [String: String] = [:]

    private static let defaultsKey = "jamdesktop.arrangements"
    @ObservationIgnored private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.songs = Self.load(from: defaults)
    }

    /// The captured map for a song ({} when none saved).
    public func captured(analysisId: String) -> [Int: [Int]] {
        guard let json = songs[analysisId] else { return [:] }
        return Arrangement.parse(json) ?? [:]
    }

    /// Persist a song's capture. An empty/degenerate map removes the entry
    /// (serialize returns nil), matching the web's removeItem.
    public func save(_ map: [Int: [Int]], analysisId: String) {
        if let json = Arrangement.serialize(map) {
            songs[analysisId] = json
        } else {
            songs.removeValue(forKey: analysisId)
        }
        persist()
    }

    // MARK: - Persistence

    private struct Persisted: Codable {
        var storeVersion: Int
        var songs: [String: String]
    }

    private static func load(from defaults: UserDefaults) -> [String: String] {
        guard let data = defaults.data(forKey: defaultsKey),
              let persisted = try? JSONDecoder().decode(Persisted.self, from: data)
        else { return [:] }
        return persisted.songs
    }

    private func persist() {
        let payload = Persisted(storeVersion: 1, songs: songs)
        if let data = try? JSONEncoder().encode(payload) {
            defaults.set(data, forKey: Self.defaultsKey)
        }
    }
}
