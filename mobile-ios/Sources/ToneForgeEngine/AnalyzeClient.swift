// AnalyzeClient.swift
//
// Upload a canonical analysis WAV to the backend and stream analysis
// progress back over SSE.
//
// Endpoint: POST {base}/api/analyze-stream (tone_forge_api.py). The
// backend emits single-line SSE frames:
//
//   data: {"type":"progress","message":"...","percent":42}
//   data: {"type":"result","data":{...,"history_id":"abc123"}}
//   data: {"type":"error","message":"..."}
//
// Form fields mirror the JAM web client (jam.js): fast_mode=false +
// analysis_mode=deep force stem separation, which the mobile JAM needs
// for chop playback; extract_midi=true for instrument voices.
//
// The multipart encoder and the SSE line parser are pure functions so
// they can be golden-tested without a network.

import Foundation

public enum AnalyzeClientError: Error, LocalizedError, Equatable {
    /// Non-200 HTTP status before the stream started.
    case badStatus(Int)
    /// The backend emitted an SSE `error` frame.
    case serverError(String)
    /// A `result` frame arrived without a `history_id`.
    case missingHistoryId
    /// The stream ended without a `result` frame.
    case streamEndedWithoutResult

    public var errorDescription: String? {
        switch self {
        case .badStatus(let code):
            return "The server rejected the upload (HTTP \(code))."
        case .serverError(let message):
            return message
        case .missingHistoryId:
            return "The analysis finished but returned no id."
        case .streamEndedWithoutResult:
            return "The connection closed before the analysis finished."
        }
    }
}

/// Client-facing events from an analyze stream.
public enum AnalyzeEvent: Equatable, Sendable {
    case progress(message: String, percent: Double?)
    case completed(historyId: String)
}

public enum AnalyzeClient {

    /// Form fields sent alongside the file, mirroring the JAM web
    /// client. Order matters for the multipart golden test.
    public static let defaultFields: [(name: String, value: String)] = [
        (name: "source_kind", value: "upload"),
        (name: "platform", value: "auto"),
        (name: "extract_midi", value: "true"),
        (name: "fast_mode", value: "false"),
        (name: "analysis_mode", value: "deep"),
        // Every mobile upload passes the ImportCoordinator's ownership
        // attestation gate before it reaches the network, so the client
        // asserts it here and the server records it with the job/history
        // entry (the server-side half of the compliance trail).
        (name: "attested", value: "true"),
    ]

    // MARK: - Multipart encoding (pure)

    /// Build a multipart/form-data body: text fields first, then the
    /// file part, then the closing boundary. CRLF line endings per
    /// RFC 2046.
    public static func multipartBody(
        fileData: Data,
        filename: String,
        contentType: String,
        fields: [(name: String, value: String)],
        boundary: String
    ) -> Data {
        var body = multipartPrefix(
            filename: filename,
            contentType: contentType,
            fields: fields,
            boundary: boundary
        )
        body.append(fileData)
        body.append(multipartSuffix(boundary: boundary))
        return body
    }

    /// Everything before the file bytes: text fields plus the file
    /// part's headers.
    public static func multipartPrefix(
        filename: String,
        contentType: String,
        fields: [(name: String, value: String)],
        boundary: String
    ) -> Data {
        var body = Data()
        func append(_ s: String) { body.append(Data(s.utf8)) }

        for field in fields {
            append("--\(boundary)\r\n")
            append("Content-Disposition: form-data; name=\"\(field.name)\"\r\n\r\n")
            append("\(field.value)\r\n")
        }
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n")
        append("Content-Type: \(contentType)\r\n\r\n")
        return body
    }

    /// Closing boundary after the file bytes.
    public static func multipartSuffix(boundary: String) -> Data {
        Data("\r\n--\(boundary)--\r\n".utf8)
    }

    /// Compose the multipart body into a temp file, streaming the
    /// audio in 1 MB chunks so large recordings never sit in memory.
    /// Pair with `URLSession.upload(for:fromFile:)`. Caller deletes
    /// the returned file when the upload finishes.
    public static func writeMultipartBodyFile(
        fileURL: URL,
        filename: String,
        contentType: String,
        fields: [(name: String, value: String)],
        boundary: String
    ) throws -> URL {
        let tempURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("toneforge-upload-\(UUID().uuidString).multipart")
        FileManager.default.createFile(atPath: tempURL.path, contents: nil)
        let output = try FileHandle(forWritingTo: tempURL)
        defer { try? output.close() }

        try output.write(contentsOf: multipartPrefix(
            filename: filename,
            contentType: contentType,
            fields: fields,
            boundary: boundary
        ))

        let input = try FileHandle(forReadingFrom: fileURL)
        defer { try? input.close() }
        while let chunk = try input.read(upToCount: 1 << 20), !chunk.isEmpty {
            try output.write(contentsOf: chunk)
        }

        try output.write(contentsOf: multipartSuffix(boundary: boundary))
        return tempURL
    }

    // MARK: - SSE parsing (pure)

    /// One decoded SSE frame from the analyze stream.
    public enum SSEFrame: Equatable {
        case progress(message: String, percent: Double?)
        /// `history_id` from the result payload, if present.
        case result(historyId: String?)
        case error(message: String)
    }

    /// Parse a single SSE line. Returns nil for blank lines, comment
    /// keep-alives, unknown frame types, and malformed JSON (the web
    /// client is equally tolerant — never kill a long analysis over
    /// one bad frame).
    public static func parseSSELine(_ line: String) -> SSEFrame? {
        guard line.hasPrefix("data: ") else { return nil }
        let payload = line.dropFirst("data: ".count)
        guard
            let object = try? JSONSerialization.jsonObject(with: Data(payload.utf8)),
            let dict = object as? [String: Any],
            let type = dict["type"] as? String
        else {
            return nil
        }
        switch type {
        case "progress":
            let message = dict["message"] as? String ?? ""
            let percent = (dict["percent"] as? NSNumber)?.doubleValue
            return .progress(message: message, percent: percent)
        case "result":
            let data = dict["data"] as? [String: Any]
            return .result(historyId: data?["history_id"] as? String)
        case "error":
            return .error(message: dict["message"] as? String ?? "Analysis failed.")
        default:
            return nil
        }
    }

    // MARK: - Streaming upload

    /// Upload `wavFileURL` and stream progress events. Yields
    /// `.progress` frames as they arrive and finishes after a single
    /// `.completed(historyId:)`. Throws `AnalyzeClientError` on
    /// server-reported errors or a stream that ends early.
    public static func analyzeStream(
        baseURL: URL,
        wavFileURL: URL,
        filename: String,
        timeout: TimeInterval = 15 * 60,
        session: URLSession = .shared
    ) -> AsyncThrowingStream<AnalyzeEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    // ~21 MB for a 4-minute mono WAV — acceptable
                    // in-memory; switch to a temp-file composite if
                    // longer inputs ever land.
                    let fileData = try Data(contentsOf: wavFileURL)
                    let boundary = "toneforge-\(UUID().uuidString)"

                    var request = URLRequest(
                        url: baseURL.appendingPathComponent("api/analyze-stream")
                    )
                    request.httpMethod = "POST"
                    request.timeoutInterval = timeout
                    request.setValue(
                        "multipart/form-data; boundary=\(boundary)",
                        forHTTPHeaderField: "Content-Type"
                    )
                    request.httpBody = multipartBody(
                        fileData: fileData,
                        filename: filename,
                        contentType: "audio/wav",
                        fields: defaultFields,
                        boundary: boundary
                    )
                    AuthContext.shared.apply(to: &request)

                    let (bytes, response) = try await session.bytes(for: request)
                    if let http = response as? HTTPURLResponse, http.statusCode != 200 {
                        throw AnalyzeClientError.badStatus(http.statusCode)
                    }

                    var sawResult = false
                    for try await line in bytes.lines {
                        guard let frame = parseSSELine(line) else { continue }
                        switch frame {
                        case .progress(let message, let percent):
                            continuation.yield(.progress(message: message, percent: percent))
                        case .result(let historyId):
                            guard let historyId else {
                                throw AnalyzeClientError.missingHistoryId
                            }
                            sawResult = true
                            continuation.yield(.completed(historyId: historyId))
                        case .error(let message):
                            throw AnalyzeClientError.serverError(message)
                        }
                    }
                    guard sawResult else {
                        throw AnalyzeClientError.streamEndedWithoutResult
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
