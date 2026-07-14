// PendingJobStore.swift
//
// Durable record of analysis jobs that are still in flight. Written when
// a job is submitted, cleared when it reaches a terminal state. Survives
// app termination so a background relaunch (or the next cold start) can
// resume polling and fire the completion notification for a job the user
// started before locking their phone.
//
// Small JSON file in Application Support; access is serialized on a
// private queue so the background URLSession delegate and the main actor
// can both touch it.

import Foundation

public struct PendingJob: Codable, Equatable {
    public let jobId: String
    public let title: String
    /// Absolute backend base URL string, so a relaunch can rebuild the
    /// poll URL without depending on AppState being wired yet.
    public let baseURL: String
    /// Album art captured at pick time, re-attached on completion.
    public var artworkData: Data?
    public let createdAt: Date

    public init(
        jobId: String, title: String, baseURL: String,
        artworkData: Data? = nil, createdAt: Date = Date()
    ) {
        self.jobId = jobId
        self.title = title
        self.baseURL = baseURL
        self.artworkData = artworkData
        self.createdAt = createdAt
    }
}

public final class PendingJobStore: @unchecked Sendable {
    private let fileURL: URL
    private let queue = DispatchQueue(label: "toneforge.pendingjobs")

    public init() {
        let dir = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(
            at: dir, withIntermediateDirectories: true
        )
        fileURL = dir.appendingPathComponent("pending-jobs.json")
    }

    public func all() -> [PendingJob] { queue.sync { load() } }

    public func job(id: String) -> PendingJob? {
        queue.sync { load().first { $0.jobId == id } }
    }

    public func add(_ job: PendingJob) {
        queue.sync {
            var jobs = load()
            jobs.removeAll { $0.jobId == job.jobId }
            jobs.append(job)
            save(jobs)
        }
    }

    public func remove(jobId: String) {
        queue.sync {
            var jobs = load()
            jobs.removeAll { $0.jobId == jobId }
            save(jobs)
        }
    }

    // MARK: - Disk

    private func load() -> [PendingJob] {
        guard let data = try? Data(contentsOf: fileURL) else { return [] }
        return (try? JSONDecoder().decode([PendingJob].self, from: data)) ?? []
    }

    private func save(_ jobs: [PendingJob]) {
        guard let data = try? JSONEncoder().encode(jobs) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }
}
