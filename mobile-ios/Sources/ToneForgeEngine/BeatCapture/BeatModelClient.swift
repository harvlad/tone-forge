// BeatModelClient.swift
//
// Beat Capture (D-024): background-updates the drum-classifier model.
// The server distributes a compiled `.mlmodelc` *directory* as
// per-file objects plus a manifest (each member + sha256), so the
// client rebuilds the directory without any unzip dependency (iOS has
// no public zip API).
//
//   GET /api/beat-model/latest                     → pointer
//   GET /api/beat-model/{version}/manifest         → { files:[{path,sha256,size}] }
//   GET /api/beat-model/{version}/file/{member}    → bytes
//
// A newly downloaded version lands in
// Caches/toneforge/beat-model/<version>/BeatClassifier.mlmodelc/ and is
// picked up by `BeatModelStore.activeModelURL()` on the next capture.
// Fetch is public (no auth); each member's sha256 is verified before
// the directory is finalized atomically.

import Foundation
import CryptoKit

/// Pointer to the current published model.
public struct BeatModelPointer: Codable, Sendable, Equatable {
    public let version: String
    public let sha256: String
    public let files: Int
    public let url: String
}

/// Manifest listing every member of a compiled model directory.
public struct BeatModelManifest: Codable, Sendable, Equatable {
    public struct Member: Codable, Sendable, Equatable {
        public let path: String
        public let sha256: String
        public let size: Int
    }
    public let version: String
    public let files: [Member]
}

public enum BeatModelClientError: LocalizedError, Sendable {
    case httpStatus(Int)
    case checksumMismatch(member: String)
    case unsafeMemberPath(String)
    case noCacheDirectory

    public var errorDescription: String? {
        switch self {
        case .httpStatus(let code):
            return "Beat model fetch failed with HTTP \(code)."
        case .checksumMismatch(let member):
            return "Beat model member \(member) failed sha256 verification."
        case .unsafeMemberPath(let path):
            return "Beat model manifest contained an unsafe path: \(path)."
        case .noCacheDirectory:
            return "No caches directory available for the beat model."
        }
    }
}

/// Downloads newer drum-classifier models into the on-device cache.
public final class BeatModelClient: @unchecked Sendable {

    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    /// Check for a newer model and, if found, download + verify it into
    /// the cache. Returns the compiled-model URL when a new version was
    /// installed, or nil when already up to date. Safe to call in the
    /// background on launch; failures throw so callers can ignore them
    /// (the app keeps using the cached/bundled model).
    @discardableResult
    public func updateIfAvailable(baseURL: URL) async throws -> URL? {
        let pointer = try await fetchLatest(baseURL: baseURL)
        if BeatModelStore.isCached(version: pointer.version) {
            return nil
        }
        return try await download(baseURL: baseURL, version: pointer.version)
    }

    /// GET the latest pointer. Throws on non-2xx (including 404 when
    /// nothing is published yet).
    public func fetchLatest(baseURL: URL) async throws -> BeatModelPointer {
        let url = baseURL.appendingPathComponent("api/beat-model/latest")
        let data = try await get(url)
        return try JSONDecoder().decode(BeatModelPointer.self, from: data)
    }

    /// Download every member of `version`, verify per-file sha256, and
    /// atomically install the reconstructed `.mlmodelc` directory.
    /// Returns the installed compiled-model URL.
    public func download(baseURL: URL, version: String) async throws -> URL {
        guard let root = BeatModelStore.cacheRoot() else {
            throw BeatModelClientError.noCacheDirectory
        }
        let manifest = try await fetchManifest(baseURL: baseURL, version: version)

        let fm = FileManager.default
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        // Stage in a temp sibling so a crash mid-download never leaves a
        // half-written version dir that activeModelURL() would pick up.
        let staging = root.appendingPathComponent(
            ".staging-\(version)-\(UUID().uuidString)", isDirectory: true
        )
        let stagedModel = staging.appendingPathComponent(
            BeatModelStore.modelFilename, isDirectory: true
        )
        try fm.createDirectory(at: stagedModel, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: staging) }

        for member in manifest.files {
            guard Self.isSafeMemberPath(member.path) else {
                throw BeatModelClientError.unsafeMemberPath(member.path)
            }
            let fileURL = baseURL
                .appendingPathComponent("api/beat-model")
                .appendingPathComponent(version)
                .appendingPathComponent("file")
                .appendingPathComponent(member.path)
            let bytes = try await get(fileURL)
            guard Self.sha256Hex(bytes) == member.sha256 else {
                throw BeatModelClientError.checksumMismatch(member: member.path)
            }
            let dest = stagedModel.appendingPathComponent(member.path)
            try fm.createDirectory(
                at: dest.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try bytes.write(to: dest, options: .atomic)
        }

        let versionDir = root.appendingPathComponent(version, isDirectory: true)
        if fm.fileExists(atPath: versionDir.path) {
            try? fm.removeItem(at: versionDir)
        }
        try fm.moveItem(at: staging, to: versionDir)
        return versionDir.appendingPathComponent(
            BeatModelStore.modelFilename, isDirectory: true
        )
    }

    // MARK: - Private

    private func fetchManifest(
        baseURL: URL, version: String
    ) async throws -> BeatModelManifest {
        let url = baseURL
            .appendingPathComponent("api/beat-model")
            .appendingPathComponent(version)
            .appendingPathComponent("manifest")
        let data = try await get(url)
        return try JSONDecoder().decode(BeatModelManifest.self, from: data)
    }

    private func get(_ url: URL) async throws -> Data {
        let (data, response) = try await session.data(from: url)
        if let http = response as? HTTPURLResponse,
           !(200..<300).contains(http.statusCode) {
            throw BeatModelClientError.httpStatus(http.statusCode)
        }
        return data
    }

    static func sha256Hex(_ data: Data) -> String {
        SHA256.hash(data: data)
            .map { String(format: "%02x", $0) }
            .joined()
    }

    /// Defense-in-depth against a malicious manifest: relative paths
    /// only, no traversal, no absolute roots.
    static func isSafeMemberPath(_ path: String) -> Bool {
        if path.isEmpty || path.hasPrefix("/") { return false }
        let parts = path.split(separator: "/", omittingEmptySubsequences: false)
        for part in parts where part == ".." || part.isEmpty {
            return false
        }
        return true
    }
}
