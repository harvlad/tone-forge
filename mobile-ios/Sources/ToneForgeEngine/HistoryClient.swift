// HistoryClient.swift
//
// Thin wrapper around GET /api/history. The backend returns
//
//   { "history": [ { id, timestamp, name, detected_type, summary,
//                    duration, amp_family? }, ... ] }
//
// We only decode the fields the Library UI needs; the analysis result
// blob (`result`) is opt-in and heavy, so it's loaded lazily via
// ``BundleLoader`` when the user actually activates a song.

import Foundation

/// One row in the history list. Matches the JSON shape produced by
/// `_add_to_history` in tone_forge_api.py.
public struct HistoryEntry: Sendable, Codable, Identifiable, Equatable {
    public let id: String
    public let timestamp: String
    public let name: String?
    public let detectedType: String?
    public let summary: String?
    public let duration: Double?
    public let ampFamily: String?

    enum CodingKeys: String, CodingKey {
        case id
        case timestamp
        case name
        case detectedType = "detected_type"
        case summary
        case duration
        case ampFamily = "amp_family"
    }

    public init(
        id: String,
        timestamp: String,
        name: String? = nil,
        detectedType: String? = nil,
        summary: String? = nil,
        duration: Double? = nil,
        ampFamily: String? = nil
    ) {
        self.id = id
        self.timestamp = timestamp
        self.name = name
        self.detectedType = detectedType
        self.summary = summary
        self.duration = duration
        self.ampFamily = ampFamily
    }
}

/// Errors surfaced by ``HistoryClient``.
public enum HistoryClientError: LocalizedError, Sendable {
    case invalidURL
    case httpStatus(Int)

    public var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid history URL."
        case .httpStatus(let code):
            return "History fetch failed with HTTP \(code)."
        }
    }
}

/// GET /api/history — recent analyses, optional search query.
public struct HistoryClient: Sendable {

    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    /// Fetch up to `limit` most-recent entries. `query` narrows by
    /// name/detected_type/summary/amp_family on the server side.
    public func fetch(
        baseURL: URL,
        query: String? = nil,
        limit: Int = 50
    ) async throws -> [HistoryEntry] {
        var components = URLComponents(
            url: baseURL.appendingPathComponent("api/history"),
            resolvingAgainstBaseURL: false
        )
        var items: [URLQueryItem] = [URLQueryItem(name: "limit", value: String(limit))]
        if let q = query, !q.isEmpty {
            items.append(URLQueryItem(name: "q", value: q))
        }
        components?.queryItems = items

        guard let url = components?.url else {
            throw HistoryClientError.invalidURL
        }

        let (data, response) = try await session.data(from: url)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw HistoryClientError.httpStatus(http.statusCode)
        }

        struct Wrapper: Decodable { let history: [HistoryEntry] }
        let decoded = try JSONDecoder().decode(Wrapper.self, from: data)
        return decoded.history
    }

    /// DELETE /api/history/{id} — removes the entry and deep-deletes
    /// its server-side artifacts (stems, R2 objects, layers).
    public func delete(baseURL: URL, entryId: String) async throws {
        try await send(
            url: baseURL.appendingPathComponent("api/history/\(entryId)")
        )
    }

    /// DELETE /api/history — wipes all history + artifacts.
    public func deleteAll(baseURL: URL) async throws {
        try await send(url: baseURL.appendingPathComponent("api/history"))
    }

    private func send(url: URL) async throws {
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        let (_, response) = try await session.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw HistoryClientError.httpStatus(http.statusCode)
        }
    }
}
