// LearnProgressStoreTests.swift
//
// Persistence round-trip: save, load, delete; corrupt file returns nil.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class LearnProgressStoreTests: XCTestCase {

    private var tempDir: URL!
    private var store: LearnProgressStore!

    override func setUp() async throws {
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("progress-tests-\(UUID())", isDirectory: true)
        try FileManager.default.createDirectory(
            at: tempDir, withIntermediateDirectories: true)
        store = LearnProgressStore(root: tempDir)
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: tempDir)
    }

    func testSaveAndLoad() throws {
        var progress = SongLearnProgress(analysisId: "abc123")
        progress.longestStreak = 5
        progress.sections["verse"] = SectionProgress(
            learned: true, passCount: 3, bestAccuracy: 0.92)

        try store.save(progress)
        let loaded = store.load(analysisId: "abc123")
        XCTAssertNotNil(loaded)
        XCTAssertEqual(loaded?.longestStreak, 5)
        XCTAssertEqual(loaded?.sections["verse"]?.learned, true)
        XCTAssertEqual(loaded?.sections["verse"]?.passCount, 3)
    }

    func testLoadMissingReturnsNil() {
        XCTAssertNil(store.load(analysisId: "does-not-exist"))
    }

    func testDeleteRemovesFile() throws {
        var progress = SongLearnProgress(analysisId: "to-delete")
        progress.longestStreak = 1
        try store.save(progress)
        XCTAssertNotNil(store.load(analysisId: "to-delete"))

        store.delete(analysisId: "to-delete")
        XCTAssertNil(store.load(analysisId: "to-delete"))
    }

    func testCorruptFileReturnsNil() throws {
        let file = tempDir.appendingPathComponent("corrupt.json")
        try "{ not valid json".write(to: file, atomically: true, encoding: .utf8)
        XCTAssertNil(store.load(analysisId: "corrupt"))
    }

    func testOverwritesExisting() throws {
        var v1 = SongLearnProgress(analysisId: "overwrite")
        v1.longestStreak = 1
        try store.save(v1)

        var v2 = SongLearnProgress(analysisId: "overwrite")
        v2.longestStreak = 99
        try store.save(v2)

        let loaded = store.load(analysisId: "overwrite")
        XCTAssertEqual(loaded?.longestStreak, 99)
    }
}
