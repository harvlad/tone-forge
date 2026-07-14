// CCTrackClient.swift
//
// Client for the curated CC0/CC-BY demo-track catalog (D-024):
//
//   GET  {base}/api/cc-tracks               -> {"tracks": [CCTrack]}
//   POST {base}/api/cc-tracks/{id}/import   -> {"job_id": "...", "engine_online": bool}
//
// Import queues a pre-attested engine job server-side — the server
// owns the license metadata and the audio, so the client never
// uploads anything; the returned job id feeds the same job-events
// flow as a normal import (JobSubmitting.events).
//
// The transport is a protocol (`CCTrackProviding`, mirrors
// JobSubmitting) so views and tests can stub the network.

import Foundation

/// One catalog entry. The server omits its private `file` field; every
/// other catalog key round-trips so the picker can show a license
/// badge and full credit before the user imports anything.
public struct CCTrack: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let title: String
    public let artist: String
    public let license: String
    public let licenseUrl: String
    public let sourceUrl: String
    public let attribution: String
    public let durationSec: Double?
    public let description: String

    public init(
        id: String,
        title: String,
        artist: String = "",
        license: String = "",
        licenseUrl: String = "",
        sourceUrl: String = "",
        attribution: String = "",
        durationSec: Double? = nil,
        description: String = ""
    ) {
        self.id = id
        self.title = title
        self.artist = artist
        self.license = license
        self.licenseUrl = licenseUrl
        self.sourceUrl = sourceUrl
        self.attribution = attribution
        self.durationSec = durationSec
        self.description = description
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        title = try container.decodeIfPresent(String.self, forKey: .title) ?? id
        artist = try container.decodeIfPresent(String.self, forKey: .artist) ?? ""
        license = try container.decodeIfPresent(String.self, forKey: .license) ?? ""
        licenseUrl = try container.decodeIfPresent(String.self, forKey: .licenseUrl) ?? ""
        sourceUrl = try container.decodeIfPresent(String.self, forKey: .sourceUrl) ?? ""
        attribution = try container.decodeIfPresent(String.self, forKey: .attribution) ?? ""
        durationSec = try container.decodeIfPresent(Double.self, forKey: .durationSec)
        description = try container.decodeIfPresent(String.self, forKey: .description) ?? ""
    }
}

public enum CCTrackClientError: Error, LocalizedError, Equatable {
    case badStatus(Int)
    case missingJobId

    public var errorDescription: String? {
        switch self {
        case .badStatus(let code):
            return "The server rejected the request (HTTP \(code))."
        case .missingJobId:
            return "The server accepted the import but returned no job id."
        }
    }
}

/// Transport seam over the cc-tracks API for test stubbing.
public protocol CCTrackProviding: Sendable {
    /// Fetch the curated catalog. Empty array when nothing is published.
    func fetchCatalog(baseURL: URL) async throws -> [CCTrack]
    /// Start a server-side import of a curated track; returns the job
    /// id to follow via `JobSubmitting.events`.
    func startImport(baseURL: URL, trackId: String) async throws -> String
}

/// Production transport hitting the real backend.
public struct BackendCCTrackClient: CCTrackProviding {
    public init() {}

    private struct CatalogResponse: Decodable {
        let tracks: [CCTrack]
    }

    public func fetchCatalog(baseURL: URL) async throws -> [CCTrack] {
        var request = URLRequest(url: baseURL.appendingPathComponent("api/cc-tracks"))
        request.timeoutInterval = 30
        AuthContext.shared.apply(to: &request)
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            throw CCTrackClientError.badStatus(http.statusCode)
        }
        return try JSONDecoder().decode(CatalogResponse.self, from: data).tracks
    }

    public func startImport(baseURL: URL, trackId: String) async throws -> String {
        let url = baseURL.appendingPathComponent("api/cc-tracks/\(trackId)/import")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        // Stamps X-Device-Id (+ bearer when signed in) so the analysis
        // lands in this device's history like a local import would.
        AuthContext.shared.apply(to: &request)
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            throw CCTrackClientError.badStatus(http.statusCode)
        }
        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let dict = object as? [String: Any],
            let jobId = dict["job_id"] as? String, !jobId.isEmpty
        else {
            throw CCTrackClientError.missingJobId
        }
        return jobId
    }
}
