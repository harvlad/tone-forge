// SessionStoreTests.swift
//
// Desktop SessionStore against a temp-dir root: round-trip through
// the frozen SessionCapture v1 wire format, newest-first listing,
// corrupt-file tolerance and deletion.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

final class SessionStoreTests: XCTestCase {

    private var root: URL!
    private var store: SessionStore!

    override func setUp() async throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        store = SessionStore(root: root)
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: root)
    }

    private func capture(
        capturedAt: Date = Date(), events: [ContributionEvent] = []
    ) -> SessionCapture {
        SessionCapture(
            sessionId: UUID(),
            songBackendId: "song-1",
            appMode: .sample,
            capturedAt: capturedAt,
            tempoBpm: 120,
            events: events,
            padMapping: [:]
        )
    }

    func testSaveLoadRoundTrip() throws {
        let take = capture(events: [
            ContributionEvent(
                source: .launchpad, kind: .padDown(row: 8, col: 1),
                timestamp: 1.5, hostTime: 0),
            ContributionEvent(
                source: .launchpad, kind: .padUp(row: 8, col: 1),
                timestamp: 2.0, hostTime: 0),
        ])
        try store.save(take)
        let loaded = try store.load(sessionId: take.sessionId)
        XCTAssertEqual(loaded.sessionId, take.sessionId)
        XCTAssertEqual(loaded.songBackendId, "song-1")
        XCTAssertEqual(loaded.events.count, 2)
        XCTAssertEqual(loaded.events[0].timestamp, 1.5)
        XCTAssertTrue(loaded.padMapping.isEmpty)
    }

    func testListNewestFirst() throws {
        let old = capture(capturedAt: Date(timeIntervalSince1970: 100))
        let new = capture(capturedAt: Date(timeIntervalSince1970: 200))
        try store.save(old)
        try store.save(new)
        let listed = store.list()
        XCTAssertEqual(
            listed.map(\.sessionId), [new.sessionId, old.sessionId])
    }

    func testListSkipsCorruptFiles() throws {
        let good = capture()
        try store.save(good)
        let bad = try store.sessionsDir()
            .appendingPathComponent("\(UUID().uuidString).json")
        try Data("not json".utf8).write(to: bad)
        let listed = store.list()
        XCTAssertEqual(listed.map(\.sessionId), [good.sessionId])
    }

    func testDeleteRemovesFile() throws {
        let take = capture()
        try store.save(take)
        try store.delete(sessionId: take.sessionId)
        XCTAssertTrue(store.list().isEmpty)
        // Deleting again is a no-op, not an error.
        XCTAssertNoThrow(try store.delete(sessionId: take.sessionId))
    }
}
