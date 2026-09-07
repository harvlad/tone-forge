// RemixClients.swift  (ToneForgeEngine)
//
// Thin wrappers around the Remix-sheet backend endpoints:
//   GET /api/song/{id}/groove            — micro-timing template (Humanize)
//   GET /api/song/{id}/redrum-candidates — ranked kit-donor suggestions
//   GET /api/song/{id}/redrum?kit=…      — replacement drums stem WAV
//
// Same conventions as KitClient: AuthContext applied, no heuristic
// caching for the JSON endpoints (server-side state moves), and the
// redrum WAV lands in Caches keyed by its server cache identity.

import Foundation

public enum RemixClientError: LocalizedError, Sendable {
    case invalidURL
    case httpStatus(Int)

    public var errorDescription: String? {
        switch self {
        case .invalidURL: return "Invalid remix URL."
        case .httpStatus(let code): return "Remix request failed with HTTP \(code)."
        }
    }
}

/// One suggested Re-Drum kit donor (server-ranked).
public struct RedrumCandidate: Codable, Sendable, Identifiable, Equatable {
    public let entryId: String
    public let name: String
    public let score: Double
    public let classes: [String]
    public let hits: Int
    public var id: String { entryId }
}

public struct RemixClient: Sendable {
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

    private func songURL(_ base: URL, _ analysisId: String, _ leaf: String,
                         query: [URLQueryItem] = []) throws -> URL {
        var components = URLComponents(
            url: base.appendingPathComponent("api/song")
                .appendingPathComponent(analysisId)
                .appendingPathComponent(leaf),
            resolvingAgainstBaseURL: false)
        if !query.isEmpty { components?.queryItems = query }
        guard let url = components?.url else { throw RemixClientError.invalidURL }
        return url
    }

    /// The song's groove template: 16 per-slot delays in step fractions,
    /// ready for `SequencerPlayer.grooveOffsets`.
    public func fetchGroove(baseURL: URL, analysisId: String) async throws -> [Double] {
        struct Wire: Codable {
            struct Groove: Codable { let offsetsSteps: [Double] }
            let groove: Groove
        }
        let (data, response) = try await session.data(
            for: request(try songURL(baseURL, analysisId, "groove")))
        try Self.check(response)
        return try JSONDecoder().decode(Wire.self, from: data).groove.offsetsSteps
    }

    /// Ranked kit-donor suggestions for Re-Drum.
    public func fetchRedrumCandidates(
        baseURL: URL, analysisId: String
    ) async throws -> [RedrumCandidate] {
        struct Wire: Codable { let candidates: [RedrumCandidate] }
        let (data, response) = try await session.data(
            for: request(try songURL(baseURL, analysisId, "redrum-candidates")))
        try Self.check(response)
        return try JSONDecoder().decode(Wire.self, from: data).candidates
    }

    /// Long-haul session for the redrum WAV: first render per (song, kit)
    /// pair runs server-side on this request (~10-60 s) and the payload is
    /// a full-length stem (tens of MB) — URLSession's 60 s default request
    /// timeout killed it on slower links and the row read as "did nothing".
    private static let longHaul: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 300
        config.timeoutIntervalForResource = 600
        return URLSession(configuration: config)
    }()

    /// Download the Re-Drum replacement drums stem into Caches and return
    /// its local URL. `kit` is "self" or "song:<entryId>". First render per
    /// (song, kit) pair happens server-side on this request — allow seconds.
    public func fetchRedrumStem(
        baseURL: URL, analysisId: String, kit: String
    ) async throws -> URL {
        let fm = FileManager.default
        guard let caches = fm.urls(for: .cachesDirectory,
                                   in: .userDomainMask).first else {
            throw RemixClientError.invalidURL
        }
        let dir = caches.appendingPathComponent("toneforge/redrum", isDirectory: true)
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
        let safeKit = kit.replacingOccurrences(of: ":", with: "_")
        let dest = dir.appendingPathComponent("\(analysisId)_\(safeKit).wav")
        if fm.fileExists(atPath: dest.path) { return dest }

        let url = try songURL(baseURL, analysisId, "redrum",
                              query: [URLQueryItem(name: "kit", value: kit)])
        let (data, response) = try await Self.longHaul.data(for: request(url))
        try Self.check(response)
        guard !data.isEmpty else { throw RemixClientError.httpStatus(204) }
        try data.write(to: dest, options: .atomic)
        return dest
    }

    private static func check(_ response: URLResponse) throws {
        if let http = response as? HTTPURLResponse,
           !(200..<300).contains(http.statusCode) {
            throw RemixClientError.httpStatus(http.statusCode)
        }
    }
}
