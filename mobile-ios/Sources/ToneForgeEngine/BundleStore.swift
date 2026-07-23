// BundleStore.swift
//
// Local cache for downloaded bundles + stems. The Perform experience
// wants offline playback — once a bundle is downloaded, we should be
// able to close the app, lose signal, and still play the song. So we:
//
//   - Persist the bundle JSON to Application Support (survives OS
//     eviction; small footprint).
//   - Persist each stem file to Caches (larger, OS may evict when
//     storage pressure is high; we redownload on demand).
//
// Directory layout:
//   Application Support/toneforge/bundles/{analysisId}/bundle.json
//   Caches/toneforge/stems/{analysisId}/{role}.{ext}
//
// Download progress is streamed via AsyncStream so LibraryView can
// paint per-stem progress bars.

import Foundation

/// Local persistence for `SongBundle` + stems. Not a singleton — the
/// app can hold one per profile if we ever add multi-user support.
public final class BundleStore: @unchecked Sendable {

    /// Per-stem download progress. `role` matches the bundle's stem
    /// role ("drums", "bass", "other", "vocals", "mix", …).
    public struct StemProgress: Sendable, Equatable {
        public let role: String
        public let bytesDownloaded: Int64
        public let bytesTotal: Int64
        public let isComplete: Bool
        public let localURL: URL?
    }

    public enum BundleError: Error, LocalizedError {
        case noStems
        case invalidURL(String)
        case httpStatus(Int)

        public var errorDescription: String? {
            switch self {
            case .noStems: return "Bundle has no stems"
            case .invalidURL(let s): return "Invalid stem URL: \(s)"
            case .httpStatus(let c): return "HTTP \(c)"
            }
        }
    }

    private let fileManager: FileManager
    private let session: URLSession
    /// Test seam: when set, bundles/stems live under this directory
    /// instead of Application Support/Caches.
    private let rootOverride: URL?

    public init(
        fileManager: FileManager = .default,
        session: URLSession = BundleStore.makeStemSession(),
        rootOverride: URL? = nil
    ) {
        self.fileManager = fileManager
        self.session = session
        self.rootOverride = rootOverride
    }

    /// Dedicated session for stem downloads with real timeouts. The
    /// default `.shared` session has a 7-day `timeoutIntervalForResource`,
    /// so a stalled stem transfer would hang the whole download stream
    /// forever — `isDownloading` never clears and every Library row is
    /// `.disabled` on it, freezing the UI. These ceilings turn a stall
    /// into a normal error the caller can surface and recover from.
    public static func makeStemSession() -> URLSession {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 30    // gap between data packets
        cfg.timeoutIntervalForResource = 300  // whole-stem ceiling
        cfg.waitsForConnectivity = false
        return URLSession(configuration: cfg)
    }

    // MARK: - Directory helpers

    /// Application Support root for our JSON bundles. Created on
    /// first access.
    public func bundlesDir() throws -> URL {
        let base = try rootOverride ?? fileManager.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        let dir = base.appendingPathComponent("toneforge", isDirectory: true)
            .appendingPathComponent("bundles", isDirectory: true)
        try fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// Caches root for downloaded stems. Created on first access.
    public func stemsDir() throws -> URL {
        let base = try rootOverride ?? fileManager.url(
            for: .cachesDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        let dir = base.appendingPathComponent("toneforge", isDirectory: true)
            .appendingPathComponent("stems", isDirectory: true)
        try fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    public func bundleJsonURL(analysisId: String) throws -> URL {
        try bundlesDir()
            .appendingPathComponent(analysisId, isDirectory: true)
    }

    public func stemLocalURL(analysisId: String, role: String, ext: String) throws -> URL {
        let dir = try stemsDir().appendingPathComponent(analysisId, isDirectory: true)
        try fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("\(role).\(ext)")
    }

    // MARK: - Bundle JSON persistence

    /// Save the fetched bundle so subsequent launches can open it
    /// offline.
    public func saveBundle(_ bundle: SongBundle) throws {
        let dir = try bundleJsonURL(analysisId: bundle.analysisId)
        try fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
        let json = dir.appendingPathComponent("bundle.json")
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(bundle)
        try data.write(to: json, options: [.atomic])
    }

    /// Look for a persisted bundle by analysisId. Returns nil if none.
    public func loadBundle(analysisId: String) throws -> SongBundle? {
        let json = try bundleJsonURL(analysisId: analysisId)
            .appendingPathComponent("bundle.json")
        guard fileManager.fileExists(atPath: json.path) else { return nil }
        let data = try Data(contentsOf: json)
        return try JSONDecoder().decode(SongBundle.self, from: data)
    }

    /// List every persisted bundle. Sorted by title for the Library
    /// view. If a bundle's JSON is corrupt it's skipped silently — a
    /// caller wanting stricter guarantees can call `loadBundle` per id.
    public func listLocalBundles() throws -> [SongBundle] {
        let dir = try bundlesDir()
        let contents = try fileManager.contentsOfDirectory(
            at: dir,
            includingPropertiesForKeys: nil
        )
        var bundles: [SongBundle] = []
        for entry in contents where entry.hasDirectoryPath {
            let json = entry.appendingPathComponent("bundle.json")
            guard fileManager.fileExists(atPath: json.path),
                  let data = try? Data(contentsOf: json),
                  let bundle = try? JSONDecoder().decode(SongBundle.self, from: data)
            else { continue }
            bundles.append(bundle)
        }
        return bundles.sorted { $0.meta.title.lowercased() < $1.meta.title.lowercased() }
    }

    // MARK: - Demo seeding (PERFORM_PARITY spec 3.1)

    /// One-shot marker so demo seeding runs once per marker version and
    /// user deletions are never undone on the next launch. Lives under
    /// `.../toneforge/` (the parent of bundlesDir).
    private func seedMarkerURL(version: String) throws -> URL {
        try bundlesDir()
            .deletingLastPathComponent()
            .appendingPathComponent("demos-seeded-\(version)")
    }

    /// Seed pre-analyzed demo songs shipped in the app on first launch so
    /// the Library is never empty and the first tap plays fully offline.
    ///
    /// `resourceRoot` holds one subdirectory per demo, each laid out as:
    ///   `{id}/bundle.json`
    ///   `{id}/stems/{role}.{ext}`
    /// The bundle JSON is copied into the normal bundle store and each
    /// stem into the stem cache, so `listLocalBundles()` + `cachedStem`
    /// find them exactly like a downloaded song.
    ///
    /// Idempotent: guarded by a one-shot marker keyed on `markerVersion`,
    /// so removing a demo in the Library sticks. Bump `markerVersion`
    /// only to force a re-seed after shipping new demo content. Returns
    /// the analysis ids seeded this call (empty when already seeded).
    @discardableResult
    public func seedBundledDemos(
        from resourceRoot: URL,
        markerVersion: String = "v1"
    ) -> [String] {
        guard let marker = try? seedMarkerURL(version: markerVersion) else { return [] }
        if fileManager.fileExists(atPath: marker.path) { return [] }

        var seeded: [String] = []
        let entries = (try? fileManager.contentsOfDirectory(
            at: resourceRoot,
            includingPropertiesForKeys: nil
        )) ?? []

        for entry in entries where entry.hasDirectoryPath {
            let srcJson = entry.appendingPathComponent("bundle.json")
            guard let data = try? Data(contentsOf: srcJson),
                  let bundle = try? JSONDecoder().decode(SongBundle.self, from: data)
            else { continue }

            do {
                try saveBundle(bundle)
                let stemsSrc = entry.appendingPathComponent("stems", isDirectory: true)
                for stem in bundle.stems {
                    let ext = codecExtension(from: stem)
                    let src = stemsSrc.appendingPathComponent("\(stem.role).\(ext)")
                    guard fileManager.fileExists(atPath: src.path) else { continue }
                    let dst = try stemLocalURL(
                        analysisId: bundle.analysisId, role: stem.role, ext: ext
                    )
                    if !fileManager.fileExists(atPath: dst.path) {
                        try fileManager.copyItem(at: src, to: dst)
                    }
                }
                seeded.append(bundle.analysisId)
            } catch {
                continue
            }
        }

        // Write the marker even when nothing was seeded (empty/absent
        // resource dir) so we don't rescan the app bundle every launch.
        try? Data().write(to: marker)
        return seeded
    }

    // MARK: - Local deletion

    /// Remove the persisted bundle JSON dir + cached stems for one
    /// analysis. Best-effort — missing directories are fine.
    public func deleteLocal(analysisId: String) {
        if let bundleDir = try? bundleJsonURL(analysisId: analysisId) {
            try? fileManager.removeItem(at: bundleDir)
        }
        if let stemDir = try? stemsDir()
            .appendingPathComponent(analysisId, isDirectory: true) {
            try? fileManager.removeItem(at: stemDir)
        }
    }

    /// Wipe every locally persisted bundle + stem cache. Best-effort.
    /// The parent dirs are recreated lazily by the directory helpers.
    public func deleteAllLocal() {
        if let dir = try? bundlesDir() {
            try? fileManager.removeItem(at: dir)
        }
        if let dir = try? stemsDir() {
            try? fileManager.removeItem(at: dir)
        }
    }

    // MARK: - Cache eviction

    /// Default total-size cap for the stem cache. iOS may also purge
    /// Caches under storage pressure; this keeps us from being the
    /// reason it has to.
    public static let defaultStemCacheLimitBytes: Int64 = 2 << 30  // 2 GiB

    /// Evict least-recently-used per-analysis stem directories until
    /// the cache fits ``maxBytes``. Recency is the directory's
    /// modification date, which ``touchStemDir`` bumps on cache hits
    /// and downloads. Ids in ``keeping`` are never evicted (the song
    /// just downloaded / currently loaded). Best-effort.
    @discardableResult
    public func enforceStemCacheLimit(
        maxBytes: Int64 = BundleStore.defaultStemCacheLimitBytes,
        keeping: Set<String> = []
    ) -> [String] {
        guard let root = try? stemsDir(),
              let entries = try? fileManager.contentsOfDirectory(
                  at: root,
                  includingPropertiesForKeys: [.contentModificationDateKey]
              )
        else { return [] }

        struct DirInfo {
            let url: URL
            let id: String
            let size: Int64
            let accessed: Date
        }
        var dirs: [DirInfo] = []
        var total: Int64 = 0
        for entry in entries where entry.hasDirectoryPath {
            let size = directorySize(entry)
            let accessed = (try? entry.resourceValues(
                forKeys: [.contentModificationDateKey]
            ).contentModificationDate) ?? .distantPast
            dirs.append(DirInfo(
                url: entry,
                id: entry.lastPathComponent,
                size: size,
                accessed: accessed
            ))
            total += size
        }
        guard total > maxBytes else { return [] }

        var evicted: [String] = []
        for dir in dirs.sorted(by: { $0.accessed < $1.accessed }) {
            if total <= maxBytes { break }
            guard !keeping.contains(dir.id) else { continue }
            try? fileManager.removeItem(at: dir.url)
            total -= dir.size
            evicted.append(dir.id)
        }
        return evicted
    }

    /// Bump the analysis's stem-dir modification date so LRU eviction
    /// prefers colder entries.
    private func touchStemDir(analysisId: String) {
        guard let dir = try? stemsDir()
            .appendingPathComponent(analysisId, isDirectory: true),
              fileManager.fileExists(atPath: dir.path)
        else { return }
        try? fileManager.setAttributes(
            [.modificationDate: Date()], ofItemAtPath: dir.path
        )
    }

    /// Recursive byte count of every regular file under ``url``.
    private func directorySize(_ url: URL) -> Int64 {
        guard let walker = fileManager.enumerator(
            at: url, includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey]
        ) else { return 0 }
        var total: Int64 = 0
        for case let file as URL in walker {
            guard let values = try? file.resourceValues(
                forKeys: [.fileSizeKey, .isRegularFileKey]
            ), values.isRegularFile == true else { continue }
            total += Int64(values.fileSize ?? 0)
        }
        return total
    }

    // MARK: - Stem downloads

    /// Local file URL if this stem is already fully downloaded, nil
    /// otherwise. "Fully downloaded" = file exists and matches the
    /// declared size, if we ever store one; for now, just existence.
    /// A hit refreshes the analysis's LRU recency.
    public func cachedStem(for stem: BundleStem, analysisId: String) -> URL? {
        let ext = codecExtension(from: stem)
        guard let url = try? stemLocalURL(analysisId: analysisId, role: stem.role, ext: ext),
              fileManager.fileExists(atPath: url.path)
        else { return nil }
        touchStemDir(analysisId: analysisId)
        return url
    }

    /// Download every stem in ``bundle`` in parallel. Yields per-stem
    /// progress + a terminal `isComplete: true` event with the local
    /// URL. Cached stems yield an immediate `isComplete` event without
    /// hitting the network.
    ///
    /// - Parameter baseURL: backend base used to resolve server-relative
    ///   stem URLs (e.g. `/api/admin/serve-file?path=...`). Absolute
    ///   stem URLs bypass resolution.
    public func download(
        bundle: SongBundle,
        baseURL: URL? = nil
    ) -> AsyncThrowingStream<StemProgress, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    try await withThrowingTaskGroup(of: Void.self) { group in
                        for stem in bundle.stems {
                            group.addTask {
                                try await self.downloadOne(
                                    stem: stem,
                                    analysisId: bundle.analysisId,
                                    baseURL: baseURL,
                                    continuation: continuation
                                )
                            }
                        }
                        try await group.waitForAll()
                    }
                    // Keep the cache bounded; never evict what we just
                    // fetched.
                    self.enforceStemCacheLimit(keeping: [bundle.analysisId])
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    // MARK: - Private

    private func downloadOne(
        stem: BundleStem,
        analysisId: String,
        baseURL: URL?,
        continuation: AsyncThrowingStream<StemProgress, Error>.Continuation
    ) async throws {
        let ext = codecExtension(from: stem)
        let localURL = try stemLocalURL(analysisId: analysisId, role: stem.role, ext: ext)

        // Cache hit: emit a single completion event and bail.
        if fileManager.fileExists(atPath: localURL.path) {
            let size = (try? fileManager.attributesOfItem(atPath: localURL.path)[.size]
                as? Int64) ?? 0
            continuation.yield(StemProgress(
                role: stem.role,
                bytesDownloaded: size,
                bytesTotal: size,
                isComplete: true,
                localURL: localURL
            ))
            return
        }

        guard let urlString = stem.url, !urlString.isEmpty else {
            throw BundleError.invalidURL("<nil>")
        }
        // Server-relative URLs (e.g. "/api/admin/serve-file?path=…")
        // resolve against the configured backend base. Absolute URLs
        // pass through unchanged — EXCEPT localhost URLs from the local
        // engine which need to be stripped and re-wrapped so the phone
        // doesn't try to hit its own loopback interface.
        let remote: URL
        let rewrittenURL = BundleStore.rewriteLocalhostURL(urlString)
        if rewrittenURL.hasPrefix("/"), let base = baseURL,
           let joined = URL(string: rewrittenURL, relativeTo: base) {
            remote = joined
        } else if let abs = URL(string: rewrittenURL), abs.scheme != nil {
            remote = abs
        } else {
            throw BundleError.invalidURL(urlString)
        }

        var request = URLRequest(url: remote)
        AuthContext.shared.apply(to: &request)
        let (tempURL, response) = try await session.download(for: request)
        defer { try? fileManager.removeItem(at: tempURL) }

        if let http = response as? HTTPURLResponse, http.statusCode >= 400 {
            throw BundleError.httpStatus(http.statusCode)
        }

        // Move into place atomically.
        if fileManager.fileExists(atPath: localURL.path) {
            try? fileManager.removeItem(at: localURL)
        }
        try fileManager.moveItem(at: tempURL, to: localURL)
        let size = (try? fileManager.attributesOfItem(atPath: localURL.path)[.size]
            as? Int64) ?? 0

        continuation.yield(StemProgress(
            role: stem.role,
            bytesDownloaded: size,
            bytesTotal: size,
            isComplete: true,
            localURL: localURL
        ))
    }

    /// Strip localhost URLs from the local engine and convert them to
    /// server-relative paths. The local engine writes URLs like:
    ///   http://127.0.0.1:7777/api/serve-file?path=/tmp/...
    /// On a phone, 127.0.0.1 is the phone itself, not the dev Mac. We
    /// extract the `path` query param and wrap it in our own relative
    /// serve-file endpoint.
    static func rewriteLocalhostURL(_ urlString: String) -> String {
        // Prefixes written by the local engine (see tone_forge_api.py).
        let localhostPrefixes = [
            "http://127.0.0.1:7777/api/serve-file?path=",
            "http://localhost:7777/api/serve-file?path="
        ]
        for prefix in localhostPrefixes {
            if urlString.hasPrefix(prefix) {
                let localPath = String(urlString.dropFirst(prefix.count))
                return "/api/admin/serve-file?path=\(localPath)"
            }
        }
        return urlString
    }

    /// Codec sniffer that mirrors `_codec_from_stem_url` on the backend
    /// but works off ``BundleStem`` fields. `stem.codec` is authoritative
    /// when present ("wav"|"m4a"|…); otherwise we fall back to sniffing
    /// the URL extension.
    private func codecExtension(from stem: BundleStem) -> String {
        let codec = stem.codec.trimmingCharacters(in: .whitespaces).lowercased()
        if !codec.isEmpty { return codec }
        // Fallback: strip any query string, take extension.
        guard let raw = stem.url, let url = URL(string: raw) else { return "bin" }
        let ext = url.pathExtension.lowercased()
        return ext.isEmpty ? "bin" : ext
    }
}
