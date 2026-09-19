// SongsModelTests.swift
//
// The unified Songs page: SourceTrack/SearchPage decode (snake_case,
// optionals, forward-compat enum fallbacks), the pure union merge
// (collapse a completing job into its history row; instant processing
// rows; metadata-filter gating), and SongsModel fetch/paging/facet
// plumbing against a recording stub — no network.

import XCTest
@testable import JamDesktopCore

// MARK: - Stub client (records the last search args)

private final class StubLibraryClient: LibrarySearching, @unchecked Sendable {
    var pages: [SearchPage]
    var pageIndex = 0
    var lastSource: SourceId?
    var lastQuery: String?
    var lastFilters: SongsFilters?
    var lastSort: SongsSort?
    var lastCursor: String??
    var ingestResult = IngestResult(historyId: "reused-1")
    var lastIngest: (SourceId, String)?

    init(pages: [SearchPage]) { self.pages = pages }

    func search(
        baseURL: URL, source: SourceId, query: String?, filters: SongsFilters,
        sort: SongsSort, cursor: String?, limit: Int
    ) async throws -> SearchPage {
        lastSource = source
        lastQuery = query
        lastFilters = filters
        lastSort = sort
        lastCursor = .some(cursor)
        let page = pages[min(pageIndex, pages.count - 1)]
        pageIndex += 1
        return page
    }

    func ingest(baseURL: URL, source: SourceId, sourceRef: String) async throws -> IngestResult {
        lastIngest = (source, sourceRef)
        return ingestResult
    }
}

final class SourceTrackDecodeTests: XCTestCase {

    func testDecodesSnakeCaseAndOptionals() throws {
        let json = """
        {"source":"library","source_ref":"h1","title":"Tune","artist":"Jane",
         "key":"A minor","tempo_bpm":120.5,"duration_s":184.2,"genre":"blues",
         "mood":"mellow","tags":["12-bar","guitar"],"license":"CC BY 4.0",
         "license_url":"https://x/l","attribution":"Jane","source_url":"https://x",
         "status":"done","progress":100,"history_id":"h1","artwork_ref":"art1"}
        """.data(using: .utf8)!
        let t = try JSONDecoder().decode(SourceTrack.self, from: json)
        XCTAssertEqual(t.source, .library)
        XCTAssertEqual(t.sourceRef, "h1")
        XCTAssertEqual(t.tempoBpm, 120.5)
        XCTAssertEqual(t.durationS, 184.2)
        XCTAssertEqual(t.tags, ["12-bar", "guitar"])
        XCTAssertEqual(t.licenseUrl, "https://x/l")
        XCTAssertEqual(t.status, .done)
        XCTAssertEqual(t.historyId, "h1")
        XCTAssertEqual(t.mergeKey, "h1")
    }

    func testLeanRowDecodesWithMissingFields() throws {
        // A bare in-flight job row: no metadata, just lifecycle.
        let json = """
        {"source":"library","source_ref":"job-9","status":"running","progress":42}
        """.data(using: .utf8)!
        let t = try JSONDecoder().decode(SourceTrack.self, from: json)
        XCTAssertNil(t.title)
        XCTAssertEqual(t.tags, [])
        XCTAssertEqual(t.status, .running)
        XCTAssertEqual(t.mergeKey, "job-9")
    }

    func testUnknownEnumsFallBackNotThrow() throws {
        let json = """
        {"source":"soundcloud","source_ref":"x","status":"transcoding"}
        """.data(using: .utf8)!
        let t = try JSONDecoder().decode(SourceTrack.self, from: json)
        XCTAssertEqual(t.source, .unknown)
        XCTAssertEqual(t.status, .unknown)
    }

    func testProgressFractionNormalizesPercentAndFraction() throws {
        func frac(_ p: Double) throws -> Double? {
            let json = "{\"source\":\"library\",\"source_ref\":\"x\",\"status\":\"running\",\"progress\":\(p)}"
            return try JSONDecoder().decode(SourceTrack.self, from: json.data(using: .utf8)!).progressFraction
        }
        XCTAssertEqual(try frac(50), 0.5)      // percent
        XCTAssertEqual(try frac(0.5), 0.5)     // fraction
        XCTAssertEqual(try frac(100), 1.0)
    }

    func testSearchPageDecodesFacetsAndCursor() throws {
        let json = """
        {"source":"library","tracks":[{"source":"library","source_ref":"h1","status":"done"}],
         "facets":{"genre":[{"value":"blues","count":3},{"value":"rock","count":1,"label":"Rock"}],
                   "status":[{"value":"done","count":4}]},
         "next_cursor":"opaque-2","total":9}
        """.data(using: .utf8)!
        let page = try JSONDecoder().decode(SearchPage.self, from: json)
        XCTAssertEqual(page.tracks.count, 1)
        XCTAssertEqual(page.facets["genre"]?.count, 2)
        XCTAssertEqual(page.facets["genre"]?.first?.displayLabel, "blues")
        XCTAssertEqual(page.facets["genre"]?.last?.displayLabel, "Rock")
        XCTAssertEqual(page.nextCursor, "opaque-2")
        XCTAssertEqual(page.total, 9)
    }
}

@MainActor
final class SongsModelMergeTests: XCTestCase {

    private let base = URL(string: "http://127.0.0.1:8000")!

    private func serverTrack(_ ref: String, status: TrackStatus = .done,
                             historyId: String? = nil, genre: String? = nil) -> SourceTrack {
        SourceTrack(source: .library, sourceRef: ref, title: ref,
                    genre: genre, status: status, historyId: historyId ?? ref)
    }

    private func liveItem(id: String, jobId: String?, status: QueueItemStatus,
                          title: String = "Live") -> AnalysisQueueItem {
        AnalysisQueueItem(id: id, jobId: jobId, title: title, kind: .upload, status: status)
    }

    func testActiveLiveOverlaysMatchingServerRow() {
        // Server surfaced the job as a running row (history_id == job key).
        let server = [serverTrack("job-1", status: .running)]
        let live = [liveItem(id: "u1", jobId: "job-1",
                             status: .running(message: "Separating", percent: 80))]
        let merged = SongsModel.merged(server: server, live: live, filters: SongsFilters())
        XCTAssertEqual(merged.count, 1, "no duplicate row — the live item collapses onto the server row")
        XCTAssertEqual(merged[0].status, .running)
        XCTAssertEqual(merged[0].progressFraction, 0.8)
    }

    func testUnmatchedActiveLivePrepended() {
        let server = [serverTrack("h-old")]
        let live = [liveItem(id: "u2", jobId: "job-2",
                             status: .running(message: "Upload", percent: 10), title: "Fresh")]
        let merged = SongsModel.merged(server: server, live: live, filters: SongsFilters())
        XCTAssertEqual(merged.count, 2)
        XCTAssertEqual(merged.first?.title, "Fresh", "brand-new upload appears instantly, newest-first")
        XCTAssertEqual(merged.first?.status, .running)
    }

    func testCompletedJobCollapsesIntoHistoryRow() {
        // The job finished; server now has the done history row keyed on
        // the same history id the live .done item carries. No synthetic row.
        let server = [serverTrack("hist-7", status: .done, historyId: "hist-7")]
        let live = [liveItem(id: "u3", jobId: "job-7", status: .done(historyId: "hist-7"))]
        let merged = SongsModel.merged(server: server, live: live, filters: SongsFilters())
        XCTAssertEqual(merged.map(\.mergeKey), ["hist-7"])
        XCTAssertEqual(merged[0].status, .done)
    }

    func testMetadataFilterHidesSyntheticProcessingRow() {
        let server = [serverTrack("h1", genre: "blues")]
        let live = [liveItem(id: "u4", jobId: "job-4",
                             status: .running(message: "x", percent: 5))]
        var filters = SongsFilters()
        filters.genre = "blues"
        let merged = SongsModel.merged(server: server, live: live, filters: filters)
        // Live processing row has no genre — must not resurface under a
        // genre filter (it'd be filtered out server-side too).
        XCTAssertEqual(merged.count, 1)
        XCTAssertEqual(merged[0].sourceRef, "h1")
    }

    func testDoneStatusFilterExcludesLiveProcessing() {
        let server = [serverTrack("h1", status: .done)]
        let live = [liveItem(id: "u5", jobId: "job-5",
                             status: .running(message: "x", percent: 5))]
        var filters = SongsFilters()
        filters.status = "done"
        let merged = SongsModel.merged(server: server, live: live, filters: filters)
        XCTAssertEqual(merged.count, 1)
        XCTAssertEqual(merged[0].status, .done)
    }

    func testProcessingCountDedupesAcrossServerAndLive() async {
        let stub = StubLibraryClient(pages: [SearchPage(
            tracks: [serverTrack("job-1", status: .running)])])
        let model = SongsModel(client: stub)
        await model.reload(baseURL: base)
        // Same job present in both the server union and the live queue.
        model.liveItems = [
            liveItem(id: "u1", jobId: "job-1", status: .running(message: "x", percent: 50)),
            liveItem(id: "u2", jobId: "job-2", status: .queued(position: 1)),
        ]
        XCTAssertEqual(model.processingCount, 2, "job-1 counted once, job-2 adds one")
    }
}

@MainActor
final class SongsModelFetchTests: XCTestCase {

    private let base = URL(string: "http://127.0.0.1:8000")!

    func testReloadPopulatesAndPassesArgs() async {
        let page = SearchPage(
            tracks: [SourceTrack(source: .library, sourceRef: "h1", title: "A")],
            facets: ["genre": [FacetBucket(value: "blues", count: 1)]],
            nextCursor: "cur-1", total: 1)
        let stub = StubLibraryClient(pages: [page])
        let model = SongsModel(client: stub)
        model.query = "  blues  "
        await model.reload(baseURL: base)
        XCTAssertEqual(model.serverTracks.map(\.sourceRef), ["h1"])
        XCTAssertEqual(model.facets["genre"]?.first?.value, "blues")
        XCTAssertEqual(model.nextCursor, "cur-1")
        XCTAssertTrue(model.hasMore)
        XCTAssertEqual(stub.lastQuery, "blues", "query trimmed before send")
        XCTAssertEqual(stub.lastCursor, .some(nil), "first page sends no cursor")
        XCTAssertEqual(stub.lastSort, .recent)
    }

    func testLoadMoreAppendsViaCursorAndDedupes() async {
        let page1 = SearchPage(
            tracks: [SourceTrack(source: .library, sourceRef: "h1")],
            nextCursor: "cur-1")
        let page2 = SearchPage(
            tracks: [
                SourceTrack(source: .library, sourceRef: "h1"), // overlap
                SourceTrack(source: .library, sourceRef: "h2"),
            ],
            nextCursor: nil)
        let stub = StubLibraryClient(pages: [page1, page2])
        let model = SongsModel(client: stub)
        await model.reload(baseURL: base)
        await model.loadMore(baseURL: base)
        XCTAssertEqual(model.serverTracks.map(\.sourceRef), ["h1", "h2"],
                       "overlapping row not duplicated")
        XCTAssertEqual(stub.lastCursor, .some("cur-1"), "loadMore echoes the opaque cursor")
        XCTAssertFalse(model.hasMore)
    }

    func testToggleFacetSetsThenClears() async {
        let stub = StubLibraryClient(pages: [SearchPage(tracks: [])])
        let model = SongsModel(client: stub)
        model.toggleFacet(field: "genre", value: "rock", baseURL: base)
        XCTAssertEqual(model.filters.genre, "rock")
        XCTAssertTrue(model.isFacetSelected(field: "genre", value: "rock"))
        model.toggleFacet(field: "genre", value: "rock", baseURL: base)
        XCTAssertNil(model.filters.genre, "second toggle clears the facet")
    }

    func testToggleTagsAccumulate() {
        let stub = StubLibraryClient(pages: [SearchPage(tracks: [])])
        let model = SongsModel(client: stub)
        model.toggleFacet(field: "tags", value: "clean", baseURL: base)
        model.toggleFacet(field: "tags", value: "12-bar", baseURL: base)
        XCTAssertEqual(model.filters.tags, ["clean", "12-bar"])
        model.toggleFacet(field: "tags", value: "clean", baseURL: base)
        XCTAssertEqual(model.filters.tags, ["12-bar"])
    }

    func testIngestReusesDedupeHit() async {
        let stub = StubLibraryClient(pages: [SearchPage(tracks: [])])
        stub.ingestResult = IngestResult(historyId: "reused-9")
        let model = SongsModel(client: stub)
        let track = SourceTrack(source: .library, sourceRef: "h9", status: .error, historyId: nil)
        let result = await model.ingest(baseURL: base, track: track)
        XCTAssertEqual(result?.historyId, "reused-9")
        XCTAssertEqual(stub.lastIngest?.0, .library)
        XCTAssertEqual(stub.lastIngest?.1, "h9")
    }
}
