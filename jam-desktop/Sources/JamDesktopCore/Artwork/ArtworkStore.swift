// ArtworkStore.swift
//
// Album-art persistence for songs. Stores JPEG at:
//   App Support/toneforge/artwork/{analysisId}.jpg
//
// Port of mobile ArtworkStore; Foundation-only.

import Foundation

public struct ArtworkStore: Sendable {

    private let root: URL

    public init(root: URL? = nil) {
        if let root {
            self.root = root
        } else {
            let appSupport = FileManager.default.urls(
                for: .applicationSupportDirectory, in: .userDomainMask
            )[0]
            self.root = appSupport
                .appendingPathComponent("toneforge", isDirectory: true)
                .appendingPathComponent("artwork", isDirectory: true)
        }
    }

    public func url(for analysisId: String) -> URL {
        root.appendingPathComponent(
            Self.sanitize(analysisId) + ".jpg", isDirectory: false
        )
    }

    @discardableResult
    public func save(_ data: Data, analysisId: String) throws -> URL {
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: true
        )
        let dest = url(for: analysisId)
        try data.write(to: dest, options: .atomic)
        return dest
    }

    public func imageData(for analysisId: String) -> Data? {
        try? Data(contentsOf: url(for: analysisId))
    }

    public func delete(analysisId: String) {
        try? FileManager.default.removeItem(at: url(for: analysisId))
    }

    static func sanitize(_ id: String) -> String {
        id.replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "\\", with: "_")
            .replacingOccurrences(of: ":", with: "_")
    }
}
