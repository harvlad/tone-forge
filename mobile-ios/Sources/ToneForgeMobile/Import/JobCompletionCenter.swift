// JobCompletionCenter.swift
//
// The single place a background-survivable analysis job "lands". It
// exists for the whole app lifetime (unlike the per-view
// ImportCoordinator), so a job that finishes while the Library view is
// gone — or after a background relaunch — still loads its bundle and
// notifies the user exactly once.
//
// Two completion paths converge here and are de-duped by job id:
//   * foreground: ImportCoordinator streamed /events, already loaded the
//     bundle -> foregroundCompleted(jobId:) suppresses the notification.
//   * background/relaunch: BackgroundAnalyzeSession polled /result out of
//     process -> jobFinished posts a local notification (if not active)
//     and defers opening until AppState is ready / app is foregrounded.

import Foundation
#if canImport(UIKit)
import UIKit
#endif

@MainActor
public final class JobCompletionCenter: ObservableObject {

    public static let shared = JobCompletionCenter()

    public weak var appState: AppState?

    private let pending = PendingJobStore()
    private let notifications = NotificationManager.shared
    private let background = BackgroundAnalyzeSession.shared

    /// Job ids already resolved, so foreground + background can't both
    /// fire completion. Bounded (FIFO eviction) so a long-lived session
    /// never grows it without limit.
    private var handled = Set<String>()
    private var handledOrder: [String] = []
    private let handledLimit = 256

    /// Finished history ids waiting for AppState / an active app before
    /// they open. Observed by the scene so a tap or foregrounding
    /// flushes. A queue (not a single slot) so two jobs finishing while
    /// backgrounded both surface instead of the first being overwritten.
    @Published public private(set) var pendingOpenHistoryIds: [String] = []

    private init() {}

    /// Wire callbacks and resume any orphaned jobs. Called once from
    /// AppState.bootAudio.
    public func boot(appState: AppState) {
        self.appState = appState
        background.onResult = { [weak self] jobId, historyId in
            Task { @MainActor in self?.jobFinished(jobId: jobId, historyId: historyId) }
        }
        background.onFailure = { [weak self] jobId in
            Task { @MainActor in self?.jobFailed(jobId: jobId) }
        }
        notifications.onOpenHistory = { [weak self] historyId in
            Task { @MainActor in self?.requestOpen(historyId: historyId) }
        }
        // Delegate only — the permission prompt is deferred to the
        // first job submission (register) so it appears in context.
        notifications.activate()
        background.resumePendingJobs()
        flushPendingOpen()
    }

    // MARK: - Registration (ImportCoordinator, at submit time)

    /// Persist a freshly submitted job and start the background safety
    /// net so it finishes + notifies even if the app is killed.
    public func register(
        jobId: String, title: String, baseURL: URL, artworkData: Data?
    ) {
        pending.add(PendingJob(
            jobId: jobId, title: title,
            baseURL: baseURL.absoluteString, artworkData: artworkData
        ))
        // First point the user actually benefits from notifications:
        // a job that may finish while the app is backgrounded.
        notifications.requestAuthorization()
        background.startPolling(jobId: jobId, baseURL: baseURL)
    }

    /// Foreground stream won the race: the coordinator already loaded the
    /// bundle. Mark handled, clear the safety net, no notification.
    public func foregroundCompleted(jobId: String) {
        _ = markHandled(jobId)
        finish(jobId: jobId)
    }

    // MARK: - Background / relaunch completion

    private func jobFinished(jobId: String, historyId: String) {
        guard markHandled(jobId) else { return }
        let job = pending.job(id: jobId)
        finish(jobId: jobId)

        if let art = job?.artworkData {
            _ = try? ArtworkStore().save(art, analysisId: historyId)
        }

        if appState != nil, Self.appIsActive {
            requestOpen(historyId: historyId)
        } else {
            notifications.postCompleted(
                historyId: historyId, title: job?.title ?? "Your song"
            )
            enqueueOpen(historyId)
        }
    }

    private func jobFailed(jobId: String) {
        guard markHandled(jobId) else { return }
        let job = pending.job(id: jobId)
        finish(jobId: jobId)
        if !Self.appIsActive {
            notifications.postFailed(title: job?.title ?? "Your song")
        }
    }

    // MARK: - Open routing

    /// Open a finished song now if AppState is ready, else queue it.
    public func requestOpen(historyId: String) {
        guard let appState else {
            enqueueOpen(historyId)
            return
        }
        pendingOpenHistoryIds.removeAll { $0 == historyId }
        appState.openFinishedSong(historyId: historyId)
    }

    /// Drain queued opens (called at boot and on app-active).
    public func flushPendingOpen() {
        while appState != nil, let historyId = pendingOpenHistoryIds.first {
            requestOpen(historyId: historyId)
        }
    }

    private func enqueueOpen(_ historyId: String) {
        guard !pendingOpenHistoryIds.contains(historyId) else { return }
        pendingOpenHistoryIds.append(historyId)
    }

    // MARK: - Helpers

    private func finish(jobId: String) {
        pending.remove(jobId: jobId)
        background.cancelPolling(jobId: jobId)
    }

    private func markHandled(_ jobId: String) -> Bool {
        if handled.contains(jobId) { return false }
        handled.insert(jobId)
        handledOrder.append(jobId)
        if handledOrder.count > handledLimit {
            handled.remove(handledOrder.removeFirst())
        }
        return true
    }

    private static var appIsActive: Bool {
        #if canImport(UIKit)
        return UIApplication.shared.applicationState == .active
        #else
        return true
        #endif
    }
}
