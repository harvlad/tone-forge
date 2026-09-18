// ProjectStoreTests.swift
//
// Documents/projects persistence: save/list/load/duplicate/delete/
// rename for durable projects, the per-song working-project sidecar,
// and the corrupt-file-skip policy — against a temp root so tests
// never touch the real Documents (SessionStore test pattern).

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

final class ProjectStoreTests: XCTestCase {

    private var root: URL!
    private var store: ProjectStore!

    override func setUpWithError() throws {
        try super.setUpWithError()
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("ProjectStoreTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: true)
        store = ProjectStore(root: root)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
        try super.tearDownWithError()
    }

    private func makeProject(
        name: String = "Test project",
        songId: String = "song-1",
        updatedAt: Date = Date()
    ) -> Project {
        Project(
            name: name, updatedAt: updatedAt, baseSongId: songId,
            baseSongTitle: "Song One",
            snapshot: ProjectSnapshot(
                hiddenPads: ["auto-x#1"],
                sectionGates: [],
                launchpad: LaunchpadSnapshot(
                    padCount: 64, sampleTriggerMode: "follow")))
    }

    // MARK: - Durable CRUD

    func testSaveLoadRoundTrip() throws {
        let project = makeProject()
        try store.save(project)
        let loaded = try store.load(projectId: project.id)
        XCTAssertEqual(loaded, project)
        // Tri-state survives disk: deny-all gates stay [] not nil.
        XCTAssertEqual(loaded.snapshot.sectionGates, [])
    }

    func testListNewestUpdatedFirstAndSkipsCorrupt() throws {
        let old = makeProject(name: "Old",
                              updatedAt: Date(timeIntervalSinceNow: -100))
        let new = makeProject(name: "New", updatedAt: Date())
        try store.save(old)
        try store.save(new)
        // A corrupt file must be skipped, never hide the rest.
        let junk = try store.projectsDir()
            .appendingPathComponent("\(UUID().uuidString).json")
        try Data("not json".utf8).write(to: junk)

        let listed = store.list()
        XCTAssertEqual(listed.map(\.name), ["New", "Old"])
    }

    func testDelete() throws {
        let project = makeProject()
        try store.save(project)
        try store.delete(projectId: project.id)
        XCTAssertTrue(store.list().isEmpty)
        XCTAssertThrowsError(try store.load(projectId: project.id))
        // Deleting a non-existent project is a no-op, not a throw.
        XCTAssertNoThrow(try store.delete(projectId: project.id))
    }

    func testDuplicateGetsFreshIdentitySameSnapshot() throws {
        let project = makeProject()
        try store.save(project)
        let copy = try store.duplicate(projectId: project.id,
                                       name: "Copy")
        XCTAssertNotEqual(copy.id, project.id)
        XCTAssertEqual(copy.name, "Copy")
        XCTAssertEqual(copy.snapshot, project.snapshot)
        XCTAssertEqual(copy.baseSongId, project.baseSongId)
        XCTAssertEqual(store.list().count, 2)
    }

    func testRenameBumpsUpdatedAt() throws {
        let project = makeProject(
            updatedAt: Date(timeIntervalSinceNow: -100))
        try store.save(project)
        let renamed = try store.rename(projectId: project.id,
                                       to: "Renamed")
        XCTAssertEqual(renamed.name, "Renamed")
        XCTAssertGreaterThan(renamed.updatedAt, project.updatedAt)
        XCTAssertEqual(try store.load(projectId: project.id).name,
                       "Renamed")
    }

    // MARK: - Working project

    func testWorkingSaveLoadDeleteIsPerSong() throws {
        let a = makeProject(songId: "song-a")
        let b = makeProject(songId: "song-b")
        try store.saveWorking(a)
        try store.saveWorking(b)

        XCTAssertEqual(store.loadWorking(analysisId: "song-a"), a)
        XCTAssertEqual(store.loadWorking(analysisId: "song-b"), b)
        XCTAssertNil(store.loadWorking(analysisId: "song-c"))
        // Working saves never appear in the Library list.
        XCTAssertTrue(store.list().isEmpty)

        store.deleteWorking(analysisId: "song-a")
        XCTAssertNil(store.loadWorking(analysisId: "song-a"))
        XCTAssertNotNil(store.loadWorking(analysisId: "song-b"))
    }

    func testWorkingOverwritesPerSong() throws {
        let first = makeProject(songId: "song-a")
        try store.saveWorking(first)
        var second = makeProject(songId: "song-a")
        second.snapshot.hiddenPads = ["auto-x#5"]
        try store.saveWorking(second)
        XCTAssertEqual(store.loadWorking(analysisId: "song-a"), second)
    }

    func testWorkingFileNameSanitizesHostileIds() throws {
        // A path-hostile analysisId must not traverse out of the
        // working dir (or crash the write).
        let hostile = makeProject(songId: "../../evil/../id with spaces")
        XCTAssertNoThrow(try store.saveWorking(hostile))
        XCTAssertEqual(
            store.loadWorking(analysisId: "../../evil/../id with spaces"),
            hostile)
        // Everything stayed under projects/.
        let escaped = root.appendingPathComponent("evil")
        XCTAssertFalse(FileManager.default.fileExists(atPath: escaped.path))
    }
}
