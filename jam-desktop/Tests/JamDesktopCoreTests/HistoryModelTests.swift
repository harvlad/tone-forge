// HistoryModelTests.swift
//
// HistoryModel with a stubbed fetcher: refresh, query passthrough,
// delete removal, error surfacing, and the D-024 artist/license
// decode via HistoryEntry.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

private final class StubHistory: HistoryFetching, @unchecked Sendable {
    var entries: [HistoryEntry]
    var lastQuery: String??
    var deleteError: Error?
    var deleted: [String] = []

    init(entries: [HistoryEntry]) {
        self.entries = entries
    }

    func fetch(baseURL: URL, query: String?, limit: Int) async throws -> [HistoryEntry] {
        lastQuery = query
        return entries
    }

    func delete(baseURL: URL, entryId: String) async throws {
        if let deleteError { throw deleteError }
        deleted.append(entryId)
    }
}

@MainActor
final class HistoryModelTests: XCTestCase {

    private let base = URL(string: "http://127.0.0.1:8000")!

    private func entry(_ id: String) -> HistoryEntry {
        HistoryEntry(id: id, timestamp: "2026-07-13T00:00:00Z", name: "Song \(id)")
    }

    func testRefreshPopulatesEntries() async {
        let stub = StubHistory(entries: [entry("a"), entry("b")])
        let model = HistoryModel(client: stub)
        await model.refresh(baseURL: base)
        XCTAssertEqual(model.entries.map(\.id), ["a", "b"])
        XCTAssertNil(model.error)
        XCTAssertFalse(model.isLoading)
    }

    func testEmptyQuerySentAsNil() async {
        let stub = StubHistory(entries: [])
        let model = HistoryModel(client: stub)
        model.query = "   "
        await model.refresh(baseURL: base)
        XCTAssertEqual(stub.lastQuery, .some(nil))

        model.query = "blues"
        await model.refresh(baseURL: base)
        XCTAssertEqual(stub.lastQuery, "blues")
    }

    func testDeleteRemovesEntryLocally() async {
        let stub = StubHistory(entries: [entry("a"), entry("b")])
        let model = HistoryModel(client: stub)
        await model.refresh(baseURL: base)
        await model.delete(baseURL: base, entryId: "a")
        XCTAssertEqual(stub.deleted, ["a"])
        XCTAssertEqual(model.entries.map(\.id), ["b"])
    }

    func testDeleteFailureKeepsEntry() async {
        let stub = StubHistory(entries: [entry("a")])
        stub.deleteError = URLError(.notConnectedToInternet)
        let model = HistoryModel(client: stub)
        await model.refresh(baseURL: base)
        await model.delete(baseURL: base, entryId: "a")
        XCTAssertEqual(model.entries.map(\.id), ["a"])
        XCTAssertNotNil(model.error)
    }

    func testHistoryEntryDecodesAttributionFields() throws {
        // D-024 wire shape: artist + license present for curated CC
        // tracks, absent for local uploads.
        let json = """
        {"id":"h1","timestamp":"2026-07-13T00:00:00Z","name":"Tune",
         "detected_type":"guitar","summary":"12-bar",
         "duration":184.2,"amp_family":"tweed",
         "artist":"Jane Doe","license":"CC BY 4.0"}
        """.data(using: .utf8)!
        let decoded = try JSONDecoder().decode(HistoryEntry.self, from: json)
        XCTAssertEqual(decoded.artist, "Jane Doe")
        XCTAssertEqual(decoded.license, "CC BY 4.0")
        XCTAssertEqual(decoded.detectedType, "guitar")
        XCTAssertEqual(decoded.ampFamily, "tweed")
    }
}
