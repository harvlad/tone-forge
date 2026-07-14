// BackgroundAnalyzeSession.swift
//
// Polls a job's /result over a background URLSession so an analysis
// finishes — and the user gets notified — even if the app is locked,
// backgrounded, or killed. `nsurlsessiond` owns the download tasks out
// of process; when a result lands after the app was terminated, iOS
// relaunches us via
// `application(_:handleEventsForBackgroundURLSession:completionHandler:)`
// and the delegate below fires.
//
// The poll is a plain GET of /api/job/{id}/result, which the server
// long-polls for ~25s and answers 200 (done) / 500 (error) / 202 (still
// running). On 202 we re-enqueue; the server-side block throttles the
// loop. Everything needed to resume after a relaunch is recoverable from
// the task URL (the job id) plus PendingJobStore (base URL, title) — no
// in-memory task map to lose.

import Foundation

public final class BackgroundAnalyzeSession: NSObject, @unchecked Sendable {

    public static let shared = BackgroundAnalyzeSession()
    public static let sessionIdentifier = "com.harvlad.toneforge.mobile.bgAnalyze"

    /// Held while iOS delivers background events after a relaunch; called
    /// once the session drains so the system can snapshot the UI.
    public var backgroundCompletionHandler: (() -> Void)? {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _backgroundCompletionHandler }
        set { stateLock.lock(); defer { stateLock.unlock() }; _backgroundCompletionHandler = newValue }
    }

    /// Terminal-result callbacks, wired by JobCompletionCenter at boot.
    public var onResult: ((_ jobId: String, _ historyId: String) -> Void)? {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _onResult }
        set { stateLock.lock(); defer { stateLock.unlock() }; _onResult = newValue }
    }
    public var onFailure: ((_ jobId: String) -> Void)? {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _onFailure }
        set { stateLock.lock(); defer { stateLock.unlock() }; _onFailure = newValue }
    }

    // Set from the main thread, read from the session's delegate queue —
    // every access goes through stateLock (the class is @unchecked
    // Sendable, so nothing else enforces this).
    private let stateLock = NSLock()
    private var _backgroundCompletionHandler: (() -> Void)?
    private var _onResult: ((_ jobId: String, _ historyId: String) -> Void)?
    private var _onFailure: ((_ jobId: String) -> Void)?
    private var retryAttempts: [String: Int] = [:]

    private let pending = PendingJobStore()

    private lazy var session: URLSession = {
        let cfg = URLSessionConfiguration.background(
            withIdentifier: Self.sessionIdentifier
        )
        cfg.sessionSendsLaunchEvents = true
        cfg.isDiscretionary = false
        // A job may outlive many app sessions; give the poll loop room.
        cfg.timeoutIntervalForResource = 6 * 3600
        return URLSession(configuration: cfg, delegate: self, delegateQueue: nil)
    }()

    /// Instantiate the session so it reattaches to any tasks still in
    /// flight from a previous process. Safe to call repeatedly.
    public func activate() { _ = session }

    /// Resume any jobs that were in flight when the app last died.
    public func resumePendingJobs() {
        activate()
        session.getAllTasks { tasks in
            let live = Set(tasks.compactMap { Self.jobId(from: $0.originalRequest?.url) })
            for job in self.pending.all() where !live.contains(job.jobId) {
                if let base = URL(string: job.baseURL) {
                    self.enqueuePoll(jobId: job.jobId, baseURL: base)
                }
            }
        }
    }

    /// Begin polling a job's result in the background.
    public func startPolling(jobId: String, baseURL: URL) {
        activate()
        enqueuePoll(jobId: jobId, baseURL: baseURL)
    }

    /// Cancel background polling for a job (foreground stream already
    /// finished it).
    public func cancelPolling(jobId: String) {
        clearRetries(for: jobId)
        session.getAllTasks { tasks in
            for task in tasks where Self.jobId(from: task.originalRequest?.url) == jobId {
                task.cancel()
            }
        }
    }

    private func enqueuePoll(jobId: String, baseURL: URL, delay: TimeInterval = 0) {
        let url = baseURL.appendingPathComponent("api/job/\(jobId)/result")
        let task = session.downloadTask(with: url)
        if delay > 0 {
            task.earliestBeginDate = Date().addingTimeInterval(delay)
        }
        task.resume()
    }

    // MARK: - Retry bookkeeping

    /// Bump and return the attempt count for a transient failure.
    private func nextRetryAttempt(for jobId: String) -> Int {
        stateLock.lock(); defer { stateLock.unlock() }
        let attempt = retryAttempts[jobId, default: 0]
        retryAttempts[jobId] = attempt + 1
        return attempt
    }

    private func clearRetries(for jobId: String) {
        stateLock.lock(); defer { stateLock.unlock() }
        retryAttempts[jobId] = nil
    }

    /// Extract the job id from a `.../api/job/{id}/result` URL.
    static func jobId(from url: URL?) -> String? {
        guard let parts = url?.pathComponents,
              let index = parts.firstIndex(of: "job"),
              index + 1 < parts.count
        else { return nil }
        return parts[index + 1]
    }
}

extension BackgroundAnalyzeSession: URLSessionDownloadDelegate {

    public func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didFinishDownloadingTo location: URL
    ) {
        let status = (downloadTask.response as? HTTPURLResponse)?.statusCode ?? 0
        let jobId = Self.jobId(from: downloadTask.originalRequest?.url)
        let data = try? Data(contentsOf: location)
        guard let jobId else { return }

        switch status {
        case 200:
            clearRetries(for: jobId)
            if let data,
               let object = try? JSONSerialization.jsonObject(with: data),
               let dict = object as? [String: Any],
               let historyId = dict["history_id"] as? String {
                onResult?(jobId, historyId)
            } else {
                onFailure?(jobId)
            }
        case 202:
            // Still running — re-poll. Small spacing so a server that
            // answers instantly (long-poll disabled/misbehaving) can't
            // be hammered.
            clearRetries(for: jobId)
            if let job = pending.job(id: jobId), let base = URL(string: job.baseURL) {
                enqueuePoll(jobId: jobId, baseURL: base, delay: 3)
            }
        case 404, 410, 500:
            // Job unknown (server restarted and lost it) or analysis
            // failed — terminal either way.
            clearRetries(for: jobId)
            onFailure?(jobId)
        default:
            // Transient (502/503 from a proxy, etc.) — retry with
            // exponential backoff, capped at 5 minutes.
            let attempt = nextRetryAttempt(for: jobId)
            let delay = min(5 * pow(2, Double(attempt)), 300)
            if let job = pending.job(id: jobId), let base = URL(string: job.baseURL) {
                enqueuePoll(jobId: jobId, baseURL: base, delay: delay)
            }
        }
    }

    public func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: Error?
    ) {
        // Successful completions were handled in didFinishDownloadingTo;
        // cancellations are deliberate (cancelPolling / foreground won).
        guard let error, (error as NSError).code != NSURLErrorCancelled else { return }
        guard let jobId = Self.jobId(from: task.originalRequest?.url),
              let job = pending.job(id: jobId),
              let base = URL(string: job.baseURL)
        else { return }

        // Network drop mid-poll — without this the poll chain died
        // silently until the next app launch. Retry with backoff.
        let attempt = nextRetryAttempt(for: jobId)
        let delay = min(5 * pow(2, Double(attempt)), 300)
        enqueuePoll(jobId: jobId, baseURL: base, delay: delay)
    }

    public func urlSessionDidFinishEvents(
        forBackgroundURLSession session: URLSession
    ) {
        // Drain the handler atomically so a racing setter can't fire it
        // twice or leave a stale one behind.
        stateLock.lock()
        let handler = _backgroundCompletionHandler
        _backgroundCompletionHandler = nil
        stateLock.unlock()
        guard let handler else { return }
        DispatchQueue.main.async(execute: handler)
    }
}
