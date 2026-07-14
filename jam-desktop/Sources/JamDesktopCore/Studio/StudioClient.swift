// StudioClient.swift
//
// HTTP client for the Studio results inspector. Phase 1 needs one
// endpoint: GET /api/history/{id} (public; R2 stem URLs are refreshed
// server-side on read). Protocol seam per HistoryModel pattern.

import Foundation

public protocol StudioFetching: Sendable {
    func fetchHistoryDetail(baseURL: URL, id: String) async throws -> StudioHistoryDetail
}

public struct StudioClient: StudioFetching {
    private let session: URLSession
    private let timeout: TimeInterval

    public init(session: URLSession = .shared, timeout: TimeInterval = 30) {
        self.session = session
        self.timeout = timeout
    }

    public func fetchHistoryDetail(
        baseURL: URL, id: String
    ) async throws -> StudioHistoryDetail {
        let url = baseURL.appending(path: "api/history/\(id)")
        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.httpMethod = "GET"
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            let code = (response as? HTTPURLResponse)?.statusCode ?? -1
            throw URLError(
                .badServerResponse,
                userInfo: [NSLocalizedDescriptionKey: "HTTP \(code) from \(url.path)"])
        }
        return try JSONDecoder().decode(StudioHistoryDetail.self, from: data)
    }
}
