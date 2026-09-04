// KitClient.swift  (ToneForgeEngine)
//
// Thin wrapper around GET /api/song/{id}/kit — the Performance-Intelligence
// auto-built Launchpad kit. The backend returns a SamplePack manifest (the same
// shape browsed/curated packs use), sourced from the song's ranked, seamlessly-
// loopable performance assets. Both apps load it through the existing pack path,
// so a fetched kit is just another SamplePack.

import Foundation

public enum KitClientError: LocalizedError, Sendable {
    case invalidURL
    case httpStatus(Int)

    public var errorDescription: String? {
        switch self {
        case .invalidURL: return "Invalid kit URL."
        case .httpStatus(let code): return "Kit fetch failed with HTTP \(code)."
        }
    }
}

/// GET /api/song/{id}/kit?skill=&pads= — the auto-built performable kit.
public struct KitClient: Sendable {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    /// Fetch the auto-kit for `analysisId` as a ``SamplePack``.
    ///
    /// - Parameters:
    ///   - skill: `"beginner" | "intermediate" | "advanced"` — filters pad
    ///     difficulty / loop preference.
    ///   - pads: kit size (1…16).
    ///   - kind: `"auto"` (mixed performance kit) or `"drums"` (the song's
    ///     drum stem as classified one-shot hits + groove loops).
    public func fetchKit(
        baseURL: URL,
        analysisId: String,
        skill: String = "intermediate",
        pads: Int = 8,
        kind: String = "auto"
    ) async throws -> SamplePack {
        var components = URLComponents(
            url: baseURL
                .appendingPathComponent("api/song")
                .appendingPathComponent(analysisId)
                .appendingPathComponent("kit"),
            resolvingAgainstBaseURL: false
        )
        components?.queryItems = [
            URLQueryItem(name: "skill", value: skill),
            URLQueryItem(name: "pads", value: String(pads)),
            URLQueryItem(name: "kind", value: kind),
        ]
        guard let url = components?.url else { throw KitClientError.invalidURL }

        var request = URLRequest(url: url)
        // The kit re-ranks server-side (usage feedback, kit-builder
        // changes) — iOS heuristic caching must never serve a stale one.
        request.cachePolicy = .reloadIgnoringLocalCacheData
        AuthContext.shared.apply(to: &request)
        let (data, response) = try await session.data(for: request)
        if let http = response as? HTTPURLResponse,
           !(200..<300).contains(http.statusCode) {
            throw KitClientError.httpStatus(http.statusCode)
        }
        return try JSONDecoder().decode(SamplePack.self, from: data)
    }
}
