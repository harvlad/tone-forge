// QualityClient.swift
//
// POST /api/admin/analyze-quality — deep stem-quality analysis of a
// local audio file. Admin-guarded: the backend answers 404 when the
// admin token is missing/wrong (existence hidden), so that status is
// mapped to a "set the admin token" error here. Trim fields
// (start_time/end_time) arrive with Studio Phase 3.

import Foundation
import ToneForgeEngine

public enum StudioAdminError: Error, LocalizedError, Equatable {
    /// 404 from an /api/admin/* path — with the backend guard this
    /// almost always means no/wrong admin token, not a missing route.
    case adminTokenRequired
    case badStatus(Int, String)

    public var errorDescription: String? {
        switch self {
        case .adminTokenRequired:
            return "Admin access denied — set the Studio admin token in "
                + "Settings (or point at a local backend)."
        case let .badStatus(code, body):
            return "Quality analysis failed (HTTP \(code)): \(body)"
        }
    }
}

public protocol QualityAnalyzing: Sendable {
    func analyzeQuality(
        baseURL: URL, fileURL: URL, filename: String
    ) async throws -> QualityAnalysis
}

public struct QualityClient: QualityAnalyzing {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func analyzeQuality(
        baseURL: URL, fileURL: URL, filename: String
    ) async throws -> QualityAnalysis {
        let boundary = "tfstudio-\(UUID().uuidString)"
        let bodyFile = try AnalyzeClient.writeMultipartBodyFile(
            fileURL: fileURL,
            filename: filename,
            contentType: "application/octet-stream",
            fields: [],
            boundary: boundary
        )
        defer { try? FileManager.default.removeItem(at: bodyFile) }

        var request = URLRequest(
            url: baseURL.appendingPathComponent("api/admin/analyze-quality"))
        request.httpMethod = "POST"
        request.setValue(
            "multipart/form-data; boundary=\(boundary)",
            forHTTPHeaderField: "Content-Type"
        )
        // Reconstruction analysis is CPU-heavy on long files.
        request.timeoutInterval = 10 * 60
        AdminCredentials.apply(to: &request)

        let (data, response) = try await session.upload(
            for: request, fromFile: bodyFile)
        let code = (response as? HTTPURLResponse)?.statusCode ?? -1
        guard code == 200 else {
            if code == 404 { throw StudioAdminError.adminTokenRequired }
            throw StudioAdminError.badStatus(
                code, String(data: data, encoding: .utf8) ?? "")
        }
        return try JSONDecoder().decode(QualityAnalysis.self, from: data)
    }
}
