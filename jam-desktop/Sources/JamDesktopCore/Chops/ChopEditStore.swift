// ChopEditStore.swift
//
// UserDefaults-backed persistence for chop boundary edits (D-023),
// keyed (analysisId, presetKey). The bundle stays immutable —
// re-analysis overwrites cleanly — while edits overlay at runtime
// via ToneForgeEngine.resolvedChops(bundleChops:edits:).
//
// iOS keeps this store in the app layer (the engine only ships the
// ChopEdits model + resolution), so this is a desktop original with
// the same wire model. onEditsChanged lets SessionController re-apply
// the resolved grid to the Launchpad and sequencer adapter without
// polling.

import Foundation
import Observation
import ToneForgeEngine

@Observable
@MainActor
public final class ChopEditStore {

    /// Edits per song: analysisId → presetKey → ChopEdits.
    public private(set) var songs: [String: [String: ChopEdits]] = [:]

    /// Fired with the analysisId after any mutation persists.
    @ObservationIgnored public var onEditsChanged: ((String) -> Void)?

    private static let defaultsKey = "jamdesktop.chopEdits"
    @ObservationIgnored private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.songs = Self.load(from: defaults)
    }

    // MARK: - Queries

    /// Edits for one preset (empty collection when none saved).
    public func edits(analysisId: String, presetKey: String) -> ChopEdits {
        songs[analysisId]?[presetKey] ?? ChopEdits(presetKey: presetKey)
    }

    /// All presets' edits for a song.
    public func edits(analysisId: String) -> [String: ChopEdits] {
        songs[analysisId] ?? [:]
    }

    // MARK: - Mutations

    /// Save one preset's edit collection. Empty collections are
    /// removed instead of stored.
    public func save(_ edits: ChopEdits, analysisId: String) {
        if edits.hasEdits {
            songs[analysisId, default: [:]][edits.presetKey] = edits
        } else {
            songs[analysisId]?.removeValue(forKey: edits.presetKey)
            if songs[analysisId]?.isEmpty == true {
                songs.removeValue(forKey: analysisId)
            }
        }
        persist()
        onEditsChanged?(analysisId)
    }

    /// Upsert a single boundary edit. Reverting to the original
    /// boundaries drops the entry (isModified == false).
    public func setBoundary(
        _ edit: ChopBoundaryEdit, analysisId: String, presetKey: String
    ) {
        var collection = edits(analysisId: analysisId, presetKey: presetKey)
        if edit.isModified {
            collection.boundaryEdits[edit.chopIndex] = edit
        } else {
            collection.boundaryEdits.removeValue(forKey: edit.chopIndex)
        }
        save(collection, analysisId: analysisId)
    }

    /// Drop all edits for one preset.
    public func reset(analysisId: String, presetKey: String) {
        save(ChopEdits(presetKey: presetKey), analysisId: analysisId)
    }

    // MARK: - Persistence

    private struct Persisted: Codable {
        var storeVersion: Int
        var songs: [String: [String: ChopEdits]]
    }

    private static func load(from defaults: UserDefaults) -> [String: [String: ChopEdits]] {
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
