// CrateClient.swift  (ToneForgeEngine)
//
// HTTP client for the "Vinyl Crate" — the shared, curated CC-BY/CC0 donor pool.
// Three orthogonal entry points into one CrateTrack set, all reusing the borrow
// engine server-side:
//
//   GET /api/crate/candidates    session-matched ranking (crate.match)
//   GET /api/crate/search        faceted metadata browse (crate.search)
//   GET /api/crate/{id}/borrow   matched loops via the borrow render path
//
// Twin of jam-desktop's SessionController crate HTTP (crateCandidates /
// searchCrate / loadCrateLoops) and web kit.js's crate loaders — the crate
// endpoints post-date the shared RemixClient, so this is a dedicated seam that
// deliberately reuses AuthContext, the same long-haul session RemixClient uses
// for a first-render borrow, and the SamplePack DTO (the crate borrow response
// is a strict SamplePack superset — extra top-level crateId/attribution fields
// are ignored by the keyed decoder) so ranking, download and mount stay one
// path.
//
// The id path segment can contain ':' ("crate:jamendo:123456"), a legal path
// char — appendingPathComponent leaves it literal and FastAPI's {crate_id}
// captures the whole segment, matching desktop's crateURL.

import Foundation

public struct CrateClient: Sendable {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    private func request(_ url: URL) -> URLRequest {
        var r = URLRequest(url: url)
        r.cachePolicy = .reloadIgnoringLocalCacheData
        AuthContext.shared.apply(to: &r)
        return r
    }

    /// `<base>/api/crate/<leaf>` (optionally `<base>/api/crate/<id>/<leaf>`)
    /// with the given query.
    private func crateURL(
        _ base: URL, _ leaf: String, id: String? = nil, query: [URLQueryItem]
    ) throws -> URL {
        var path = base.appendingPathComponent("api/crate")
        if let id { path = path.appendingPathComponent(id) }
        path = path.appendingPathComponent(leaf)
        var comps = URLComponents(url: path, resolvingAgainstBaseURL: false)
        comps?.queryItems = query.isEmpty ? nil : query
        guard let url = comps?.url else { throw RemixClientError.invalidURL }
        return url
    }

    /// Session-matched crate suggestions for a stem. `sessionId` gives the
    /// backend the host tempo/key/melody/stems context; `genreMode` toggles
    /// similar-vs-contrast affinity; `facets` pre-filter the pool BEFORE the
    /// weighted ranking (so "rank-match but only CC0 / only with a drum stem"
    /// stacks rather than competes). `sessionId` nil = blank canvas (ranked on
    /// the session-independent signals).
    public func fetchCandidates(
        baseURL: URL, sessionId: String?, stem: String,
        genreMode: CrateGenreMode = .similar,
        facets: CrateFacetQuery = .init(), limit: Int = 24
    ) async throws -> CrateCandidatesResponse {
        var q: [URLQueryItem] = [
            .init(name: "stem", value: stem),
            .init(name: "genre_mode", value: genreMode.rawValue),
        ]
        if let sessionId, !sessionId.isEmpty {
            q.append(.init(name: "session_id", value: sessionId))
        }
        q.append(contentsOf: facets.facetQueryItems())   // pre-rank filters
        q.append(.init(name: "limit", value: String(limit)))
        let url = try crateURL(baseURL, "candidates", query: q)
        let (data, response) = try await session.data(for: request(url))
        try Self.check(response)
        return try JSONDecoder().decode(CrateCandidatesResponse.self, from: data)
    }

    /// Faceted crate browse — NO session needed (catalog view). Returns the
    /// rows PLUS the per-facet counts so the UI can render a facet sidebar.
    public func search(
        baseURL: URL, facets: CrateFacetQuery, limit: Int = 40, offset: Int = 0
    ) async throws -> CrateSearchResponse {
        let q = facets.searchQueryItems(limit: limit, offset: offset)
        let url = try crateURL(baseURL, "search", query: q)
        let (data, response) = try await session.data(for: request(url))
        try Self.check(response)
        return try JSONDecoder().decode(CrateSearchResponse.self, from: data)
    }

    /// `GET /api/crate/{id}/borrow` — render + fetch a crate track's curated
    /// auto-kit as borrow pads, conformed to the session (donor tempo-matched +
    /// key-conformed; the host's own pads stay TRUE). First call renders
    /// server-side (WSOLA + transpose) — allow seconds, hence the long-haul
    /// session. `sessionId` nil = blank canvas (donor-only, its own tempo).
    /// The returned SamplePack's pads carry `/api/crate/sample/...` sampleUrls
    /// + per-pad attribution, mounted through the identical borrow layout path.
    public func fetchBorrowPack(
        baseURL: URL, trackId: String, sessionId: String?, stem: String,
        targetBpm: Double? = nil, targetKey: String? = nil
    ) async throws -> SamplePack {
        var q: [URLQueryItem] = [.init(name: "stem", value: stem)]
        if let sessionId, !sessionId.isEmpty {
            q.append(.init(name: "session_id", value: sessionId))
        }
        if let targetBpm {
            q.append(.init(name: "target_bpm", value: String(targetBpm)))
        }
        if let targetKey, !targetKey.isEmpty {
            q.append(.init(name: "target_key", value: targetKey))
        }
        let url = try crateURL(baseURL, "borrow", id: trackId, query: q)
        let (data, response) = try await Self.longHaul.data(for: request(url))
        try Self.check(response)
        return try JSONDecoder().decode(SamplePack.self, from: data)
    }

    /// Long-haul session for the first crate-borrow render — same reason as
    /// RemixClient's: the initial server-side WSOLA render can take tens of
    /// seconds and would trip URLSession's 60 s default request timeout.
    private static let longHaul: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 300
        config.timeoutIntervalForResource = 600
        return URLSession(configuration: config)
    }()

    private static func check(_ response: URLResponse) throws {
        if let http = response as? HTTPURLResponse,
           !(200..<300).contains(http.statusCode) {
            throw RemixClientError.httpStatus(http.statusCode)
        }
    }
}
