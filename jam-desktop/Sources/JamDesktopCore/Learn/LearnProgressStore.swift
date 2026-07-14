// LearnProgressStore.swift
//
// JSON persistence for Learn mode progress (SongLearnProgress per
// analysisId). Desktop port of the mobile LearnProgressStore pattern:
// ~/Library/Application Support/toneforge/learnProgress/{analysisId}.json.
//
// Each file is a single SongLearnProgress record. Section keys are
// lowercased labels — same normalisation as iOS — so backend
// capitalisation churn doesn't fork history.

import Foundation
import ToneForgeEngine

@MainActor
public final class LearnProgressStore {

    private let root: URL
    private let fileManager: FileManager
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    public init(
        root: URL? = nil,
        fileManager: FileManager = .default
    ) {
        if let root {
            self.root = root
        } else {
            let appSupport = fileManager.urls(
                for: .applicationSupportDirectory,
                in: .userDomainMask
            ).first ?? fileManager.temporaryDirectory
            self.root = appSupport
                .appendingPathComponent("toneforge", isDirectory: true)
                .appendingPathComponent("learnProgress", isDirectory: true)
        }
        self.fileManager = fileManager
        try? fileManager.createDirectory(
            at: self.root, withIntermediateDirectories: true)
    }

    private func url(analysisId: String) -> URL {
        root.appendingPathComponent("\(analysisId).json")
    }

    /// Load progress for a song; nil when not on disk or corrupt.
    public func load(analysisId: String) -> SongLearnProgress? {
        let path = url(analysisId: analysisId)
        guard let data = try? Data(contentsOf: path) else { return nil }
        return try? decoder.decode(SongLearnProgress.self, from: data)
    }

    /// Persist progress. Overwrites existing file.
    public func save(_ progress: SongLearnProgress) throws {
        let data = try encoder.encode(progress)
        try data.write(to: url(analysisId: progress.analysisId), options: .atomic)
    }

    /// Delete progress for a song. Idempotent.
    public func delete(analysisId: String) {
        try? fileManager.removeItem(at: url(analysisId: analysisId))
    }
}
