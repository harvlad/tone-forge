// LayerClient.swift
//
// Thin HTTP wrapper for the mobile-side of layer sync. Backs the
// three routes defined in tone_forge_api.py:
//
//   POST /api/song/{id}/layers          — upload a full LayerTimeline
//   GET  /api/song/{id}/layers          — metadata-only summaries
//   GET  /api/song/{id}/layers/{layerId} — full LayerTimeline JSON
//
// Kept in ToneForgeEngine (not ToneForgeMobile) so it's shareable with
// any future desktop client and stays test-friendly under
// URLProtocol-mocked sessions.
//
// Design notes:
//   - No auth: the backend is a single-user dev tool for v1.
//   - We JSON-encode the client-side `LayerTimeline` directly. The
//     backend validates the same required-key set.
//   - Idempotent upload: re-POSTing the same layerId overwrites the
//     stored file. Callers use this to sync renames without a
//     dedicated PATCH endpoint.

import Foundation

/// Errors surfaced by ``LayerClient``.
public enum LayerClientError: LocalizedError, Sendable {
    case invalidURL
    case httpStatus(Int, String?)

    public var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid layer URL."
        case .httpStatus(let code, let msg):
            if let msg = msg, !msg.isEmpty {
                return "Layer request failed (\(code)): \(msg)"
            }
            return "Layer request failed with HTTP \(code)."
        }
    }
}

/// Server response to `POST /api/song/{id}/layers`.
public struct LayerUploadResponse: Sendable, Codable, Equatable {
    public let layerId: String
    public let analysisId: String
    /// Relative URL under the backend for the freshly-stored layer,
    /// e.g. ``/api/song/song-abc/layers/{layerId}``.
    public let url: String

    public init(layerId: String, analysisId: String, url: String) {
        self.layerId = layerId
        self.analysisId = analysisId
        self.url = url
    }
}

/// One row of `GET /api/song/{id}/layers` — metadata only, no events.
public struct LayerSummary: Sendable, Codable, Identifiable, Equatable {
    public var id: String { layerId }
    public let layerId: String
    public let name: String?
    public let createdAtEpoch: Double?
    public let durationSec: Double?
    public let eventCount: Int?
    public let activePackId: String?

    public init(
        layerId: String,
        name: String? = nil,
        createdAtEpoch: Double? = nil,
        durationSec: Double? = nil,
        eventCount: Int? = nil,
        activePackId: String? = nil
    ) {
        self.layerId = layerId
        self.name = name
        self.createdAtEpoch = createdAtEpoch
        self.durationSec = durationSec
        self.eventCount = eventCount
        self.activePackId = activePackId
    }
}

public struct LayerClient: Sendable {

    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    // MARK: - Upload

    /// POST a LayerTimeline. Returns the server's ack (layerId, canonical URL).
    public func upload(
        baseURL: URL,
        timeline: LayerTimeline
    ) async throws -> LayerUploadResponse {
        guard let url = URL(
            string: "api/song/\(timeline.analysisId)/layers",
            relativeTo: baseURL
        )?.absoluteURL else {
            throw LayerClientError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(timeline)
        AuthContext.shared.apply(to: &request)

        let (data, response) = try await session.data(for: request)
        try Self.assertOk(response, data: data)
        return try JSONDecoder().decode(LayerUploadResponse.self, from: data)
    }

    // MARK: - List

    /// GET the metadata-only layer summaries for a song.
    public func list(
        baseURL: URL,
        analysisId: String
    ) async throws -> [LayerSummary] {
        guard let url = URL(
            string: "api/song/\(analysisId)/layers",
            relativeTo: baseURL
        )?.absoluteURL else {
            throw LayerClientError.invalidURL
        }
        var request = URLRequest(url: url)
        AuthContext.shared.apply(to: &request)
        let (data, response) = try await session.data(for: request)
        try Self.assertOk(response, data: data)
        struct Wrapper: Decodable { let layers: [LayerSummary] }
        return try JSONDecoder().decode(Wrapper.self, from: data).layers
    }

    // MARK: - Fetch

    /// GET the full LayerTimeline JSON for one layer.
    public func fetch(
        baseURL: URL,
        analysisId: String,
        layerId: String
    ) async throws -> LayerTimeline {
        guard let url = URL(
            string: "api/song/\(analysisId)/layers/\(layerId)",
            relativeTo: baseURL
        )?.absoluteURL else {
            throw LayerClientError.invalidURL
        }
        var request = URLRequest(url: url)
        AuthContext.shared.apply(to: &request)
        let (data, response) = try await session.data(for: request)
        try Self.assertOk(response, data: data)
        return try JSONDecoder().decode(LayerTimeline.self, from: data)
    }

    // MARK: - Helpers

    /// Throws on non-2xx. Tries to surface the FastAPI ``detail`` field
    /// so the mobile UI can display a real reason, not just a status.
    private static func assertOk(_ response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        if (200..<300).contains(http.statusCode) { return }

        struct DetailWrapper: Decodable { let detail: String? }
        let detail = (try? JSONDecoder().decode(DetailWrapper.self, from: data))?.detail
        throw LayerClientError.httpStatus(http.statusCode, detail)
    }
}
