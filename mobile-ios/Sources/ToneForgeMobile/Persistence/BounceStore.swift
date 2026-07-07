// BounceStore.swift
//
// Read-side browser over `Documents/bounces/` — the directory
// `AppState.bounceSession()` renders into (P6, D-015). Unlike the
// other stores there is no metadata sidecar: the audio files ARE the
// content, so listing is a plain directory scan (wav/m4a only),
// newest first by creation date, with per-file byte counts for the
// storage browser (P7).
//
// Root + FileManager are injectable — tests run against a temp
// directory (LayerStore pattern). @MainActor because it publishes
// the file list to SwiftUI; the files themselves are only ever
// enumerated/removed, never decoded, so nothing here needs to hop
// off-main.

import Foundation

@MainActor
public final class BounceStore: ObservableObject {

    /// One rendered bounce on disk.
    public struct BounceFile: Identifiable, Equatable, Sendable {
        public let url: URL
        public let createdAt: Date
        public let bytes: Int64

        public var id: URL { url }
        public var name: String { url.lastPathComponent }
    }

    /// The two extensions `BounceFormat` can produce (.wav /
    /// .m4aAAC256). Anything else in the directory is ignored —
    /// e.g. `.DS_Store` or a half-written temp file.
    private static let audioExtensions: Set<String> = ["wav", "m4a"]

    /// All bounces on disk, newest first. Reloaded from disk at init
    /// and maintained incrementally by delete/deleteAll.
    @Published public private(set) var bounces: [BounceFile] = []

    private let root: URL?
    private let fileManager: FileManager

    /// - Parameter root: base directory override for tests; nil =
    ///   the app's Documents directory.
    public init(root: URL? = nil, fileManager: FileManager = .default) {
        self.root = root
        self.fileManager = fileManager
        reload()
    }

    // MARK: - Paths

    /// `{Documents}/bounces/`. Created on first access — the same
    /// directory `bounceSession` creates before rendering.
    public func bouncesDir() throws -> URL {
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
        let dir = base.appendingPathComponent("bounces", isDirectory: true)
        try fileManager.createDirectory(
            at: dir, withIntermediateDirectories: true)
        return dir
    }

    // MARK: - Listing

    /// Re-scan the directory. Unreadable resource values degrade to
    /// zero-byte / distant-past rather than hiding the file.
    public func reload() {
        guard let dir = try? bouncesDir(),
              let contents = try? fileManager.contentsOfDirectory(
                  at: dir,
                  includingPropertiesForKeys: [.creationDateKey, .fileSizeKey],
                  options: [.skipsHiddenFiles]
              )
        else {
            bounces = []
            return
        }
        bounces = contents
            .filter { Self.audioExtensions.contains($0.pathExtension.lowercased()) }
            .map { url in
                let values = try? url.resourceValues(
                    forKeys: [.creationDateKey, .fileSizeKey])
                return BounceFile(
                    url: url,
                    createdAt: values?.creationDate ?? .distantPast,
                    bytes: Int64(values?.fileSize ?? 0)
                )
            }
            .sorted { $0.createdAt > $1.createdAt }
    }

    /// Total bytes across all listed bounces (storage browser, P7).
    public func totalBytes() -> Int64 {
        bounces.reduce(0) { $0 + $1.bytes }
    }

    // MARK: - Delete

    /// Remove one bounce. Only URLs matching the published list are
    /// honoured — this store never deletes outside its directory.
    /// Comparison resolves symlinks so a caller's `/var/…` URL still
    /// matches the scan's `/private/var/…` form (darwin tmp alias).
    public func delete(url: URL) {
        let target = url.resolvingSymlinksInPath()
        guard let match = bounces.first(where: {
            $0.url.resolvingSymlinksInPath() == target
        }) else { return }
        try? fileManager.removeItem(at: match.url)
        bounces.removeAll { $0.url == match.url }
    }

    /// Remove every listed bounce, then re-scan (anything written
    /// mid-delete survives and shows up in the fresh list).
    public func deleteAll() {
        for file in bounces {
            try? fileManager.removeItem(at: file.url)
        }
        reload()
    }
}
