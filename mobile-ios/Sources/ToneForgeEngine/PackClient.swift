// PackClient.swift
//
// HTTP client for curated remote sample packs. Mirrors `BundleStore`'s
// streaming-download shape so the UI can paint a progress bar while
// the pack downloads:
//
//   GET /api/sample-packs
//     → catalog list ([SamplePackCatalogEntry])
//   GET /api/sample-packs/{packId}
//     → pack manifest (SamplePack)
//   GET /api/sample-packs/{packId}/pads/{filename}
//     → individual pad audio bytes
//
// On-disk layout (matches `SampleBank.cachedPackDir`):
//
//   {cacheRoot}/{packId}/manifest.json
//   {cacheRoot}/{packId}/pads/{filename}
//
// After `download(...)` finishes, `SampleBank.loadCached(packId:)`
// succeeds and the pack activates like any other.
//
// Split from `BundleStore` because the units differ (packs are
// pad-count driven; bundles are role-driven) and the URL shape is
// different, but the resumability + parallel-fetch + AsyncStream
// pattern is intentionally identical so the UI code has one mental
// model.

import Foundation

// MARK: - Catalog entry

/// One row in the curated pack catalog. Matches the JSON emitted by
/// `GET /api/sample-packs` on the backend. `packId` is stable across
/// server restarts and used as the cache directory name + the
/// activation key.
public struct SamplePackCatalogEntry: Codable, Sendable, Equatable, Identifiable {
    public var id: String { packId }
    public let packId: String
    public let name: String
    public let family: SampleFamily
    public let paletteHint: String?
    public let tags: [String]
    /// Browse-filter facets (Phase 10). Older catalogs omit them —
    /// they decode to empty and the pack simply matches no
    /// genre/mood chips.
    public let genres: [String]
    public let moods: [String]
    /// Approximate download size in bytes. Nil if the server hasn't
    /// pre-computed it — the client will emit progress in "pads
    /// completed" units in that case.
    public let sizeBytes: Int64?
    public let coverUrl: String?
    /// Relative or absolute URL of a short audio preview. Nil = no
    /// preview available (the UI hides the preview button).
    public let previewUrl: String?
    public let description: String?
    public let padCount: Int

    public init(
        packId: String,
        name: String,
        family: SampleFamily,
        paletteHint: String? = nil,
        tags: [String] = [],
        genres: [String] = [],
        moods: [String] = [],
        sizeBytes: Int64? = nil,
        coverUrl: String? = nil,
        previewUrl: String? = nil,
        description: String? = nil,
        padCount: Int
    ) {
        self.packId = packId
        self.name = name
        self.family = family
        self.paletteHint = paletteHint
        self.tags = tags
        self.genres = genres
        self.moods = moods
        self.sizeBytes = sizeBytes
        self.coverUrl = coverUrl
        self.previewUrl = previewUrl
        self.description = description
        self.padCount = padCount
    }

    // Defensive decoder: the backend catalog omits some fields as
    // `null`, and older catalogs may lack `tags`/`padCount`/`genres`/
    // `moods`/`previewUrl` entirely.
    private enum CodingKeys: String, CodingKey {
        case packId, name, family, paletteHint, tags, genres, moods,
             sizeBytes, coverUrl, previewUrl, description, padCount
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.packId = try c.decode(String.self, forKey: .packId)
        self.name = try c.decode(String.self, forKey: .name)
        self.family = try c.decodeIfPresent(SampleFamily.self, forKey: .family) ?? .mixed
        self.paletteHint = try c.decodeIfPresent(String.self, forKey: .paletteHint)
        self.tags = try c.decodeIfPresent([String].self, forKey: .tags) ?? []
        self.genres = try c.decodeIfPresent([String].self, forKey: .genres) ?? []
        self.moods = try c.decodeIfPresent([String].self, forKey: .moods) ?? []
        self.sizeBytes = try c.decodeIfPresent(Int64.self, forKey: .sizeBytes)
        self.coverUrl = try c.decodeIfPresent(String.self, forKey: .coverUrl)
        self.previewUrl = try c.decodeIfPresent(String.self, forKey: .previewUrl)
        self.description = try c.decodeIfPresent(String.self, forKey: .description)
        self.padCount = try c.decodeIfPresent(Int.self, forKey: .padCount) ?? 0
    }
}

// MARK: - Download progress

/// Emitted by ``PackClient/download(baseURL:packId:cacheRoot:)`` as
/// each pad file finishes. A single terminal event with
/// `isComplete == true` marks the pack fully cached and ready to
/// activate via `SampleBank.loadCached(packId:)`.
public struct PackDownloadProgress: Sendable, Equatable {
    public let packId: String
    /// Number of pad files fully written to disk so far.
    public let padsCompleted: Int
    /// Total pad files in the pack manifest.
    public let padsTotal: Int
    /// Best-effort byte count. Zero when the server didn't declare a
    /// size; UI should fall back to `padsCompleted/padsTotal`.
    public let bytesDownloaded: Int64
    public let bytesTotal: Int64
    public let isComplete: Bool
    /// Local URL of the pack's manifest.json once written. Present on
    /// the terminal event and on the initial manifest-persisted event.
    public let manifestLocalURL: URL?
    /// Root directory the pack was written into.
    public let packLocalDir: URL?

    public init(
        packId: String,
        padsCompleted: Int,
        padsTotal: Int,
        bytesDownloaded: Int64,
        bytesTotal: Int64,
        isComplete: Bool,
        manifestLocalURL: URL?,
        packLocalDir: URL?
    ) {
        self.packId = packId
        self.padsCompleted = padsCompleted
        self.padsTotal = padsTotal
        self.bytesDownloaded = bytesDownloaded
        self.bytesTotal = bytesTotal
        self.isComplete = isComplete
        self.manifestLocalURL = manifestLocalURL
        self.packLocalDir = packLocalDir
    }
}

// MARK: - Errors

public enum PackClientError: LocalizedError, Sendable {
    case invalidURL
    case invalidPackId(String)
    case httpStatus(Int)
    case padMissingFilename(padIdx: Int)

    public var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid pack URL."
        case .invalidPackId(let id):
            return "Invalid pack id '\(id)'."
        case .httpStatus(let code):
            return "Pack fetch failed with HTTP \(code)."
        case .padMissingFilename(let idx):
            return "Pad \(idx) has no filename in manifest."
        }
    }
}

// MARK: - Client

/// Reads the curated pack catalog and downloads packs into
/// `SampleBank`'s cache directory.
public final class PackClient: @unchecked Sendable {

    private let session: URLSession
    private let fileManager: FileManager

    public init(
        session: URLSession = .shared,
        fileManager: FileManager = .default
    ) {
        self.session = session
        self.fileManager = fileManager
    }

    // MARK: Catalog

    /// GET /api/sample-packs → `[SamplePackCatalogEntry]`.
    ///
    /// Server response is wrapped as `{ "packs": [...] }` to leave
    /// room for future top-level fields (pagination, featured, etc.).
    public func fetchCatalog(baseURL: URL) async throws -> [SamplePackCatalogEntry] {
        let url = baseURL.appendingPathComponent("api/sample-packs")
        let (data, response) = try await session.data(from: url)
        try Self.checkOk(response)
        struct Wrapper: Decodable { let packs: [SamplePackCatalogEntry] }
        return try JSONDecoder().decode(Wrapper.self, from: data).packs
    }

    // MARK: Manifest

    /// GET /api/sample-packs/{packId} → `SamplePack`.
    public func fetchManifest(
        baseURL: URL,
        packId: String
    ) async throws -> SamplePack {
        try Self.validatePackId(packId)
        let url = baseURL
            .appendingPathComponent("api/sample-packs")
            .appendingPathComponent(packId)
        let (data, response) = try await session.data(from: url)
        try Self.checkOk(response)
        return try JSONDecoder().decode(SamplePack.self, from: data)
    }

    // MARK: Download

    /// Download `packId` into `cacheRoot/{packId}/`. Yields one
    /// progress event per completed pad plus a terminal event with
    /// `isComplete == true`. If the pack is already fully cached
    /// (manifest + every pad file present) a single terminal event is
    /// emitted immediately without hitting the network.
    ///
    /// - Parameter cacheRoot: parent directory — usually the same
    ///   `cachedPacksRoot` the `SampleBank` was constructed with, so
    ///   `SampleBank.loadCached(packId:)` finds the result.
    public func download(
        baseURL: URL,
        packId: String,
        cacheRoot: URL
    ) -> AsyncThrowingStream<PackDownloadProgress, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    try Self.validatePackId(packId)
                    let packDir = cacheRoot.appendingPathComponent(packId, isDirectory: true)
                    let padsDir = packDir.appendingPathComponent("pads", isDirectory: true)
                    let manifestURL = packDir.appendingPathComponent("manifest.json")

                    // Fast path: fully cached. Sum sizes for parity
                    // with the streaming path so the UI shows a
                    // consistent progress payload.
                    if let cached = try self.detectFullyCached(
                        packDir: packDir,
                        manifestURL: manifestURL,
                        padsDir: padsDir
                    ) {
                        continuation.yield(PackDownloadProgress(
                            packId: packId,
                            padsCompleted: cached.padsTotal,
                            padsTotal: cached.padsTotal,
                            bytesDownloaded: cached.totalBytes,
                            bytesTotal: cached.totalBytes,
                            isComplete: true,
                            manifestLocalURL: manifestURL,
                            packLocalDir: packDir
                        ))
                        continuation.finish()
                        return
                    }

                    // Fetch manifest fresh (or re-fetch — safer than
                    // reading a partial cached copy).
                    let manifest = try await self.fetchManifest(
                        baseURL: baseURL, packId: packId
                    )

                    // Ensure directories, write manifest.
                    try self.fileManager.createDirectory(
                        at: padsDir, withIntermediateDirectories: true
                    )
                    let manifestData = try JSONEncoder().encode(manifest)
                    try manifestData.write(to: manifestURL, options: [.atomic])

                    let filePads = manifest.pads.filter {
                        ($0.filename ?? "").isEmpty == false
                    }
                    let padsTotal = filePads.count
                    let declaredTotal: Int64 = 0 // server does not
                    // pre-declare per-pad sizes in v1; we accumulate
                    // downloaded bytes and report that as the total
                    // once done.

                    // Initial progress: manifest saved, no pads yet.
                    continuation.yield(PackDownloadProgress(
                        packId: packId,
                        padsCompleted: 0,
                        padsTotal: padsTotal,
                        bytesDownloaded: 0,
                        bytesTotal: declaredTotal,
                        isComplete: false,
                        manifestLocalURL: manifestURL,
                        packLocalDir: packDir
                    ))

                    // Bookkeeping actor for concurrent completion
                    // accounting.
                    actor Tally {
                        var padsCompleted = 0
                        var bytesDownloaded: Int64 = 0
                        func record(bytes: Int64) -> (Int, Int64) {
                            padsCompleted += 1
                            bytesDownloaded += bytes
                            return (padsCompleted, bytesDownloaded)
                        }
                    }
                    let tally = Tally()

                    try await withThrowingTaskGroup(of: Void.self) { group in
                        for pad in filePads {
                            let filename = pad.filename ?? ""
                            let padIdx = pad.padIdx
                            group.addTask {
                                let bytes = try await self.downloadPad(
                                    baseURL: baseURL,
                                    packId: packId,
                                    padIdx: padIdx,
                                    filename: filename,
                                    padsDir: padsDir
                                )
                                let (completed, totalBytes) = await tally.record(bytes: bytes)
                                continuation.yield(PackDownloadProgress(
                                    packId: packId,
                                    padsCompleted: completed,
                                    padsTotal: padsTotal,
                                    bytesDownloaded: totalBytes,
                                    bytesTotal: declaredTotal,
                                    isComplete: false,
                                    manifestLocalURL: manifestURL,
                                    packLocalDir: packDir
                                ))
                            }
                        }
                        try await group.waitForAll()
                    }

                    // Terminal event.
                    let finalBytes = await tally.bytesDownloaded
                    continuation.yield(PackDownloadProgress(
                        packId: packId,
                        padsCompleted: padsTotal,
                        padsTotal: padsTotal,
                        bytesDownloaded: finalBytes,
                        bytesTotal: finalBytes,
                        isComplete: true,
                        manifestLocalURL: manifestURL,
                        packLocalDir: packDir
                    ))
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    // MARK: - Private

    /// Sum sizes of manifest + all pad files if the pack is fully on
    /// disk; nil otherwise. A single missing pad forces a re-download
    /// of the whole pack — simpler than partial-recovery logic, and
    /// packs are small.
    private func detectFullyCached(
        packDir: URL,
        manifestURL: URL,
        padsDir: URL
    ) throws -> (padsTotal: Int, totalBytes: Int64)? {
        guard fileManager.fileExists(atPath: manifestURL.path) else { return nil }
        let data = try Data(contentsOf: manifestURL)
        let manifest: SamplePack
        do {
            manifest = try JSONDecoder().decode(SamplePack.self, from: data)
        } catch {
            return nil
        }
        let filePads = manifest.pads.filter { ($0.filename ?? "").isEmpty == false }
        var total: Int64 = 0
        total += Self.fileSize(at: manifestURL) ?? 0
        for pad in filePads {
            guard let filename = pad.filename else { continue }
            let padURL = padsDir.appendingPathComponent(filename)
            guard fileManager.fileExists(atPath: padURL.path),
                  let sz = Self.fileSize(at: padURL) else { return nil }
            total += sz
        }
        return (filePads.count, total)
    }

    /// Download one pad audio file, atomic-move into `padsDir`,
    /// return its size in bytes.
    private func downloadPad(
        baseURL: URL,
        packId: String,
        padIdx: Int,
        filename: String,
        padsDir: URL
    ) async throws -> Int64 {
        guard !filename.isEmpty else {
            throw PackClientError.padMissingFilename(padIdx: padIdx)
        }
        // Reject filename tricks — the backend's own path-traversal
        // guard rejects "/" and "..", but iOS should assume the server
        // is untrusted and enforce again.
        if filename.contains("/") || filename.contains("\\") || filename.contains("..") {
            throw PackClientError.padMissingFilename(padIdx: padIdx)
        }
        let url = baseURL
            .appendingPathComponent("api/sample-packs")
            .appendingPathComponent(packId)
            .appendingPathComponent("pads")
            .appendingPathComponent(filename)

        let (tempURL, response) = try await session.download(from: url)
        defer { try? fileManager.removeItem(at: tempURL) }
        try Self.checkOk(response)

        let dest = padsDir.appendingPathComponent(filename)
        if fileManager.fileExists(atPath: dest.path) {
            try? fileManager.removeItem(at: dest)
        }
        try fileManager.moveItem(at: tempURL, to: dest)
        return Self.fileSize(at: dest) ?? 0
    }

    private static func fileSize(at url: URL) -> Int64? {
        let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
        return (attrs?[.size] as? NSNumber)?.int64Value
    }

    private static func checkOk(_ response: URLResponse) throws {
        if let http = response as? HTTPURLResponse,
           !(200..<300).contains(http.statusCode) {
            throw PackClientError.httpStatus(http.statusCode)
        }
    }

    private static func validatePackId(_ id: String) throws {
        if id.isEmpty || id.contains("/") || id.contains("\\") || id.contains("..") {
            throw PackClientError.invalidPackId(id)
        }
    }
}
