// ArtworkStore.swift
//
// Album-art persistence for imported songs. The backend bundle has no
// artwork field, so art is captured on-device at import time
// (MPMediaItemArtwork via MPMediaLibrarySource) and stored locally,
// keyed by analysisId:
//
//   Documents/toneforge/artwork/{analysisId}.jpg
//
// Foundation-only by design — the store hands back raw JPEG Data and
// lets the view layer decide how to decode/render it (UIImage on iOS,
// deterministic gradient fallback when no art exists). Files-app
// imports have no artwork source, so a missing file is the normal
// case, not an error.

import Foundation

public struct ArtworkStore: Sendable {

    private let root: URL

    /// Injectable root for tests; production callers use the no-arg
    /// init which resolves to Documents/toneforge/artwork.
    public init(root: URL? = nil) {
        if let root {
            self.root = root
        } else {
            let docs = FileManager.default.urls(
                for: .documentDirectory, in: .userDomainMask
            )[0]
            self.root = docs
                .appendingPathComponent("toneforge", isDirectory: true)
                .appendingPathComponent("artwork", isDirectory: true)
        }
    }

    /// Where the JPEG for `analysisId` lives (whether or not it
    /// exists yet).
    public func url(for analysisId: String) -> URL {
        root.appendingPathComponent(
            Self.sanitize(analysisId) + ".jpg", isDirectory: false
        )
    }

    /// Persist JPEG data for the song. Creates the directory on first
    /// use; atomic write so a crash can't leave a half-written image.
    @discardableResult
    public func save(_ data: Data, analysisId: String) throws -> URL {
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: true
        )
        let dest = url(for: analysisId)
        try data.write(to: dest, options: .atomic)
        return dest
    }

    /// Raw JPEG bytes, or nil when no art was captured for this song
    /// (Files-app imports, denied Music permission, artless tracks).
    public func imageData(for analysisId: String) -> Data? {
        try? Data(contentsOf: url(for: analysisId))
    }

    /// Remove the stored art (no-op when absent).
    public func delete(analysisId: String) {
        try? FileManager.default.removeItem(at: url(for: analysisId))
    }

    /// analysisIds are backend-issued UUID-like strings; defend
    /// against path separators anyway so a hostile id can't escape
    /// the artwork directory.
    static func sanitize(_ id: String) -> String {
        id.replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "\\", with: "_")
            .replacingOccurrences(of: ":", with: "_")
    }
}
