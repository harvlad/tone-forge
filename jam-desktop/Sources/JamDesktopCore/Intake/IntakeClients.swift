// IntakeClients.swift
//
// Backend clients for the Intake flows the ToneForgeEngine package
// doesn't already cover:
//   - GET  /api/engine/status        (engine banner)
//   - POST /api/analyze-upload       (web jam upload path → GPU job)
//   - POST /api/analyze-url-stream   (URL paste → deep analysis SSE)
//
// Each sits behind a protocol so IntakeModel tests run with stubs.
// The SSE payload shape is shared with /api/analyze-stream:
//   data: {"type":"progress","message":"…","percent":42}
//   data: {"type":"result","data":{…,"history_id":"…"}}
//   data: {"type":"error","message":"…"}
// and maps onto ToneForgeEngine.AnalyzeEvent.

import Foundation
import ToneForgeEngine

// MARK: - Engine status

public struct EngineStatus: Codable, Sendable, Equatable {
    public let online: Bool
    public let device: String?

    public init(online: Bool, device: String?) {
        self.online = online
        self.device = device
    }
}

public protocol EngineStatusFetching: Sendable {
    func status(baseURL: URL) async throws -> EngineStatus
}

public struct EngineStatusClient: EngineStatusFetching {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func status(baseURL: URL) async throws -> EngineStatus {
        let url = baseURL.appendingPathComponent("api/engine/status")
        var request = URLRequest(url: url)
        request.timeoutInterval = 10
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(EngineStatus.self, from: data)
    }
}

// MARK: - File upload (analyze-upload → job)

/// Response of POST /api/analyze-upload and
/// POST /api/cc-tracks/{id}/import.
public struct UploadStart: Codable, Sendable, Equatable {
    public let jobId: String
    public let engineOnline: Bool

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id"
        case engineOnline = "engine_online"
    }

    public init(jobId: String, engineOnline: Bool) {
        self.jobId = jobId
        self.engineOnline = engineOnline
    }
}

public protocol UploadSubmitting: Sendable {
    func submitUpload(
        baseURL: URL, fileURL: URL, filename: String
    ) async throws -> UploadStart
}

public struct UploadClient: UploadSubmitting {
    public enum UploadError: Error, LocalizedError {
        case badStatus(Int, String)

        public var errorDescription: String? {
            switch self {
            case let .badStatus(code, body):
                return "Upload failed (HTTP \(code)): \(body)"
            }
        }
    }

    /// Fields matching jam.js's upload FormData. The desktop UI gates
    /// on the ownership-attestation checkbox before calling, so
    /// `attested` is always true on the wire (the server 400s
    /// otherwise).
    static let formFields: [(name: String, value: String)] = [
        ("attested", "true"),
        ("extract_midi", "true"),
    ]

    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func submitUpload(
        baseURL: URL, fileURL: URL, filename: String
    ) async throws -> UploadStart {
        let boundary = "tfjam-\(UUID().uuidString)"
        // Streamed body file (audio uploads can be large).
        let bodyFile = try AnalyzeClient.writeMultipartBodyFile(
            fileURL: fileURL,
            filename: filename,
            contentType: "application/octet-stream",
            fields: Self.formFields,
            boundary: boundary
        )
        defer { try? FileManager.default.removeItem(at: bodyFile) }

        var request = URLRequest(url: baseURL.appendingPathComponent("api/analyze-upload"))
        request.httpMethod = "POST"
        request.setValue(
            "multipart/form-data; boundary=\(boundary)",
            forHTTPHeaderField: "Content-Type"
        )
        request.timeoutInterval = 5 * 60
        // Device id (+ bearer when signed in) so the job is scoped to
        // this caller and shows up in GET /api/jobs.
        AuthContext.shared.apply(to: &request)

        let (data, response) = try await session.upload(for: request, fromFile: bodyFile)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            let code = (response as? HTTPURLResponse)?.statusCode ?? -1
            throw UploadError.badStatus(code, String(data: data, encoding: .utf8) ?? "")
        }
        return try JSONDecoder().decode(UploadStart.self, from: data)
    }
}

// MARK: - URL analysis (analyze-url-stream SSE)

public protocol URLAnalyzeStreaming: Sendable {
    func analyze(baseURL: URL, sourceUrl: String)
        -> AsyncThrowingStream<AnalyzeEvent, Error>
}

public struct URLAnalyzeClient: URLAnalyzeStreaming {
    public enum URLAnalyzeError: Error, LocalizedError, Equatable {
        case badStatus(Int)
        case serverError(String)
        case streamEndedWithoutResult

        public var errorDescription: String? {
            switch self {
            case let .badStatus(code): return "Analysis failed (HTTP \(code))"
            case let .serverError(message): return message
            case .streamEndedWithoutResult: return "Analysis ended unexpectedly"
            }
        }
    }

    /// JSON body jam.js sends (startSseAnalysis): deep mode forces
    /// stem separation on the local engine.
    struct RequestBody: Encodable {
        let url: String
        let sourceKind = "auto"
        let platform = "auto"
        let fastMode = false
        let analysisMode = "deep"
        let useLocalEngine = true
        let extractMidi = true

        enum CodingKeys: String, CodingKey {
            case url
            case sourceKind = "source_kind"
            case platform
            case fastMode = "fast_mode"
            case analysisMode = "analysis_mode"
            case useLocalEngine = "use_local_engine"
            case extractMidi = "extract_midi"
        }
    }

    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func analyze(baseURL: URL, sourceUrl: String)
        -> AsyncThrowingStream<AnalyzeEvent, Error>
    {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var request = URLRequest(
                        url: baseURL.appendingPathComponent("api/analyze-url-stream"))
                    request.httpMethod = "POST"
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.httpBody = try JSONEncoder().encode(RequestBody(url: sourceUrl))
                    request.timeoutInterval = 15 * 60
                    // Attribute the resulting history entry to this device.
                    AuthContext.shared.apply(to: &request)

                    let (bytes, response) = try await session.bytes(for: request)
                    guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                        throw URLAnalyzeError.badStatus(
                            (response as? HTTPURLResponse)?.statusCode ?? -1)
                    }

                    for try await line in bytes.lines {
                        switch Self.parseSSELine(line) {
                        case .none:
                            continue
                        case let .event(event):
                            continuation.yield(event)
                            if case .completed = event {
                                continuation.finish()
                                return
                            }
                        case let .serverError(message):
                            throw URLAnalyzeError.serverError(message)
                        }
                    }
                    throw URLAnalyzeError.streamEndedWithoutResult
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    enum ParsedLine: Equatable {
        case none
        case event(AnalyzeEvent)
        case serverError(String)
    }

    /// One SSE line → event. Shared payload shape with the upload
    /// stream. Exposed for tests.
    static func parseSSELine(_ line: String) -> ParsedLine {
        guard line.hasPrefix("data:") else { return .none }
        let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
        guard let data = payload.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = obj["type"] as? String
        else { return .none }

        switch type {
        case "progress":
            return .event(.progress(
                message: obj["message"] as? String ?? "",
                percent: obj["percent"] as? Double
            ))
        case "result":
            if let inner = obj["data"] as? [String: Any],
               let historyId = (inner["history_id"] as? String)
                ?? (inner["analysis_id"] as? String)
                ?? (inner["id"] as? String) {
                return .event(.completed(historyId: historyId))
            }
            return .none
        case "error":
            return .serverError(obj["message"] as? String ?? "Analysis failed")
        default:
            return .none
        }
    }
}
