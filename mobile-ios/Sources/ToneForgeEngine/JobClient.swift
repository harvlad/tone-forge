// JobClient.swift
//
// Client for the async analysis job API (tone_forge_api.py):
//
//   POST {base}/api/analyze-upload     -> {"job_id": "..."}   (returns fast)
//   GET  {base}/api/job/{id}/events    -> reconnectable SSE, live percent
//   GET  {base}/api/job/{id}/result    -> long-poll terminal result
//
// The job survives the connection: the phone can lock, the app can be
// backgrounded, and the analysis keeps running server-side. The client
// attaches for live progress (`events`) in the foreground and hands the
// same job to a background URLSession poll of `/result` when it can't
// hold a live connection.
//
// `submit` reuses AnalyzeClient's multipart encoder; the event stream
// parses the job's JSON snapshots (not the analyze-stream frame shape).
//
// The transport is a protocol (`JobSubmitting`) so UI tests can stub the
// network entirely.

import Foundation

public enum JobClientError: Error, LocalizedError, Equatable {
    case badStatus(Int)
    case missingJobId
    case serverError(String)
    case streamEndedWithoutResult

    public var errorDescription: String? {
        switch self {
        case .badStatus(let code):
            return "The server rejected the upload (HTTP \(code))."
        case .missingJobId:
            return "The server accepted the upload but returned no job id."
        case .serverError(let message):
            return message
        case .streamEndedWithoutResult:
            return "The connection closed before the analysis finished."
        }
    }
}

/// Transport seam over the job API for test stubbing.
public protocol JobSubmitting: Sendable {
    /// Upload the WAV, create a job, return its id. `extraFields` are
    /// appended to the multipart form after the default analysis
    /// fields — used for attribution metadata (D-024: title/artist),
    /// since the on-device transcode strips file tags before upload.
    /// `onUploadProgress` receives the fraction of request bytes sent
    /// (0…1) so the UI can show a REAL upload bar — stubs may ignore
    /// it.
    func submit(
        baseURL: URL, wavFileURL: URL, filename: String,
        extraFields: [(name: String, value: String)],
        onUploadProgress: (@Sendable (Double) -> Void)?
    ) async throws -> String
    /// Stream live progress for a job; finishes after a single
    /// `.completed(historyId:)`. Throws on a server-reported error.
    func events(baseURL: URL, jobId: String) -> AsyncThrowingStream<AnalyzeEvent, Error>
}

public extension JobSubmitting {
    /// Convenience overloads — protocols can't carry default arguments.
    func submit(baseURL: URL, wavFileURL: URL, filename: String) async throws -> String {
        try await submit(
            baseURL: baseURL, wavFileURL: wavFileURL,
            filename: filename, extraFields: [], onUploadProgress: nil
        )
    }

    func submit(
        baseURL: URL, wavFileURL: URL, filename: String,
        extraFields: [(name: String, value: String)]
    ) async throws -> String {
        try await submit(
            baseURL: baseURL, wavFileURL: wavFileURL,
            filename: filename, extraFields: extraFields,
            onUploadProgress: nil
        )
    }
}

/// Production transport hitting the real backend.
public struct BackendJobClient: JobSubmitting {
    /// Gap-between-data timeout for the submit upload. Analyses are
    /// decoupled from this request now, but a large upload still needs
    /// headroom.
    private let uploadTimeout: TimeInterval

    public init(uploadTimeout: TimeInterval = 5 * 60) {
        self.uploadTimeout = uploadTimeout
    }

    public func submit(
        baseURL: URL, wavFileURL: URL, filename: String,
        extraFields: [(name: String, value: String)],
        onUploadProgress: (@Sendable (Double) -> Void)?
    ) async throws -> String {
        let boundary = "toneforge-\(UUID().uuidString)"
        // Engine-job path (/api/analyze-upload), NOT the legacy
        // /api/analyze-job: the production backend has no GPU — jobs
        // must go to the remote GPU engine worker, not the VPS CPU
        // (which crawls/stalls mid-pipeline on real songs). Response
        // shape is the same {"job_id": ...}; /api/job/{id}/events is
        // shared by both job kinds.
        var request = URLRequest(url: baseURL.appendingPathComponent("api/analyze-upload"))
        request.httpMethod = "POST"
        request.timeoutInterval = uploadTimeout
        request.setValue(
            "multipart/form-data; boundary=\(boundary)",
            forHTTPHeaderField: "Content-Type"
        )
        AuthContext.shared.apply(to: &request)

        // Compose the multipart body on disk and hand URLSession a
        // file — the WAV never has to fit in memory.
        let bodyFile = try AnalyzeClient.writeMultipartBodyFile(
            fileURL: wavFileURL,
            filename: filename,
            contentType: "audio/wav",
            fields: AnalyzeClient.defaultFields + extraFields,
            boundary: boundary
        )
        defer { try? FileManager.default.removeItem(at: bodyFile) }

        // A dedicated session with a task delegate reports real byte
        // progress (URLSession.shared can't). Invalidated afterwards so
        // sessions don't leak across submits.
        let progressDelegate = UploadProgressDelegate(onProgress: onUploadProgress)
        let session = URLSession(
            configuration: .default, delegate: progressDelegate, delegateQueue: nil
        )
        defer { session.finishTasksAndInvalidate() }
        let (data, response) = try await session.upload(
            for: request, fromFile: bodyFile
        )
        if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            throw JobClientError.badStatus(http.statusCode)
        }
        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let dict = object as? [String: Any],
            let jobId = dict["job_id"] as? String, !jobId.isEmpty
        else {
            throw JobClientError.missingJobId
        }
        return jobId
    }

    public func events(
        baseURL: URL, jobId: String
    ) -> AsyncThrowingStream<AnalyzeEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let url = baseURL
                        .appendingPathComponent("api/job/\(jobId)/events")
                    var request = URLRequest(url: url)
                    // The server heartbeats every ~15s; give the read a
                    // comfortable margin over that.
                    request.timeoutInterval = 60
                    AuthContext.shared.apply(to: &request)
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    if let http = response as? HTTPURLResponse, http.statusCode != 200 {
                        throw JobClientError.badStatus(http.statusCode)
                    }
                    for try await line in bytes.lines {
                        guard let snapshot = Self.parseJobLine(line) else { continue }
                        if let message = snapshot.progressMessage {
                            continuation.yield(
                                .progress(message: message, percent: snapshot.percent)
                            )
                        }
                        switch snapshot.status {
                        case "done":
                            guard let historyId = snapshot.historyId else {
                                throw JobClientError.streamEndedWithoutResult
                            }
                            continuation.yield(.completed(historyId: historyId))
                            continuation.finish()
                            return
                        case "error":
                            throw JobClientError.serverError(
                                snapshot.error ?? "Analysis failed."
                            )
                        default:
                            break
                        }
                    }
                    // Stream closed without a terminal snapshot.
                    throw JobClientError.streamEndedWithoutResult
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    // MARK: - Upload progress

    /// Task delegate that forwards multipart-body byte progress to a
    /// closure as a 0…1 fraction.
    private final class UploadProgressDelegate: NSObject, URLSessionTaskDelegate {
        private let onProgress: (@Sendable (Double) -> Void)?

        init(onProgress: (@Sendable (Double) -> Void)?) {
            self.onProgress = onProgress
        }

        func urlSession(
            _ session: URLSession,
            task: URLSessionTask,
            didSendBodyData bytesSent: Int64,
            totalBytesSent: Int64,
            totalBytesExpectedToSend: Int64
        ) {
            guard totalBytesExpectedToSend > 0 else { return }
            onProgress?(
                min(1, Double(totalBytesSent) / Double(totalBytesExpectedToSend))
            )
        }
    }

    // MARK: - SSE parsing (pure)

    /// Decoded fields from one job snapshot SSE line. Non-`data:` lines
    /// (heartbeat comments, blanks) return nil.
    public struct JobSnapshot: Equatable {
        public let status: String
        public let percent: Double?
        public let progressMessage: String?
        public let historyId: String?
        public let error: String?
    }

    public static func parseJobLine(_ line: String) -> JobSnapshot? {
        guard line.hasPrefix("data: ") else { return nil }
        let payload = line.dropFirst("data: ".count)
        guard
            let object = try? JSONSerialization.jsonObject(with: Data(payload.utf8)),
            let dict = object as? [String: Any],
            let status = dict["status"] as? String
        else {
            return nil
        }
        let message = dict["message"] as? String
        return JobSnapshot(
            status: status,
            percent: (dict["percent"] as? NSNumber)?.doubleValue,
            progressMessage: (message?.isEmpty == false) ? message : nil,
            historyId: dict["history_id"] as? String,
            error: dict["error"] as? String
        )
    }
}
