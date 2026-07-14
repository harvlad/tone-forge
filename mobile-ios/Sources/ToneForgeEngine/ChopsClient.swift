// ChopsClient.swift
//
// Thin wrapper around GET /api/song/{id}/chops. The backend returns
//
//   { "chops": [ Chop, Chop, ... ] }
//
// This is the on-demand path for song-derived pads: the bundle already
// carries the "canonical" presets (harmonic/sections) inline under
// `SongBundle.presets`, and we can synthesise virtual packs from those
// without a network call. But when the user wants a different slice
// mode ("beat", "phrase", "onset", "drum-bundle", ...) they open Browse
// Packs → Song DNA → change slice mode, and this client fetches the
// chops for that (stem, sliceMode) tuple.
//
// The returned `[Chop]` is fed into `SampleBank.songDerived(preset:...)`
// by wrapping it in an ad-hoc `BundlePreset` so the mobile side has
// one code path for "chops → virtual pack".

import Foundation

/// Errors surfaced by ``ChopsClient``.
public enum ChopsClientError: LocalizedError, Sendable {
    case invalidURL
    case httpStatus(Int)

    public var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid chops URL."
        case .httpStatus(let code):
            return "Chops fetch failed with HTTP \(code)."
        }
    }
}

/// GET /api/song/{id}/chops?stem=&sliceMode= — on-demand chop stream
/// for a specific (stem, sliceMode) tuple.
public struct ChopsClient: Sendable {

    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    /// Fetch chops for `(stem, sliceMode)` under song `analysisId`.
    ///
    /// - Parameters:
    ///   - baseURL: backend base URL, e.g. `https://…/`.
    ///   - analysisId: `SongBundle.analysisId`.
    ///   - stem: stem role. Must match a `BundleStem.role` from the
    ///     song's bundle (e.g. `"vocals"`, `"drums"`, `"other"`). nil
    ///     lets the backend pick a default.
    ///   - sliceMode: chop policy — one of `"chord"`, `"section"`,
    ///     `"beat"`, `"phrase"`, `"onset"`, `"drum-bundle"`. nil lets
    ///     the backend pick a default.
    public func fetchChops(
        baseURL: URL,
        analysisId: String,
        stem: String? = nil,
        sliceMode: String? = nil
    ) async throws -> [Chop] {
        var components = URLComponents(
            url: baseURL
                .appendingPathComponent("api/song")
                .appendingPathComponent(analysisId)
                .appendingPathComponent("chops"),
            resolvingAgainstBaseURL: false
        )
        var items: [URLQueryItem] = []
        if let s = stem, !s.isEmpty {
            items.append(URLQueryItem(name: "stem", value: s))
        }
        if let m = sliceMode, !m.isEmpty {
            items.append(URLQueryItem(name: "sliceMode", value: m))
        }
        if !items.isEmpty { components?.queryItems = items }

        guard let url = components?.url else {
            throw ChopsClientError.invalidURL
        }

        var request = URLRequest(url: url)
        AuthContext.shared.apply(to: &request)
        let (data, response) = try await session.data(for: request)
        if let http = response as? HTTPURLResponse,
           !(200..<300).contains(http.statusCode)
        {
            throw ChopsClientError.httpStatus(http.statusCode)
        }

        struct Wrapper: Decodable { let chops: [Chop] }
        let decoded = try JSONDecoder().decode(Wrapper.self, from: data)
        return decoded.chops
    }
}
