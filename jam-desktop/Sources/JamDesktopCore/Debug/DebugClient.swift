// DebugClient.swift
//
// HTTP client for the Debug window's four endpoints. Protocol seam so
// the tab models can be tested with canned payloads (same pattern as
// HistoryModel's HistoryFetching). /api/debug/* is admin-guarded on
// hosted backends (404 without a token), so every request carries the
// admin token when one is set; local loopback works tokenless.

import Foundation

public protocol DebugFetching: Sendable {
    func fetchSessions(baseURL: URL) async throws -> [DebugSessionSummary]
    func fetchBundle(baseURL: URL, id: String) async throws -> DebugBundle
    func fetchCorpus(baseURL: URL) async throws -> DebugCorpus
    func fetchHistory(baseURL: URL, limit: Int) async throws -> [DebugHistoryRow]
}

public struct DebugClient: DebugFetching {
    private let session: URLSession
    // 30s like HistoryClientAdapter — the backend can be slow while an
    // analysis worker is uploading results.
    private let timeout: TimeInterval

    public init(session: URLSession = .shared, timeout: TimeInterval = 30) {
        self.session = session
        self.timeout = timeout
    }

    public func fetchSessions(baseURL: URL) async throws -> [DebugSessionSummary] {
        let url = baseURL.appending(path: "api/debug/sessions")
        let response: DebugSessionsResponse = try await get(url)
        return response.sessions
    }

    public func fetchBundle(baseURL: URL, id: String) async throws -> DebugBundle {
        let url = baseURL.appending(path: "api/session/\(id)")
        return try await get(url)
    }

    public func fetchCorpus(baseURL: URL) async throws -> DebugCorpus {
        let url = baseURL.appending(path: "api/debug/corpus")
        return try await get(url)
    }

    public func fetchHistory(baseURL: URL, limit: Int) async throws -> [DebugHistoryRow] {
        var components = URLComponents(
            url: baseURL.appending(path: "api/history"),
            resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "limit", value: String(limit))]
        let response: DebugHistoryResponse = try await get(components.url!)
        return response.history
    }

    private func get<T: Decodable>(_ url: URL) async throws -> T {
        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.httpMethod = "GET"
        AdminCredentials.apply(to: &request)
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            let code = (response as? HTTPURLResponse)?.statusCode ?? -1
            throw URLError(
                .badServerResponse,
                userInfo: [NSLocalizedDescriptionKey: "HTTP \(code) from \(url.path)"])
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}
