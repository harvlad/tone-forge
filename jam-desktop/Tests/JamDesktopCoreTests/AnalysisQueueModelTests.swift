// AnalysisQueueModelTests.swift
//
// Queue-model tests with stubbed clients: all three enqueue flows,
// concurrent enqueues (no busy guard), progress clamping, stream-drop
// snapshot fallback + reattach, restart recovery via refreshFromServer,
// dismiss, and activeCount.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

// MARK: - Stubs

private struct StubUpload: UploadSubmitting {
    let result: UploadStart
    func submitUpload(baseURL: URL, fileURL: URL, filename: String) async throws -> UploadStart {
        result
    }
}

private struct FailingUpload: UploadSubmitting {
    struct Boom: Error, LocalizedError {
        var errorDescription: String? { "upload exploded" }
    }
    func submitUpload(baseURL: URL, fileURL: URL, filename: String) async throws -> UploadStart {
        throw Boom()
    }
}

private struct StubURLAnalyze: URLAnalyzeStreaming {
    let events: [AnalyzeEvent]
    let terminalError: Error?
    /// When true the stream yields its events and then stays open
    /// (mid-flight snapshot tests; callers must dismiss to cancel).
    let holdOpen: Bool

    init(events: [AnalyzeEvent], terminalError: Error? = nil, holdOpen: Bool = false) {
        self.events = events
        self.terminalError = terminalError
        self.holdOpen = holdOpen
    }

    func analyze(baseURL: URL, sourceUrl: String)
        -> AsyncThrowingStream<AnalyzeEvent, Error>
    {
        let (events, terminalError, holdOpen) = (events, terminalError, holdOpen)
        return AsyncThrowingStream { continuation in
            for event in events { continuation.yield(event) }
            if !holdOpen { continuation.finish(throwing: terminalError) }
        }
    }
}

/// Job event streams served in order, one script per `events` call —
/// lets a test drop the first connection and succeed on reattach.
private final class ScriptedJobs: JobSubmitting, @unchecked Sendable {
    enum Script {
        case events([AnalyzeEvent])          // yield then finish cleanly
        case failure(Error)                  // finish throwing
        case holdOpen([AnalyzeEvent])        // yield, never finish
    }

    private let lock = NSLock()
    private var scripts: [Script]

    init(_ scripts: [Script]) {
        self.scripts = scripts
    }

    func submit(
        baseURL: URL, wavFileURL: URL, filename: String,
        extraFields: [(name: String, value: String)]
    ) async throws -> String {
        XCTFail("submit should not be called by the desktop queue")
        return "unused"
    }

    func events(baseURL: URL, jobId: String) -> AsyncThrowingStream<AnalyzeEvent, Error> {
        lock.lock()
        let script = scripts.isEmpty
            ? Script.failure(JobClientError.streamEndedWithoutResult)
            : scripts.removeFirst()
        lock.unlock()
        return AsyncThrowingStream { continuation in
            switch script {
            case let .events(events):
                for event in events { continuation.yield(event) }
                continuation.finish()
            case let .failure(error):
                continuation.finish(throwing: error)
            case let .holdOpen(events):
                for event in events { continuation.yield(event) }
            }
        }
    }
}

private struct StubCC: CCTrackProviding {
    let jobId: String
    func fetchCatalog(baseURL: URL) async throws -> [CCTrack] { [] }
    func startImport(baseURL: URL, trackId: String) async throws -> String { jobId }
}

private final class StubJobsList: JobsListing, @unchecked Sendable {
    var listRows: [JobListRow] = []
    var listError: Error?
    /// jobId -> snapshot; unknown ids 404 like the real backend.
    var fetchResults: [String: JobListRow] = [:]

    func listJobs(baseURL: URL, limit: Int) async throws -> [JobListRow] {
        if let listError { throw listError }
        return listRows
    }

    func fetchJob(baseURL: URL, jobId: String) async throws -> JobListRow {
        if let row = fetchResults[jobId] { return row }
        throw JobsListError.badStatus(404)
    }
}

// MARK: - Tests

@MainActor
final class AnalysisQueueModelTests: XCTestCase {

    private let base = URL(string: "http://127.0.0.1:8000")!
    private let wav = URL(fileURLWithPath: "/tmp/x.wav")

    private func makeQueue(
        upload: UploadSubmitting = StubUpload(
            result: UploadStart(jobId: "j1", engineOnline: true)),
        url: URLAnalyzeStreaming = StubURLAnalyze(events: []),
        jobs: JobSubmitting = ScriptedJobs([.events([.completed(historyId: "h")])]),
        cc: CCTrackProviding = StubCC(jobId: "j1"),
        list: JobsListing = StubJobsList()
    ) -> AnalysisQueueModel {
        let queue = AnalysisQueueModel(
            uploadClient: upload, urlClient: url, jobClient: jobs,
            ccClient: cc, listClient: list
        )
        queue.reattachDelay = .milliseconds(1)
        return queue
    }

    // MARK: URL flow

    func testURLEnqueueReachesDone() async {
        let queue = makeQueue(url: StubURLAnalyze(events: [
            .progress(message: "Downloading audio", percent: 10),
            .completed(historyId: "hist-1"),
        ]))
        queue.enqueueURL(baseURL: base, sourceUrl: "https://example.com/song")
        XCTAssertEqual(queue.items.count, 1)
        XCTAssertEqual(queue.items[0].kind, .url)
        await queue.awaitAll()
        XCTAssertEqual(queue.items[0].status, .done(historyId: "hist-1"))
    }

    func testURLServerErrorBecomesErrorRow() async {
        let queue = makeQueue(url: StubURLAnalyze(
            events: [],
            terminalError: URLAnalyzeClient.URLAnalyzeError.serverError("Download failed")
        ))
        queue.enqueueURL(baseURL: base, sourceUrl: "https://example.com/song")
        await queue.awaitAll()
        XCTAssertEqual(queue.items[0].status, .error("Download failed"))
    }

    func testURLStreamEndingWithoutResultErrors() async {
        let queue = makeQueue(url: StubURLAnalyze(events: [
            .progress(message: "Working", percent: 50),
        ]))
        queue.enqueueURL(baseURL: base, sourceUrl: "https://example.com/song")
        await queue.awaitAll()
        XCTAssertEqual(queue.items[0].status, .error("Analysis ended unexpectedly"))
    }

    func testEmptyURLIsIgnored() {
        let queue = makeQueue()
        queue.enqueueURL(baseURL: base, sourceUrl: "   ")
        XCTAssertTrue(queue.items.isEmpty)
    }

    // MARK: Upload flow

    func testUploadWithoutAttestationBecomesErrorRow() async {
        let queue = makeQueue()
        queue.enqueueUpload(baseURL: base, fileURL: wav, filename: "x.wav", attested: false)
        XCTAssertEqual(queue.items.count, 1)
        guard case .error = queue.items[0].status else {
            return XCTFail("expected error row, got \(queue.items[0].status)")
        }
        // No watcher was started; nothing to await.
        await queue.awaitAll()
        XCTAssertEqual(queue.activeCount, 0)
    }

    func testUploadFollowsJobToDone() async {
        let queue = makeQueue(jobs: ScriptedJobs([
            .events([
                .progress(message: "Analyzing tone", percent: 80),
                .completed(historyId: "hist-9"),
            ]),
        ]))
        queue.enqueueUpload(baseURL: base, fileURL: wav, filename: "x.wav", attested: true)
        XCTAssertEqual(queue.items[0].status, .queued(position: nil))
        await queue.awaitAll()
        XCTAssertEqual(queue.items[0].jobId, "j1")
        XCTAssertEqual(queue.items[0].status, .done(historyId: "hist-9"))
    }

    func testUploadSubmitFailureBecomesErrorRow() async {
        let queue = makeQueue(upload: FailingUpload())
        queue.enqueueUpload(baseURL: base, fileURL: wav, filename: "x.wav", attested: true)
        await queue.awaitAll()
        XCTAssertEqual(queue.items[0].status, .error("upload exploded"))
    }

    // MARK: Demo flow

    func testDemoImportFollowsJobToDone() async {
        let queue = makeQueue(
            jobs: ScriptedJobs([.events([.completed(historyId: "hist-cc")])]),
            cc: StubCC(jobId: "job-cc")
        )
        queue.enqueueDemoImport(baseURL: base, trackId: "track-1", title: "Demo Song")
        await queue.awaitAll()
        XCTAssertEqual(queue.items[0].title, "Demo Song")
        XCTAssertEqual(queue.items[0].jobId, "job-cc")
        XCTAssertEqual(queue.items[0].status, .done(historyId: "hist-cc"))
    }

    // MARK: No busy guard

    func testConcurrentEnqueuesAreBothTracked() async {
        let queue = makeQueue(jobs: ScriptedJobs([
            .events([.completed(historyId: "h1")]),
            .events([.completed(historyId: "h2")]),
        ]))
        queue.enqueueDemoImport(baseURL: base, trackId: "t1", title: "One")
        queue.enqueueDemoImport(baseURL: base, trackId: "t2", title: "Two")
        XCTAssertEqual(queue.items.count, 2)
        await queue.awaitAll()
        let historyIds = queue.items.compactMap { item -> String? in
            if case let .done(historyId) = item.status { return historyId }
            return nil
        }
        XCTAssertEqual(Set(historyIds), ["h1", "h2"])
    }

    // MARK: Progress semantics

    func testProgressClampsPercentAndKeepsLastMessage() async {
        // Upper clamp + message retention: 150 -> 100, "" keeps "Starting".
        let queue = makeQueue(url: StubURLAnalyze(events: [
            .progress(message: "Starting", percent: 0),
            .progress(message: "", percent: 150),
        ], holdOpen: true))
        queue.enqueueURL(baseURL: base, sourceUrl: "https://example.com/song")
        for _ in 0..<1000 {
            if case let .running(message, percent) = queue.items[0].status,
               message == "Starting", percent == 100 { break }
            await Task.yield()
        }
        guard case let .running(message, percent) = queue.items[0].status else {
            return XCTFail("expected running row")
        }
        XCTAssertEqual(message, "Starting")
        XCTAssertEqual(percent, 100)
        queue.dismiss(id: queue.items[0].id)

        // Lower clamp: 0 -> 2.
        let queue2 = makeQueue(url: StubURLAnalyze(events: [
            .progress(message: "Starting", percent: 0),
        ], holdOpen: true))
        queue2.enqueueURL(baseURL: base, sourceUrl: "https://example.com/song")
        for _ in 0..<1000 {
            if case let .running(_, percent) = queue2.items[0].status,
               percent == 2 { break }
            await Task.yield()
        }
        guard case let .running(_, percent2) = queue2.items[0].status else {
            return XCTFail("expected running row")
        }
        XCTAssertEqual(percent2, 2)
        queue2.dismiss(id: queue2.items[0].id)
    }

    // MARK: Stream drop handling

    func testStreamDropFallsBackToSnapshot() async {
        let list = StubJobsList()
        list.fetchResults["j1"] = JobListRow(
            jobId: "j1", status: "done", historyId: "h-snap")
        let queue = makeQueue(
            jobs: ScriptedJobs([.failure(JobClientError.badStatus(500))]),
            list: list
        )
        queue.enqueueUpload(baseURL: base, fileURL: wav, filename: "x.wav", attested: true)
        await queue.awaitAll()
        XCTAssertEqual(queue.items[0].status, .done(historyId: "h-snap"))
    }

    func testStreamDropReattachesWhileJobStillActive() async {
        let list = StubJobsList()
        list.fetchResults["j1"] = JobListRow(
            jobId: "j1", status: "running", percent: 50, message: "Half")
        let queue = makeQueue(
            jobs: ScriptedJobs([
                .failure(JobClientError.badStatus(500)),
                .events([.completed(historyId: "h-retry")]),
            ]),
            list: list
        )
        queue.enqueueUpload(baseURL: base, fileURL: wav, filename: "x.wav", attested: true)
        await queue.awaitAll()
        XCTAssertEqual(queue.items[0].status, .done(historyId: "h-retry"))
    }

    func testServerReportedJobErrorIsTerminal() async {
        let queue = makeQueue(jobs: ScriptedJobs([
            .failure(JobClientError.serverError("Separation crashed")),
        ]))
        queue.enqueueUpload(baseURL: base, fileURL: wav, filename: "x.wav", attested: true)
        await queue.awaitAll()
        XCTAssertEqual(queue.items[0].status, .error("Separation crashed"))
    }

    func testVanishedJobBecomesError() async {
        // Stream drops and the snapshot 404s (job swept server-side).
        let queue = makeQueue(jobs: ScriptedJobs([
            .failure(JobClientError.badStatus(500)),
        ]))
        queue.enqueueUpload(baseURL: base, fileURL: wav, filename: "x.wav", attested: true)
        await queue.awaitAll()
        XCTAssertEqual(
            queue.items[0].status,
            .error("This analysis is no longer available."))
    }

    // MARK: Restart recovery

    func testRefreshRestoresUnknownJobs() async {
        let list = StubJobsList()
        list.listRows = [
            JobListRow(jobId: "run-1", status: "running", percent: 40,
                       message: "Separating", filename: "live.wav", createdAt: 200),
            JobListRow(jobId: "done-1", status: "done", historyId: "h-done",
                       filename: "old.wav", createdAt: 100),
        ]
        let queue = makeQueue(
            jobs: ScriptedJobs([.events([.completed(historyId: "h-live")])]),
            list: list
        )
        await queue.refreshFromServer(baseURL: base)
        XCTAssertEqual(queue.items.count, 2)
        XCTAssertEqual(queue.items[0].jobId, "run-1")  // newest first
        XCTAssertEqual(queue.items[0].kind, .restored)
        XCTAssertEqual(queue.items[0].title, "live.wav")
        XCTAssertEqual(queue.items[1].status, .done(historyId: "h-done"))
        // The active restored row got a watcher that runs to done.
        await queue.awaitAll()
        XCTAssertEqual(queue.items[0].status, .done(historyId: "h-live"))
    }

    func testRefreshDeDupesByJobIdAndUpdatesQueuePosition() async {
        let list = StubJobsList()
        list.listRows = [
            JobListRow(jobId: "j1", status: "queued", filename: "x.wav",
                       queuePosition: 3),
        ]
        // Hold the job stream open so the local row keeps its watcher
        // and stays queued while we refresh.
        let queue = makeQueue(jobs: ScriptedJobs([.holdOpen([])]), list: list)
        queue.enqueueUpload(baseURL: base, fileURL: wav, filename: "x.wav", attested: true)
        for _ in 0..<1000 {
            if queue.items[0].jobId != nil { break }
            await Task.yield()
        }
        XCTAssertEqual(queue.items[0].jobId, "j1")
        await queue.refreshFromServer(baseURL: base)
        XCTAssertEqual(queue.items.count, 1)  // no duplicate row
        XCTAssertEqual(queue.items[0].status, .queued(position: 3))
        queue.dismiss(id: queue.items[0].id)
    }

    func testRefreshFailureIsSwallowedIntoRefreshError() async {
        let list = StubJobsList()
        list.listError = JobsListError.badStatus(500)
        let queue = makeQueue(list: list)
        await queue.refreshFromServer(baseURL: base)
        XCTAssertNotNil(queue.refreshError)
        XCTAssertTrue(queue.items.isEmpty)
    }

    // MARK: Row actions

    func testDismissRemovesRowAndCancelsWatcher() async {
        let queue = makeQueue(url: StubURLAnalyze(events: [
            .progress(message: "Working", percent: 10),
        ], holdOpen: true))
        queue.enqueueURL(baseURL: base, sourceUrl: "https://example.com/song")
        XCTAssertEqual(queue.items.count, 1)
        queue.dismiss(id: queue.items[0].id)
        XCTAssertTrue(queue.items.isEmpty)
        await queue.awaitAll()
    }

    func testClearFinishedKeepsActiveRows() async {
        let queue = makeQueue(url: StubURLAnalyze(events: [
            .progress(message: "Working", percent: 10),
        ], holdOpen: true))
        queue.enqueueUpload(baseURL: base, fileURL: wav, filename: "x.wav", attested: false)
        queue.enqueueURL(baseURL: base, sourceUrl: "https://example.com/song")
        XCTAssertEqual(queue.items.count, 2)
        XCTAssertEqual(queue.activeCount, 1)
        queue.clearFinished()
        XCTAssertEqual(queue.items.count, 1)
        XCTAssertEqual(queue.items[0].kind, .url)
        queue.dismiss(id: queue.items[0].id)
    }
}
