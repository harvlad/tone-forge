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

    public init(
        fileManager: FileManager = .default,
        session: URLSession = .shared
    ) {
        self.fileManager = fileManager
        self.session = session
    }

    // MARK: - Directory helpers

    /// Application Support root for our JSON bundles. Created on
    /// first access.
    public func bundlesDir() throws -> URL {
        let base = try fileManager.url(
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
        let base = try fileManager.url(
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

    // MARK: - Stem downloads

    /// Local file URL if this stem is already fully downloaded, nil
    /// otherwise. "Fully downloaded" = file exists and matches the
    /// declared size, if we ever store one; for now, just existence.
    public func cachedStem(for stem: BundleStem, analysisId: String) -> URL? {
        let ext = codecExtension(from: stem)
        guard let url = try? stemLocalURL(analysisId: analysisId, role: stem.role, ext: ext),
              fileManager.fileExists(atPath: url.path)
        else { return nil }
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

        let (tempURL, response) = try await session.download(from: remote)
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
