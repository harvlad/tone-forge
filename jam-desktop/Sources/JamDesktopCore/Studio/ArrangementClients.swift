// ArrangementClients.swift
//
// Multipart clients for the Studio Phase 3 endpoints (none of them
// admin-guarded):
//   POST /api/preview-waveform  file → WaveformPreview
//   POST /api/detect-sections   file (+ tempo hint) → arrangement
//   POST /api/analyze-region    file + bounds → region deep-dive
// plus the trimmed re-run over /api/analyze-stream (data-only SSE,
// same payload dialect as analyze-url-stream).

import Foundation
import ToneForgeEngine

// MARK: - preview-waveform

public protocol WaveformPreviewing: Sendable {
    func previewWaveform(
        baseURL: URL, fileURL: URL, filename: String
    ) async throws -> WaveformPreview
}

public struct WaveformPreviewClient: WaveformPreviewing {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func previewWaveform(
        baseURL: URL, fileURL: URL, filename: String
    ) async throws -> WaveformPreview {
        try await postMultipart(
            session: session,
            url: baseURL.appendingPathComponent("api/preview-waveform"),
            fileURL: fileURL, filename: filename,
            fields: [], timeout: 120)
    }
}

// MARK: - detect-sections

public protocol SectionDetecting: Sendable {
    func detectSections(
        baseURL: URL, fileURL: URL, filename: String, tempo: Double?
    ) async throws -> ArrangementAnalysisDTO
}

public struct SectionDetectClient: SectionDetecting {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func detectSections(
        baseURL: URL, fileURL: URL, filename: String, tempo: Double?
    ) async throws -> ArrangementAnalysisDTO {
        var fields: [(name: String, value: String)] = []
        if let tempo {
            fields.append(("tempo", String(tempo)))
        }
        return try await postMultipart(
            session: session,
            url: baseURL.appendingPathComponent("api/detect-sections"),
            fileURL: fileURL, filename: filename,
            fields: fields, timeout: 5 * 60)
    }
}

// MARK: - analyze-region

public protocol RegionAnalyzing: Sendable {
    func analyzeRegion(
        baseURL: URL, fileURL: URL, filename: String,
        startTime: Double, endTime: Double, stemType: String
    ) async throws -> RegionAnalysisDTO
}

public struct RegionAnalyzeClient: RegionAnalyzing {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func analyzeRegion(
        baseURL: URL, fileURL: URL, filename: String,
        startTime: Double, endTime: Double, stemType: String
    ) async throws -> RegionAnalysisDTO {
        try await postMultipart(
            session: session,
            url: baseURL.appendingPathComponent("api/analyze-region"),
            fileURL: fileURL, filename: filename,
            fields: [
                ("start_time", String(startTime)),
                ("end_time", String(endTime)),
                ("stem_type", stemType),
                ("include_midi", "true"),
                ("include_provenance", "true"),
            ],
            timeout: 5 * 60)
    }
}

// MARK: - trimmed analyze-stream

public protocol StudioStreamAnalyzing: Sendable {
    func analyze(
        baseURL: URL, fileURL: URL, filename: String,
        startTime: Double?, endTime: Double?
    ) -> AsyncThrowingStream<AnalyzeEvent, Error>
}

public struct StudioStreamClient: StudioStreamAnalyzing {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    /// Fixed fields matching studio.html's trimmed re-run: studio
    /// analysis mode, fast (no server stem separation), MIDI on. The
    /// desktop Studio is an operator tool over the operator's own
    /// files, so attested is always true.
    static func formFields(
        startTime: Double?, endTime: Double?
    ) -> [(name: String, value: String)] {
        var fields: [(name: String, value: String)] = [
            ("source_kind", "auto"),
            ("platform", "auto"),
            ("extract_midi", "true"),
            ("fast_mode", "true"),
            ("analysis_mode", "studio"),
            ("attested", "true"),
        ]
        if let startTime {
            fields.append(("start_time", String(startTime)))
        }
        if let endTime {
            fields.append(("end_time", String(endTime)))
        }
        return fields
    }

    public func analyze(
        baseURL: URL, fileURL: URL, filename: String,
        startTime: Double?, endTime: Double?
    ) -> AsyncThrowingStream<AnalyzeEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let boundary = "tfstudio-\(UUID().uuidString)"
                    let bodyFile = try AnalyzeClient.writeMultipartBodyFile(
                        fileURL: fileURL,
                        filename: filename,
                        contentType: "application/octet-stream",
                        fields: Self.formFields(
                            startTime: startTime, endTime: endTime),
                        boundary: boundary
                    )
                    defer { try? FileManager.default.removeItem(at: bodyFile) }

                    var request = URLRequest(
                        url: baseURL.appendingPathComponent("api/analyze-stream"))
                    request.httpMethod = "POST"
                    request.setValue(
                        "multipart/form-data; boundary=\(boundary)",
                        forHTTPHeaderField: "Content-Type"
                    )
                    request.timeoutInterval = 15 * 60
                    // Attribute the history entry to this device.
                    AuthContext.shared.apply(to: &request)

                    // Streamed request body + streamed SSE response:
                    // URLSession.bytes has no fromFile variant, so the
                    // multipart body goes up as an InputStream with an
                    // explicit Content-Length.
                    let bodySize = (try? FileManager.default
                        .attributesOfItem(atPath: bodyFile.path)[.size]
                        as? NSNumber)?.intValue
                    if let bodySize {
                        request.setValue(
                            String(bodySize),
                            forHTTPHeaderField: "Content-Length")
                    }
                    request.httpBodyStream = InputStream(url: bodyFile)

                    let (bytes, response) = try await session.bytes(for: request)
                    guard let http = response as? HTTPURLResponse,
                          http.statusCode == 200 else {
                        throw URLAnalyzeClient.URLAnalyzeError.badStatus(
                            (response as? HTTPURLResponse)?.statusCode ?? -1)
                    }

                    for try await line in bytes.lines {
                        switch URLAnalyzeClient.parseSSELine(line) {
                        case .none:
                            continue
                        case let .event(event):
                            continuation.yield(event)
                            if case .completed = event {
                                continuation.finish()
                                return
                            }
                        case let .serverError(message):
                            throw URLAnalyzeClient.URLAnalyzeError
                                .serverError(message)
                        }
                    }
                    throw URLAnalyzeClient.URLAnalyzeError
                        .streamEndedWithoutResult
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

// MARK: - shared multipart POST

private func postMultipart<T: Decodable>(
    session: URLSession,
    url: URL,
    fileURL: URL,
    filename: String,
    fields: [(name: String, value: String)],
    timeout: TimeInterval
) async throws -> T {
    let boundary = "tfstudio-\(UUID().uuidString)"
    let bodyFile = try AnalyzeClient.writeMultipartBodyFile(
        fileURL: fileURL,
        filename: filename,
        contentType: "application/octet-stream",
        fields: fields,
        boundary: boundary
    )
    defer { try? FileManager.default.removeItem(at: bodyFile) }

    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    request.setValue(
        "multipart/form-data; boundary=\(boundary)",
        forHTTPHeaderField: "Content-Type"
    )
    request.timeoutInterval = timeout

    let (data, response) = try await session.upload(
        for: request, fromFile: bodyFile)
    guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
        let code = (response as? HTTPURLResponse)?.statusCode ?? -1
        throw URLError(
            .badServerResponse,
            userInfo: [NSLocalizedDescriptionKey:
                "HTTP \(code) from \(url.path): "
                + (String(data: data, encoding: .utf8)?.prefix(200)
                    .description ?? "")])
    }
    return try JSONDecoder().decode(T.self, from: data)
}
