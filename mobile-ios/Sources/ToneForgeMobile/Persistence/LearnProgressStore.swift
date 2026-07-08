// LearnProgressStore.swift
//
// Persists SongLearnProgress as JSON under
// `Documents/learnProgress/{analysisId}.json` (redesign Phase 8).
// Same policy as SessionStore: injectable root for tests, atomic
// writes, prettyPrinted + sortedKeys for clean diffs, and corrupt or
// missing files simply read back as nil — the controller starts a
// fresh record rather than crashing on bad JSON.
//
// analysisId is backend-issued and already used verbatim in cache
// paths (stems, artwork), so it is safe as a filename.

import Foundation
import ToneForgeEngine

public final class LearnProgressStore {

    private let root: URL?
    private let fileManager: FileManager

    /// - Parameter root: base directory override for tests; nil =
    ///   the app's Documents directory.
    public init(root: URL? = nil, fileManager: FileManager = .default) {
        self.root = root
        self.fileManager = fileManager
    }

    // MARK: - Paths

    /// `{Documents}/learnProgress/`. Created on first access.
    public func learnProgressDir() throws -> URL {
        let base: URL
        if let root {
            base = root
        } else {
            base = try fileManager.url(
                for: .documentDirectory,
                in: .userDomainMask,
                appropriateFor: nil,
                create: true
            )
        }
        let dir = base.appendingPathComponent(
            "learnProgress", isDirectory: true)
        try fileManager.createDirectory(
            at: dir, withIntermediateDirectories: true)
        return dir
    }

    public func jsonURL(analysisId: String) throws -> URL {
        try learnProgressDir()
            .appendingPathComponent("\(analysisId).json")
    }

    // MARK: - CRUD

    public func save(_ progress: SongLearnProgress) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(progress)
        try data.write(
            to: jsonURL(analysisId: progress.analysisId),
            options: .atomic
        )
    }

    /// nil when no record exists or the file is corrupt — callers
    /// fall back to a fresh SongLearnProgress.
    public func load(analysisId: String) -> SongLearnProgress? {
        guard let url = try? jsonURL(analysisId: analysisId),
              let data = try? Data(contentsOf: url)
        else { return nil }
        return try? JSONDecoder().decode(SongLearnProgress.self, from: data)
    }

    public func delete(analysisId: String) throws {
        let url = try jsonURL(analysisId: analysisId)
        if fileManager.fileExists(atPath: url.path) {
            try fileManager.removeItem(at: url)
        }
    }
}
