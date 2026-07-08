// BundleLoader.swift
//
// Thin async loader for SongBundle. Two entry points:
//   fetch(from:analysisId:)  — GET the bundle from a running backend
//   load(from:)              — decode a bundle already-on-disk
//
// Persistence policy (v1): the app caches bundles under
//   ~/Library/Application Support/ToneForge/bundles/<analysisId>.json
// so airport-mode works even without network. Cache eviction is a
// separate concern (see BundleCache — not implemented in v1).

import Foundation

public enum BundleLoaderError: Error, LocalizedError {
    case badResponse(Int)
    case decodeFailure(Error)
    case invalidUrl

    public var errorDescription: String? {
        switch self {
        case .badResponse(let code):
            return "Bundle request failed (HTTP \(code))"
        case .decodeFailure(let err):
            return "Bundle JSON decode failed: \(err.localizedDescription)"
        case .invalidUrl:
            return "Invalid backend URL"
        }
    }
}

public struct BundleLoader {
    /// Per-request timeout. Default URLRequest timeout is 60s, which
    /// hangs song activation for a minute when the backend host is
    /// unreachable; keep it short so the caller's cache fallback
    /// (AppState.loadBundle) kicks in quickly.
    private let timeout: TimeInterval

    public init(timeout: TimeInterval = 5) {
        self.timeout = timeout
    }

    /// Fetch a bundle from a running tone-forge backend.
    ///
    /// - Parameters:
    ///   - backend: base URL of the tone-forge backend, e.g.
    ///     `URL(string: "http://192.168.1.10:8000")!`. The app UI
    ///     lets the user configure this (see BackendConfig).
    ///   - analysisId: history entry id (short hex prefix used
    ///     across the tone-forge codebase).
    /// - Returns: decoded `SongBundle`.
    public func fetch(from backend: URL, analysisId: String) async throws -> SongBundle {
        let path = "/api/song/\(analysisId)/bundle"
        guard let url = URL(string: path, relativeTo: backend) else {
            throw BundleLoaderError.invalidUrl
        }
        var req = URLRequest(url: url)
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.timeoutInterval = timeout
        let (data, response) = try await URLSession.shared.data(for: req)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw BundleLoaderError.badResponse(http.statusCode)
        }
        return try decode(data)
    }

    /// Decode a bundle previously written to disk.
    public func load(from fileURL: URL) throws -> SongBundle {
        let data = try Data(contentsOf: fileURL)
        return try decode(data)
    }

    /// Serialize a bundle to disk so the app can play it offline.
    public func save(_ bundle: SongBundle, to fileURL: URL) throws {
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try enc.encode(bundle)
        try FileManager.default.createDirectory(
            at: fileURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try data.write(to: fileURL, options: .atomic)
    }

    // MARK: - Private

    private func decode(_ data: Data) throws -> SongBundle {
        let dec = JSONDecoder()
        // The backend sends camelCase already; no key strategy needed.
        do {
            return try dec.decode(SongBundle.self, from: data)
        } catch {
            throw BundleLoaderError.decodeFailure(error)
        }
    }
}
