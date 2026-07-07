// BounceStoreTests.swift
//
// Disk contract for the Documents/bounces browser: audio-only
// newest-first listing, byte totals, single delete constrained to
// the store's own list, and delete-all.

import XCTest
@testable import ToneForgeMobile

@MainActor
final class BounceStoreTests: XCTestCase {

    private var root: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("bounce-store-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    private func makeStore() -> BounceStore {
        BounceStore(root: root)
    }

    /// Drop a file into {root}/bounces with a fixed creation date so
    /// ordering assertions are deterministic.
    @discardableResult
    private func writeFile(
        name: String,
        bytes: Int = 4,
        createdAt: Date = Date(timeIntervalSince1970: 1_700_000_000)
    ) throws -> URL {
        let dir = root.appendingPathComponent("bounces", isDirectory: true)
        try FileManager.default.createDirectory(
            at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent(name)
        try Data(repeating: 0xAB, count: bytes).write(to: url)
        try FileManager.default.setAttributes(
            [.creationDate: createdAt], ofItemAtPath: url.path)
        return url
    }

    func testListsAudioFilesNewestFirst() throws {
        try writeFile(name: "old.wav",
                      createdAt: Date(timeIntervalSince1970: 1_000))
        try writeFile(name: "newest.m4a",
                      createdAt: Date(timeIntervalSince1970: 3_000))
        try writeFile(name: "middle.wav",
                      createdAt: Date(timeIntervalSince1970: 2_000))

        let store = makeStore()
        XCTAssertEqual(
            store.bounces.map(\.name),
            ["newest.m4a", "middle.wav", "old.wav"]
        )
    }

    func testIgnoresNonAudioFiles() throws {
        try writeFile(name: "take.wav")
        try writeFile(name: "notes.txt")
        try writeFile(name: "take.json")

        let store = makeStore()
        XCTAssertEqual(store.bounces.map(\.name), ["take.wav"])
    }

    func testTotalBytesSumsPayloads() throws {
        try writeFile(name: "a.wav", bytes: 100)
        try writeFile(name: "b.m4a", bytes: 250)

        let store = makeStore()
        XCTAssertEqual(store.totalBytes(), 350)
        XCTAssertEqual(
            store.bounces.map(\.bytes).sorted(), [100, 250])
    }

    func testDeleteRemovesFileAndPublishes() throws {
        let keep = try writeFile(name: "keep.wav")
        let drop = try writeFile(name: "drop.wav")

        let store = makeStore()
        store.delete(url: drop)

        // Compare names, not URLs — the scan returns /private/var
        // forms while temporaryDirectory hands out /var aliases.
        XCTAssertEqual(store.bounces.map(\.name), [keep.lastPathComponent])
        XCTAssertFalse(FileManager.default.fileExists(atPath: drop.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: keep.path))
    }

    func testDeleteIgnoresURLsOutsideItsList() throws {
        // A file the store never listed (wrong extension) must
        // survive a delete call aimed at it.
        let outsider = try writeFile(name: "sidecar.json")

        let store = makeStore()
        store.delete(url: outsider)

        XCTAssertTrue(FileManager.default.fileExists(atPath: outsider.path))
    }

    func testDeleteAllClearsListedBouncesOnly() throws {
        try writeFile(name: "a.wav")
        try writeFile(name: "b.m4a")
        let outsider = try writeFile(name: "notes.txt")

        let store = makeStore()
        store.deleteAll()

        XCTAssertTrue(store.bounces.isEmpty)
        XCTAssertEqual(store.totalBytes(), 0)
        // Non-audio neighbours are untouched.
        XCTAssertTrue(FileManager.default.fileExists(atPath: outsider.path))
    }

    func testReloadPicksUpNewFiles() throws {
        let store = makeStore()
        XCTAssertTrue(store.bounces.isEmpty)

        try writeFile(name: "late.wav")
        store.reload()

        XCTAssertEqual(store.bounces.map(\.name), ["late.wav"])
    }

    func testEmptyDirectoryYieldsEmptyList() {
        let store = makeStore()
        XCTAssertTrue(store.bounces.isEmpty)
        XCTAssertEqual(store.totalBytes(), 0)
    }
}
