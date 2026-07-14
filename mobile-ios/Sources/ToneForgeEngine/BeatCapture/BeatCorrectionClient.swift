// BeatCorrectionClient.swift
//
// Beat Capture (D-024): uploads user drum-role corrections to the
// backend so the server-side corpus (and the trained model) improve.
// Devices never accumulate the large training corpus — they only push
// their handful of corrections and then clear them locally.
//
//   POST /api/beat-corrections
//     body { "corrections": [ { "features": {<named>}, "original",
//            "corrected", "ts" } ] }
//
// Auth via AuthContext (Bearer + X-Device-Id), matching every other
// engine client. Analysis features only — no audio ever leaves the
// device.

import Foundation

/// One correction row in the upload payload. Features are sent as a
/// name→value map (keyed by `OnsetFeatures.featureNames`) so the server
/// validates the schema independent of column order.
public struct BeatCorrectionUpload: Codable, Sendable, Equatable {
    public let features: [String: Double]
    public let original: String
    public let corrected: String
    public let ts: String

    public init(features: [String: Double], original: String, corrected: String, ts: String) {
        self.features = features
        self.original = original
        self.corrected = corrected
        self.ts = ts
    }
}

public enum BeatCorrectionClientError: LocalizedError, Sendable {
    case httpStatus(Int)

    public var errorDescription: String? {
        switch self {
        case .httpStatus(let code):
            return "Beat correction upload failed with HTTP \(code)."
        }
    }
}

/// Posts batches of drum-role corrections to the backend.
public final class BeatCorrectionClient: @unchecked Sendable {

    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    /// POST a batch of corrections. Returns normally on 2xx; throws on
    /// transport error or non-2xx so the caller keeps the rows queued
    /// for a later retry.
    public func upload(baseURL: URL, corrections: [BeatCorrectionUpload]) async throws {
        guard !corrections.isEmpty else { return }
        let url = baseURL.appendingPathComponent("api/beat-corrections")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        AuthContext.shared.apply(to: &request)

        struct Body: Encodable { let corrections: [BeatCorrectionUpload] }
        request.httpBody = try JSONEncoder().encode(Body(corrections: corrections))

        let (_, response) = try await session.data(for: request)
        if let http = response as? HTTPURLResponse,
           !(200..<300).contains(http.statusCode) {
            throw BeatCorrectionClientError.httpStatus(http.statusCode)
        }
    }
}
