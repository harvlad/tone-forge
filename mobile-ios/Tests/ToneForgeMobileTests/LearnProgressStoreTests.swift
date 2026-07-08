// LearnProgressStoreTests.swift
//
// Disk round-trip for Learn progress records (redesign Phase 8):
// injectable root, atomic save/load, corrupt files read as nil,
// delete is idempotent.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

final class LearnProgressStoreTests: XCTestCase {

    private var tmpDir: URL!
    private var store: LearnProgressStore!

    override func setUpWithError() throws {
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: tmpDir, withIntermediateDirectories: true)
        store = LearnProgressStore(root: tmpDir)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmpDir)
    }

    func testSaveLoadRoundTrip() throws {
        var progress = SongLearnProgress(analysisId: "song1")
        progress.recordPass(
            LearnPassResult(
                hits: 4, misses: 0, coverage: 1.0,
                accuracy: 1.0, isPassing: true
            ),
            sectionLabel: "verse"
        )
        progress.recordStreak(7)
        try store.save(progress)

        XCTAssertEqual(store.load(analysisId: "song1"), progress)
    }

    func testLoadMissingReturnsNil() {
        XCTAssertNil(store.load(analysisId: "nope"))
    }

    func testLoadCorruptReturnsNil() throws {
        let url = try store.jsonURL(analysisId: "bad")
        try Data("not json{".utf8).write(to: url)
        XCTAssertNil(store.load(analysisId: "bad"))
    }

    func testDeleteRemovesRecord() throws {
        let progress = SongLearnProgress(analysisId: "song1")
        try store.save(progress)
        XCTAssertNotNil(store.load(analysisId: "song1"))

        try store.delete(analysisId: "song1")
        XCTAssertNil(store.load(analysisId: "song1"))

        // Idempotent on already-missing files.
        XCTAssertNoThrow(try store.delete(analysisId: "song1"))
    }

    func testRecordsAreIsolatedPerSong() throws {
        var a = SongLearnProgress(analysisId: "a")
        a.recordStreak(3)
        var b = SongLearnProgress(analysisId: "b")
        b.recordStreak(9)
        try store.save(a)
        try store.save(b)

        XCTAssertEqual(store.load(analysisId: "a")?.longestStreak, 3)
        XCTAssertEqual(store.load(analysisId: "b")?.longestStreak, 9)
    }
}
