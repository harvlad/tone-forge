// JobsListClient.swift
//
// Client for the caller-scoped job list + single-job snapshot:
//   GET /api/jobs?limit=N   -> {"jobs":[{...public_dict + queue_position}]}
//   GET /api/job/{id}       -> {...public_dict}
//
// Both shapes are JobState.public_dict() rows; the list adds a 1-based
// `queue_position` to queued engine jobs. Scoping happens server-side
// via the X-Device-Id header (stamped by AuthContext.apply), so an
// anonymous fresh install just gets an empty list.
//
// Sits behind a protocol so AnalysisQueueModel tests run with stubs.

import Foundation
import ToneForgeEngine

/// One job row as the backend reports it.
public struct JobListRow: Decodable, Sendable, Equatable {
    public let jobId: String
    public let status: String
    public let percent: Double?
    public let message: String?
    public let historyId: String?
    public let error: String?
    public let filename: String?
    public let createdAt: Double?
    public let updatedAt: Double?
    /// 1-based FIFO position; present only on queued engine jobs.
    public let queuePosition: Int?

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id"
        case status
        case percent
        case message
        case historyId = "history_id"
        case error
        case filename
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case queuePosition = "queue_position"
    }

    public init(
        jobId: String, status: String, percent: Double? = nil,
        message: String? = nil, historyId: String? = nil,
        error: String? = nil, filename: String? = nil,
        createdAt: Double? = nil, updatedAt: Double? = nil,
        queuePosition: Int? = nil
    ) {
        self.jobId = jobId
        self.status = status
        self.percent = percent
        self.message = message
        self.historyId = historyId
        self.error = error
        self.filename = filename
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.queuePosition = queuePosition
    }
}

public enum JobsListError: Error, LocalizedError, Equatable {
    case badStatus(Int)

    public var errorDescription: String? {
        switch self {
        case let .badStatus(code):
            return code == 404
                ? "This analysis is no longer available."
                : "Could not reach the analysis queue (HTTP \(code))."
        }
    }
}

public protocol JobsListing: Sendable {
    /// This caller's jobs, newest first.
    func listJobs(baseURL: URL, limit: Int) async throws -> [JobListRow]
    /// Current snapshot of one job (no queue position).
    func fetchJob(baseURL: URL, jobId: String) async throws -> JobListRow
}

public struct BackendJobsListClient: JobsListing {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func listJobs(baseURL: URL, limit: Int) async throws -> [JobListRow] {
        var components = URLComponents(
            url: baseURL.appendingPathComponent("api/jobs"),
            resolvingAgainstBaseURL: false
        )!
        components.queryItems = [URLQueryItem(name: "limit", value: String(limit))]
        let data = try await get(components.url!)
        struct Wrapper: Decodable { let jobs: [JobListRow] }
        return try JSONDecoder().decode(Wrapper.self, from: data).jobs
    }

    public func fetchJob(baseURL: URL, jobId: String) async throws -> JobListRow {
        let url = baseURL.appendingPathComponent("api/job/\(jobId)")
        let data = try await get(url)
        return try JSONDecoder().decode(JobListRow.self, from: data)
    }

    private func get(_ url: URL) async throws -> Data {
        var request = URLRequest(url: url)
        request.timeoutInterval = 15
        AuthContext.shared.apply(to: &request)
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw JobsListError.badStatus((response as? HTTPURLResponse)?.statusCode ?? -1)
        }
        return data
    }
}
