// BeatModelStore.swift
//
// Beat Capture (D-024): resolves which compiled drum-classifier model
// the runtime should use. A downloaded model cached under
// Caches/toneforge/beat-model/<version>/ takes precedence over the
// bundled baseline (see BeatModel). Phase 1 only reads the cache; the
// downloader that populates it lives in BeatModelClient (Phase 3).

import Foundation

/// Local cache of downloaded drum-classifier models, newest-wins.
public enum BeatModelStore {

    /// Compiled-model filename inside each version directory.
    static let modelFilename = "BeatClassifier.mlmodelc"

    /// Root cache directory: Caches/toneforge/beat-model/.
    public static func cacheRoot() -> URL? {
        guard let caches = FileManager.default.urls(
            for: .cachesDirectory, in: .userDomainMask
        ).first else { return nil }
        return caches
            .appendingPathComponent("toneforge", isDirectory: true)
            .appendingPathComponent("beat-model", isDirectory: true)
    }

    /// True when `version` is already fully installed in the cache (its
    /// compiled-model directory exists), so the downloader can skip it.
    public static func isCached(version: String) -> Bool {
        guard let root = cacheRoot() else { return false }
        let model = root
            .appendingPathComponent(version, isDirectory: true)
            .appendingPathComponent(modelFilename, isDirectory: true)
        return FileManager.default.fileExists(atPath: model.path)
    }

    /// URL of the newest cached compiled model, or nil when none has
    /// been downloaded yet (callers then fall back to `BeatModel`).
    /// Versions are compared as strings (ISO-8601 timestamps sort
    /// lexicographically), newest last.
    public static func activeModelURL() -> URL? {
        guard let root = cacheRoot(),
              let entries = try? FileManager.default.contentsOfDirectory(
                at: root, includingPropertiesForKeys: nil
              ) else { return nil }
        let versions = entries
            .filter { (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        for dir in versions.reversed() {
            let model = dir.appendingPathComponent(modelFilename, isDirectory: true)
            if FileManager.default.fileExists(atPath: model.path) {
                return model
            }
        }
        return nil
    }
}
