// DebugInspectorModelTests.swift

import XCTest
@testable import JamDesktopCore

private final class StubDebugClient: DebugFetching, @unchecked Sendable {
    var sessions: [DebugSessionSummary] = []
    var bundles: [String: DebugBundle] = [:]
    var corpus = DebugCorpus(songs: [])
    var history: [DebugHistoryRow] = []
    var errorToThrow: Error?
    var bundleFetchIds: [String] = []

    func fetchSessions(baseURL: URL) async throws -> [DebugSessionSummary] {
        if let errorToThrow { throw errorToThrow }
        return sessions
    }

    func fetchBundle(baseURL: URL, id: String) async throws -> DebugBundle {
        bundleFetchIds.append(id)
        if let errorToThrow { throw errorToThrow }
        guard let bundle = bundles[id] else {
            throw URLError(.badServerResponse)
        }
        return bundle
    }

    func fetchCorpus(baseURL: URL) async throws -> DebugCorpus {
        if let errorToThrow { throw errorToThrow }
        return corpus
    }

    func fetchHistory(baseURL: URL, limit: Int) async throws -> [DebugHistoryRow] {
        if let errorToThrow { throw errorToThrow }
        return history
    }
}

@MainActor
final class DebugInspectorModelTests: XCTestCase {

    private let base = URL(string: "http://127.0.0.1:8000")!

    private func quickBundle() -> DebugBundle {
        DebugBundle(understanding: DebugUnderstanding(
            sections: [
                DebugSection(startS: 0, endS: 2),   // quick
                DebugSection(startS: 2, endS: 10),  // none
            ],
            chords: [DebugChord(startS: 0, endS: 1, symbol: "F")]))  // barre in s0
    }

    func testLoadSessionsPopulates() async {
        let stub = StubDebugClient()
        stub.sessions = [DebugSessionSummary(id: "s1", name: "One")]
        let model = DebugInspectorModel(client: stub)
        await model.loadSessions(baseURL: base)
        XCTAssertEqual(model.sessions.count, 1)
        XCTAssertNil(model.error)
        XCTAssertFalse(model.isLoading)
    }

    func testLoadSessionsError() async {
        let stub = StubDebugClient()
        stub.errorToThrow = URLError(.timedOut)
        let model = DebugInspectorModel(client: stub)
        await model.loadSessions(baseURL: base)
        XCTAssertNotNil(model.error)
    }

    func testLoadBundleComputesTagRowsAndResetsSelection() async {
        let stub = StubDebugClient()
        stub.bundles["s1"] = quickBundle()
        let model = DebugInspectorModel(client: stub)
        model.selectedSectionIndex = 5
        model.tagFilter = "colour"
        await model.loadBundle(baseURL: base, id: "s1")
        XCTAssertEqual(model.currentSessionId, "s1")
        XCTAssertEqual(model.tagRows.count, 2)
        XCTAssertNil(model.selectedSectionIndex)
        XCTAssertNil(model.tagFilter)
    }

    func testLoadBundleIgnoresEmptyId() async {
        let stub = StubDebugClient()
        let model = DebugInspectorModel(client: stub)
        await model.loadBundle(baseURL: base, id: "")
        XCTAssertTrue(stub.bundleFetchIds.isEmpty)
        XCTAssertNil(model.currentBundle)
    }

    func testTagCountsAndFilter() async {
        let stub = StubDebugClient()
        stub.bundles["s1"] = quickBundle()
        let model = DebugInspectorModel(client: stub)
        await model.loadBundle(baseURL: base, id: "s1")

        // Section 0: barre (F chord mid 0.5) + quick. Section 1: none.
        XCTAssertEqual(model.tagCounts["barre"], 1)
        XCTAssertEqual(model.tagCounts["quick"], 1)
        XCTAssertNil(model.tagCounts["colour"])

        // nil filter passes everything.
        XCTAssertTrue(model.sectionMatchesFilter(0))
        XCTAssertTrue(model.sectionMatchesFilter(1))

        model.tagFilter = "quick"
        XCTAssertTrue(model.sectionMatchesFilter(0))
        XCTAssertFalse(model.sectionMatchesFilter(1))
        XCTAssertFalse(model.sectionMatchesFilter(99))  // out of range
    }
}
