// LayerStore.swift
//
// On-disk persistence for `LayerTimeline` JSON files. Kept
// AVFoundation-free + injectable-FileManager so it exercises fully
// under SwiftPM tests. Layout (mirrors the bundle store pattern):
//
//   {appSupportRoot}/toneforge/layers/{analysisId}/{layerId}.json
//
// Read/write are atomic. Listing is best-effort (bad files are
// skipped, not surfaced as errors) so a single corrupt layer doesn't
// hide every other one from the user.

import Foundation

public final class LayerStore: @unchecked Sendable {

    /// Sentinel analysisId for song-less "sketch" layers (recorded on
    /// the Play tab with no bundle loaded). Uses the standard
    /// `layers/{analysisId}/` layout — the double-underscore name can
    /// never collide with a real backend analysis id.
    public static let sketchAnalysisId = "__sketch__"

    public enum StoreError: Error, LocalizedError {
        case rootUnavailable(String)
        case notFound(analysisId: String, layerId: String)

        public var errorDescription: String? {
            switch self {
            case .rootUnavailable(let msg):
                return "Layer store root unavailable: \(msg)"
            case .notFound(let a, let l):
                return "Layer \(a)/\(l) not found on disk"
            }
        }
    }

    /// Base directory (usually Application Support). If nil, the
    /// standard `.applicationSupportDirectory` is used on demand.
    private let root: URL?
    private let fileManager: FileManager

    public init(root: URL? = nil, fileManager: FileManager = .default) {
        self.root = root
        self.fileManager = fileManager
    }

    // MARK: - Paths

    /// `{root}/toneforge/layers/`. Created on first access.
    public func layersDir() throws -> URL {
        let base: URL
        if let root = root {
            base = root
        } else {
            base = try fileManager.url(
                for: .applicationSupportDirectory,
                in: .userDomainMask,
                appropriateFor: nil,
                create: true
            )
        }
        let dir = base
            .appendingPathComponent("toneforge", isDirectory: true)
            .appendingPathComponent("layers", isDirectory: true)
        try fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// `{layersDir}/{analysisId}/`. Created on first access.
    public func songDir(analysisId: String) throws -> URL {
        let dir = try layersDir().appendingPathComponent(analysisId, isDirectory: true)
        try fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// `{songDir}/{layerId}.json`. Does NOT create the file.
    public func layerJsonURL(analysisId: String, layerId: String) throws -> URL {
        try songDir(analysisId: analysisId)
            .appendingPathComponent("\(layerId).json")
    }

    // MARK: - Read / write

    /// Atomically write the timeline to disk.
    public func save(_ timeline: LayerTimeline) throws {
        let url = try layerJsonURL(
            analysisId: timeline.analysisId,
            layerId: timeline.layerId
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(timeline)
        try data.write(to: url, options: [.atomic])
    }

    /// Load a specific layer from disk.
    public func load(analysisId: String, layerId: String) throws -> LayerTimeline {
        let url = try layerJsonURL(analysisId: analysisId, layerId: layerId)
        guard fileManager.fileExists(atPath: url.path) else {
            throw StoreError.notFound(analysisId: analysisId, layerId: layerId)
        }
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(LayerTimeline.self, from: data)
    }

    /// Every layer for a song, sorted newest-first. Bad JSON files are
    /// skipped silently — one corrupt file must not hide the rest.
    public func list(analysisId: String) -> [LayerTimeline] {
        guard let dir = try? songDir(analysisId: analysisId) else { return [] }
        let contents = (try? fileManager.contentsOfDirectory(
            at: dir,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        )) ?? []
        var out: [LayerTimeline] = []
        let decoder = JSONDecoder()
        for url in contents where url.pathExtension.lowercased() == "json" {
            guard let data = try? Data(contentsOf: url),
                  let tl = try? decoder.decode(LayerTimeline.self, from: data)
            else { continue }
            out.append(tl)
        }
        return out.sorted { $0.createdAtEpoch > $1.createdAtEpoch }
    }

    /// Delete a layer file. No-op if it's already gone.
    public func delete(analysisId: String, layerId: String) throws {
        let url = try layerJsonURL(analysisId: analysisId, layerId: layerId)
        if fileManager.fileExists(atPath: url.path) {
            try fileManager.removeItem(at: url)
        }
    }

    /// Rename a saved layer. Rewrites the JSON in place; no filesystem
    /// rename because the file name is keyed by layerId, not name.
    public func rename(analysisId: String, layerId: String, to newName: String) throws {
        var updated = try load(analysisId: analysisId, layerId: layerId)
        updated.name = newName
        try save(updated)
    }
}
