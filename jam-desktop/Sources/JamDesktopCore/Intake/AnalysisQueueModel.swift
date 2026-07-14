// AnalysisQueueModel.swift
//
// Background analysis queue: every import (URL paste, file upload,
// demo CC track) becomes a row the user can watch from the Band Room
// while jamming elsewhere. Nothing blocks — enqueue as many as you
// like; the server/worker serializes actual work and reports a queue
// position.
//
// Rows for server jobs (upload/demo) survive app restarts via
// GET /api/jobs (`refreshFromServer`). URL rows are inline SSE with no
// job id, so they live only as long as the app run — documented
// tradeoff.
//
// Completion NEVER navigates. Done rows get an explicit Open action in
// the UI; this model only flips row status.

import Combine
import Foundation
import ToneForgeEngine

/// How a queue row came to exist.
public enum JobSourceKind: Sendable, Equatable {
    case url
    case upload
    case demo
    /// Recovered from GET /api/jobs after a restart.
    case restored
}

/// Row lifecycle. Mirrors JobState.status server-side, plus the local
/// pre-submit moment.
public enum QueueItemStatus: Equatable, Sendable {
    /// Waiting for the engine worker; `position` is the server's
    /// 1-based FIFO slot when known.
    case queued(position: Int?)
    case running(message: String, percent: Double?)
    case done(historyId: String)
    case error(String)

    public var isActive: Bool {
        switch self {
        case .queued, .running: return true
        case .done, .error: return false
        }
    }
}

public struct AnalysisQueueItem: Identifiable, Equatable, Sendable {
    /// Stable SwiftUI identity: local UUID for rows created here (kept
    /// even after the server assigns a job id), job id for restored
    /// rows.
    public let id: String
    public var jobId: String?
    public var title: String
    public let kind: JobSourceKind
    public var status: QueueItemStatus
    public let createdAt: Date

    public init(
        id: String, jobId: String?, title: String, kind: JobSourceKind,
        status: QueueItemStatus, createdAt: Date = Date()
    ) {
        self.id = id
        self.jobId = jobId
        self.title = title
        self.kind = kind
        self.status = status
        self.createdAt = createdAt
    }
}

@MainActor
public final class AnalysisQueueModel: ObservableObject {
    // MARK: - Observable state

    /// Newest first.
    @Published public private(set) var items: [AnalysisQueueItem] = []
    /// Last refreshFromServer failure (UI may show quietly or ignore).
    @Published public private(set) var refreshError: String?

    public var activeCount: Int {
        items.filter { $0.status.isActive }.count
    }

    // MARK: - Injected clients

    private let uploadClient: UploadSubmitting
    private let urlClient: URLAnalyzeStreaming
    private let jobClient: JobSubmitting
    private let ccClient: CCTrackProviding
    private let listClient: JobsListing

    /// Backoff between reconnect attempts after a job stream drops.
    /// Settable so tests don't sleep.
    var reattachDelay: Duration = .seconds(2)
    /// Reconnect attempts before giving up on a silent job.
    var maxReattachAttempts = 30

    private var watchers: [String: Task<Void, Never>] = [:]

    public init(
        uploadClient: UploadSubmitting = UploadClient(),
        urlClient: URLAnalyzeStreaming = URLAnalyzeClient(),
        jobClient: JobSubmitting = BackendJobClient(),
        ccClient: CCTrackProviding = BackendCCTrackClient(),
        listClient: JobsListing = BackendJobsListClient()
    ) {
        self.uploadClient = uploadClient
        self.urlClient = urlClient
        self.jobClient = jobClient
        self.ccClient = ccClient
        self.listClient = listClient
    }

    // MARK: - Enqueue (all non-blocking, no busy guard)

    /// Paste-a-URL analysis: one inline SSE straight to result. No job
    /// id, so this row can't be recovered after a restart.
    public func enqueueURL(baseURL: URL, sourceUrl: String) {
        let trimmed = sourceUrl.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let item = AnalysisQueueItem(
            id: UUID().uuidString, jobId: nil, title: trimmed, kind: .url,
            status: .running(message: "Starting analysis…", percent: nil)
        )
        items.insert(item, at: 0)
        let itemId = item.id
        watchers[itemId] = Task { [weak self, urlClient] in
            defer { self?.watchers[itemId] = nil }
            do {
                for try await event in urlClient.analyze(baseURL: baseURL, sourceUrl: trimmed) {
                    guard !Task.isCancelled else { return }
                    switch event {
                    case let .progress(message, percent):
                        self?.applyProgress(itemId: itemId, message: message, percent: percent)
                    case let .completed(historyId):
                        self?.setStatus(itemId: itemId, .done(historyId: historyId))
                        return
                    }
                }
                if !Task.isCancelled {
                    self?.setStatus(itemId: itemId, .error("Analysis ended unexpectedly"))
                }
            } catch is CancellationError {
                // dismissed — row already removed
            } catch {
                if !Task.isCancelled {
                    self?.setStatus(itemId: itemId, .error(error.localizedDescription))
                }
            }
        }
    }

    /// Local-file upload → server job. The UI gates on the attestation
    /// checkbox; a call without it becomes an immediate error row so
    /// the user sees why nothing started.
    public func enqueueUpload(
        baseURL: URL, fileURL: URL, filename: String, attested: Bool
    ) {
        guard attested else {
            items.insert(
                AnalysisQueueItem(
                    id: UUID().uuidString, jobId: nil, title: filename,
                    kind: .upload,
                    status: .error("Confirm you own the rights to this recording first.")
                ),
                at: 0
            )
            return
        }
        enqueueJob(kind: .upload, title: filename, baseURL: baseURL) { [uploadClient] in
            try await uploadClient.submitUpload(
                baseURL: baseURL, fileURL: fileURL, filename: filename
            ).jobId
        }
    }

    /// Curated CC demo track: server-side download → job.
    public func enqueueDemoImport(baseURL: URL, trackId: String, title: String) {
        enqueueJob(kind: .demo, title: title, baseURL: baseURL) { [ccClient] in
            try await ccClient.startImport(baseURL: baseURL, trackId: trackId)
        }
    }

    // MARK: - Row actions

    /// Remove a row. For a URL row this cancels the live SSE — a real
    /// cancel. Server jobs keep running server-side (no cancel API);
    /// the row just stops being tracked here.
    public func dismiss(id: String) {
        watchers[id]?.cancel()
        watchers[id] = nil
        items.removeAll { $0.id == id }
    }

    public func clearFinished() {
        items.removeAll { !$0.status.isActive }
    }

    // MARK: - Restart recovery

    /// Merge this caller's server-side jobs into the queue. Known rows
    /// with a live watcher only take queue-position updates (the stream
    /// is fresher); watcherless known rows adopt the server status;
    /// unknown jobs appear as restored rows (and get a watcher while
    /// active). URL rows are untouched — they have no job id.
    public func refreshFromServer(baseURL: URL, limit: Int = 50) async {
        let rows: [JobListRow]
        do {
            rows = try await listClient.listJobs(baseURL: baseURL, limit: limit)
        } catch {
            refreshError = error.localizedDescription
            return
        }
        refreshError = nil
        for row in rows {
            if let index = items.firstIndex(where: { $0.jobId == row.jobId }) {
                if watchers[items[index].id] != nil {
                    if case .queued = items[index].status, row.status == "queued" {
                        items[index].status = .queued(position: row.queuePosition)
                    }
                } else {
                    items[index].status = Self.status(from: row)
                }
            } else {
                let item = AnalysisQueueItem(
                    id: row.jobId, jobId: row.jobId,
                    title: row.filename ?? "Analysis", kind: .restored,
                    status: Self.status(from: row),
                    createdAt: Date(
                        timeIntervalSince1970: row.createdAt
                            ?? Date().timeIntervalSince1970)
                )
                items.append(item)
                if item.status.isActive {
                    startJobWatcher(itemId: item.id, baseURL: baseURL, jobId: row.jobId)
                }
            }
        }
        items.sort { $0.createdAt > $1.createdAt }
    }

    // MARK: - Test hooks

    /// Wait for every in-flight watcher to finish. Each watcher removes
    /// itself from `watchers` (defer) as it completes.
    func awaitAll() async {
        while let (_, task) = watchers.first {
            await task.value
        }
    }

    // MARK: - Internals

    /// Shared server-job path: create a queued row, submit off-row,
    /// then follow the job's event stream.
    private func enqueueJob(
        kind: JobSourceKind, title: String, baseURL: URL,
        submit: @escaping @Sendable () async throws -> String
    ) {
        let item = AnalysisQueueItem(
            id: UUID().uuidString, jobId: nil, title: title, kind: kind,
            status: .queued(position: nil)
        )
        items.insert(item, at: 0)
        let itemId = item.id
        watchers[itemId] = Task { [weak self] in
            defer { self?.watchers[itemId] = nil }
            do {
                let jobId = try await submit()
                guard !Task.isCancelled, let self else { return }
                if let index = self.items.firstIndex(where: { $0.id == itemId }) {
                    self.items[index].jobId = jobId
                }
                await self.followJob(itemId: itemId, baseURL: baseURL, jobId: jobId)
            } catch is CancellationError {
                // dismissed
            } catch {
                if !Task.isCancelled {
                    self?.setStatus(itemId: itemId, .error(error.localizedDescription))
                }
            }
        }
    }

    /// Restored-row watcher (no submit step).
    private func startJobWatcher(itemId: String, baseURL: URL, jobId: String) {
        watchers[itemId] = Task { [weak self] in
            defer { self?.watchers[itemId] = nil }
            await self?.followJob(itemId: itemId, baseURL: baseURL, jobId: jobId)
        }
    }

    /// Consume the job event stream until terminal. A dropped stream
    /// (60s read timeout, network blip, server restart) falls back to
    /// a one-shot snapshot; if the job is still active we reattach
    /// after a backoff, up to `maxReattachAttempts`.
    private func followJob(itemId: String, baseURL: URL, jobId: String) async {
        var attempts = 0
        while !Task.isCancelled {
            do {
                for try await event in jobClient.events(baseURL: baseURL, jobId: jobId) {
                    guard !Task.isCancelled else { return }
                    switch event {
                    case let .progress(message, percent):
                        applyProgress(itemId: itemId, message: message, percent: percent)
                    case let .completed(historyId):
                        setStatus(itemId: itemId, .done(historyId: historyId))
                        return
                    }
                }
                // Stream ended without terminal — treat as a drop.
            } catch is CancellationError {
                return
            } catch let error as JobClientError {
                // A server-reported job failure is terminal; transport
                // trouble falls through to the snapshot check.
                if case let .serverError(message) = error {
                    setStatus(itemId: itemId, .error(message))
                    return
                }
            } catch {
                // Transport error — snapshot check below.
            }
            if Task.isCancelled { return }

            do {
                let row = try await listClient.fetchJob(baseURL: baseURL, jobId: jobId)
                let status = Self.status(from: row)
                if !status.isActive {
                    setStatus(itemId: itemId, status)
                    return
                }
            } catch JobsListError.badStatus(404) {
                setStatus(itemId: itemId, .error("This analysis is no longer available."))
                return
            } catch {
                // Snapshot also failed — keep retrying below.
            }

            attempts += 1
            if attempts >= maxReattachAttempts {
                setStatus(
                    itemId: itemId,
                    .error("Lost connection to the analysis. It may still finish server-side.")
                )
                return
            }
            try? await Task.sleep(for: reattachDelay)
        }
    }

    /// Progress card parity with jam.js: keep the last non-empty
    /// message, clamp percent to [2, 100] so the bar never looks dead.
    /// A progress event also promotes a queued row to running.
    private func applyProgress(itemId: String, message: String, percent: Double?) {
        guard let index = items.firstIndex(where: { $0.id == itemId }) else { return }
        let (prevMessage, prevPercent): (String, Double?)
        switch items[index].status {
        case let .running(m, p): (prevMessage, prevPercent) = (m, p)
        case .queued: (prevMessage, prevPercent) = ("", nil)
        case .done, .error: return
        }
        let newMessage = message.isEmpty ? prevMessage : message
        let newPercent = percent.map { min(100, max(2, $0)) } ?? prevPercent
        items[index].status = .running(message: newMessage, percent: newPercent)
    }

    private func setStatus(itemId: String, _ status: QueueItemStatus) {
        guard let index = items.firstIndex(where: { $0.id == itemId }) else { return }
        items[index].status = status
    }

    /// Server row → local status.
    static func status(from row: JobListRow) -> QueueItemStatus {
        switch row.status {
        case "queued":
            return .queued(position: row.queuePosition)
        case "running":
            return .running(message: row.message ?? "", percent: row.percent)
        case "done":
            if let historyId = row.historyId {
                return .done(historyId: historyId)
            }
            return .error("Analysis finished without a result")
        case "error":
            return .error(row.error ?? "Analysis failed")
        default:
            return .running(message: row.message ?? "", percent: row.percent)
        }
    }
}
