// LibrarySearchClient.swift
//
// The ONE door the Songs page talks to:
//   GET  /api/library/search?source=&q=&genre=&key=&tempo_min=&
//        tempo_max=&mood=&tags=&status=&sort=&cursor=&limit=  -> SearchPage
//   POST /api/library/ingest  {source, source_ref}           -> IngestResult
//
// Sits behind a protocol so SongsModel tests run against a stub with no
// network. The caller's identity travels in the X-Device-Id / auth
// header stamped by AuthContext, but that header ALONE does not scope
// the results: /api/library/search only engages the scope=mine owner
// gate when the request carries scope=mine — WITHOUT it the endpoint
// returns the full multi-user library (masked today only by the
// SHARED_LIBRARY testing flag; a data leak the moment it is unset). So
// search() MUST send scope=mine; see the query items below.

import Foundation
import ToneForgeEngine

/// Server-side sort key. Sorting happens BEFORE paging so the opaque
/// cursor stays stable; the client only names the order.
public enum SongsSort: String, Codable, Sendable, CaseIterable, Equatable {
    case recent
    case title
    case tempo
    case key

    public var label: String {
        switch self {
        case .recent: return "Recent"
        case .title: return "Title"
        case .tempo: return "Tempo"
        case .key: return "Key"
        }
    }
}

/// The active facet selection. All optional / empty = no filter. `status`
/// is the free-form lifecycle filter; "processing" is the Band-Room
/// replacement chip (queued ∪ running).
public struct SongsFilters: Sendable, Equatable {
    public var genre: String?
    public var key: String?
    public var tempoMin: Double?
    public var tempoMax: Double?
    public var mood: String?
    public var tags: [String]
    public var status: String?

    public init(
        genre: String? = nil,
        key: String? = nil,
        tempoMin: Double? = nil,
        tempoMax: Double? = nil,
        mood: String? = nil,
        tags: [String] = [],
        status: String? = nil
    ) {
        self.genre = genre
        self.key = key
        self.tempoMin = tempoMin
        self.tempoMax = tempoMax
        self.mood = mood
        self.tags = tags
        self.status = status
    }

    /// True when any metadata facet (not the lifecycle `status`) is set.
    /// A live-only processing row has no genre/key/etc., so it must be
    /// hidden while such a filter is active — it would be filtered out
    /// server-side anyway.
    public var hasMetadataFilter: Bool {
        genre != nil || key != nil || mood != nil
            || !tags.isEmpty || tempoMin != nil || tempoMax != nil
    }

    public var isEmpty: Bool {
        !hasMetadataFilter && (status == nil || status?.isEmpty == true)
    }
}

public enum LibrarySearchError: Error, LocalizedError, Equatable {
    case invalidURL
    case badStatus(Int)

    public var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid library URL."
        case let .badStatus(code):
            return "Library request failed (HTTP \(code))."
        }
    }
}

/// Seam for the Songs page's two calls.
public protocol LibrarySearching: Sendable {
    func search(
        baseURL: URL,
        source: SourceId,
        query: String?,
        filters: SongsFilters,
        sort: SongsSort,
        cursor: String?,
        limit: Int
    ) async throws -> SearchPage

    func ingest(
        baseURL: URL,
        source: SourceId,
        sourceRef: String
    ) async throws -> IngestResult

    /// Remove a finished song from the library — purges the analysis
    /// (stems, R2 objects, graph) server-side. Backs the row's "Remove
    /// from Library" action.
    func delete(baseURL: URL, historyId: String) async throws

    /// Dismiss a failed analysis JOB (the Songs-page error-row "Dismiss").
    /// An error row is a failed engine job surfaced in the union, so
    /// removing the backing job is what actually clears the row.
    func dismissJob(baseURL: URL, jobId: String) async throws
}

/// Production adapter over the backend endpoints.
public struct BackendLibrarySearchClient: LibrarySearching {
    private let session: URLSession
    private let timeout: TimeInterval

    // 30s matches SessionLoader / desktop HistoryClientAdapter: the
    // backend can answer slowly while an analysis worker is uploading.
    public init(session: URLSession = .shared, timeout: TimeInterval = 30) {
        self.session = session
        self.timeout = timeout
    }

    public func search(
        baseURL: URL,
        source: SourceId,
        query: String?,
        filters: SongsFilters,
        sort: SongsSort,
        cursor: String?,
        limit: Int
    ) async throws -> SearchPage {
        var components = URLComponents(
            url: baseURL.appendingPathComponent("api/library/search"),
            resolvingAgainstBaseURL: false
        )
        var items: [URLQueryItem] = [
            URLQueryItem(name: "source", value: source.rawValue),
            URLQueryItem(name: "sort", value: sort.rawValue),
            URLQueryItem(name: "limit", value: String(limit)),
            // Engage the owner gate. /api/library/search returns the full
            // multi-user library UNLESS scope=mine is present — the auth
            // header identifies the caller but does not itself scope, so
            // omitting this leaks every user's songs the instant the
            // SHARED_LIBRARY testing flag is off.
            URLQueryItem(name: "scope", value: "mine"),
        ]
        func add(_ name: String, _ value: String?) {
            if let v = value?.trimmingCharacters(in: .whitespacesAndNewlines),
               !v.isEmpty {
                items.append(URLQueryItem(name: name, value: v))
            }
        }
        add("q", query)
        add("genre", filters.genre)
        add("key", filters.key)
        add("mood", filters.mood)
        add("status", filters.status)
        if let lo = filters.tempoMin {
            items.append(URLQueryItem(name: "tempo_min", value: String(lo)))
        }
        if let hi = filters.tempoMax {
            items.append(URLQueryItem(name: "tempo_max", value: String(hi)))
        }
        if !filters.tags.isEmpty {
            // Comma-joined so a single param carries the whole selection;
            // the backend splits on comma.
            items.append(URLQueryItem(name: "tags", value: filters.tags.joined(separator: ",")))
        }
        add("cursor", cursor)
        components?.queryItems = items

        guard let url = components?.url else {
            throw LibrarySearchError.invalidURL
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = timeout
        request.cachePolicy = .reloadIgnoringLocalCacheData
        AuthContext.shared.apply(to: &request)
        let (data, response) = try await session.data(for: request)
        if let http = response as? HTTPURLResponse,
           !(200..<300).contains(http.statusCode) {
            throw LibrarySearchError.badStatus(http.statusCode)
        }
        return try JSONDecoder().decode(SearchPage.self, from: data)
    }

    public func ingest(
        baseURL: URL,
        source: SourceId,
        sourceRef: String
    ) async throws -> IngestResult {
        let url = baseURL.appendingPathComponent("api/library/ingest")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        AuthContext.shared.apply(to: &request)
        let body: [String: String] = ["source": source.rawValue, "source_ref": sourceRef]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await session.data(for: request)
        if let http = response as? HTTPURLResponse,
           !(200..<300).contains(http.statusCode) {
            throw LibrarySearchError.badStatus(http.statusCode)
        }
        return try JSONDecoder().decode(IngestResult.self, from: data)
    }

    public func delete(baseURL: URL, historyId: String) async throws {
        try await deletePath(baseURL: baseURL, path: "api/history/\(historyId)")
    }

    public func dismissJob(baseURL: URL, jobId: String) async throws {
        try await deletePath(baseURL: baseURL, path: "api/jobs/\(jobId)")
    }

    /// Shared DELETE helper: percent-encode the id segment, apply the auth
    /// header, and surface a non-2xx as a `badStatus`.
    private func deletePath(baseURL: URL, path: String) async throws {
        let url = baseURL.appendingPathComponent(path)
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        request.timeoutInterval = timeout
        AuthContext.shared.apply(to: &request)
        let (_, response) = try await session.data(for: request)
        if let http = response as? HTTPURLResponse,
           !(200..<300).contains(http.statusCode) {
            throw LibrarySearchError.badStatus(http.statusCode)
        }
    }
}
