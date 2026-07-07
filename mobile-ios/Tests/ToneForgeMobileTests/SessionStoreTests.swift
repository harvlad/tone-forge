// SessionStoreTests.swift
//
// Disk contract for Documents/sessions: save/load round trip,
// autosave-overwrite under one sessionId, newest-first listing that
// survives corrupt files, and delete.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

final class SessionStoreTests: XCTestCase {

    private var root: URL!
    private var store: SessionStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("session-store-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: true)
        store = SessionStore(root: root)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    private func makeSession(
        id: UUID = UUID(),
        // Round epoch — Date() carries sub-second fractions that are
        // not bit-stable through JSON double formatting.
        capturedAt: Date = Date(timeIntervalSince1970: 1_700_000_000)
    ) -> SessionCapture {
        SessionCapture(
            sessionId: id,
            songBackendId: "song-1",
            appMode: .sample,
            capturedAt: capturedAt,
            tempoBpm: 110,
            events: [
                ContributionEvent(
                    source: .touch, kind: .padDown(row: 1, col: 1),
                    timestamp: 0.5, hostTime: 0, velocity: 0.8)
            ],
            padMapping: [
                PadAddress(mode: .sample, pad: PadIndex(11)):
                    .localSample(id: id)
            ])
    }

    func testSaveLoadRoundTrip() throws {
        let session = makeSession()
        try store.save(session)
        let loaded = try store.load(sessionId: session.sessionId)
        XCTAssertEqual(loaded, session)
    }

    func testAutosaveOverwritesSameFile() throws {
        var session = makeSession()
        try store.save(session)
        session.events.append(ContributionEvent(
            source: .touch, kind: .padUp(row: 1, col: 1),
            timestamp: 1.0, hostTime: 0))
        try store.save(session)
        XCTAssertEqual(store.list().count, 1,
                       "same sessionId must land in one file")
        XCTAssertEqual(
            try store.load(sessionId: session.sessionId).events.count, 2)
    }

    func testListIsNewestFirstAndSkipsCorruptFiles() throws {
        let old = makeSession(
            capturedAt: Date(timeIntervalSince1970: 1_000))
        let new = makeSession(
            capturedAt: Date(timeIntervalSince1970: 2_000))
        try store.save(old)
        try store.save(new)
        // A corrupt file must not hide the valid ones.
        try Data("not json".utf8).write(
            to: store.sessionsDir()
                .appendingPathComponent("\(UUID().uuidString).json"))
        let listed = store.list()
        XCTAssertEqual(listed.map(\.sessionId),
                       [new.sessionId, old.sessionId])
    }

    func testDeleteRemovesFile() throws {
        let session = makeSession()
        try store.save(session)
        try store.delete(sessionId: session.sessionId)
        XCTAssertTrue(store.list().isEmpty)
        XCTAssertThrowsError(
            try store.load(sessionId: session.sessionId))
        // Deleting again is harmless.
        XCTAssertNoThrow(
            try store.delete(sessionId: session.sessionId))
    }
}
