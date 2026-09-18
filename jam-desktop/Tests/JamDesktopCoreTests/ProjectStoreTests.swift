// ProjectStoreTests.swift
//
// Desktop ProjectStore: durable CRUD + the per-song working slot,
// against a temp-dir root. Fault-tolerance parity with SessionStore:
// corrupt files skipped, atomic writes, iOS-compatible JSON.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

final class ProjectStoreTests: XCTestCase {

    private var tempDir: URL!
    private var store: ProjectStore!

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        store = ProjectStore(root: tempDir)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempDir)
        super.tearDown()
    }

    private func project(
        name: String = "My jam", song: String? = "song1",
        updatedAt: Date = Date()
    ) -> Project {
        Project(
            name: name, updatedAt: updatedAt, baseSongId: song,
            baseSongTitle: "Title", snapshot: ProjectSnapshot()
        )
    }

    func testSaveLoadRoundTrip() throws {
        let p = project()
        try store.save(p)
        XCTAssertEqual(try store.load(projectId: p.id), p)
    }

    func testListNewestUpdatedFirstAndSkipsCorrupt() throws {
        let old = project(name: "Old", updatedAt: Date(timeIntervalSince1970: 100))
        let new = project(name: "New", updatedAt: Date(timeIntervalSince1970: 200))
        try store.save(old)
        try store.save(new)
        // Corrupt file must not hide the rest.
        try Data("not json".utf8).write(
            to: store.projectsDir().appendingPathComponent("junk.json"))
        let listed = store.list()
        XCTAssertEqual(listed.map(\.name), ["New", "Old"])
    }

    func testWorkingProjectsStayOutOfList() throws {
        let working = project(name: "Working")
        try store.saveWorking(working)
        XCTAssertTrue(store.list().isEmpty)
        XCTAssertEqual(store.loadWorking(analysisId: "song1")?.name, "Working")
        store.deleteWorking(analysisId: "song1")
        XCTAssertNil(store.loadWorking(analysisId: "song1"))
    }

    func testWorkingSaveOverwritesPerSong() throws {
        try store.saveWorking(project(name: "First"))
        try store.saveWorking(project(name: "Second"))
        XCTAssertEqual(store.loadWorking(analysisId: "song1")?.name, "Second")
        // Odd/hostile ids sanitize instead of escaping the dir.
        try store.saveWorking(project(name: "Weird", song: "../../etc/passwd"))
        XCTAssertEqual(
            store.loadWorking(analysisId: "../../etc/passwd")?.name, "Weird")
    }

    func testBlankCanvasWorkingSaveIsNoOp() throws {
        // v2 blank-canvas projects (nil baseSongId) have no per-song
        // working slot on desktop.
        try store.saveWorking(project(name: "Canvas", song: nil))
        XCTAssertTrue(store.list().isEmpty)
    }

    func testDuplicateAndRename() throws {
        let p = project()
        try store.save(p)
        let copy = try store.duplicate(projectId: p.id, name: "Copy")
        XCTAssertNotEqual(copy.id, p.id)
        XCTAssertEqual(copy.baseSongId, p.baseSongId)
        XCTAssertEqual(copy.snapshot, p.snapshot)
        let renamed = try store.rename(projectId: p.id, to: "Renamed")
        XCTAssertEqual(renamed.name, "Renamed")
        XCTAssertEqual(try store.load(projectId: p.id).name, "Renamed")
        XCTAssertEqual(store.list().count, 2)
    }

    func testDelete() throws {
        let p = project()
        try store.save(p)
        try store.delete(projectId: p.id)
        XCTAssertTrue(store.list().isEmpty)
        // Deleting twice is fine.
        XCTAssertNoThrow(try store.delete(projectId: p.id))
    }
}
