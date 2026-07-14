// BundleStoreCacheTests.swift
//
// LRU stem-cache eviction: size accounting, recency ordering, and the
// `keeping` guard. Uses the rootOverride test seam so nothing touches
// the real Caches directory.

import XCTest
@testable import ToneForgeEngine

final class BundleStoreCacheTests: XCTestCase {

    private var root: URL!
    private var store: BundleStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("bundle-store-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: true
        )
        store = BundleStore(rootOverride: root)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    /// Create a fake analysis dir holding `bytes` of stem data with the
    /// given modification date (LRU recency).
    private func makeAnalysis(
        _ id: String, bytes: Int, modified: Date
    ) throws -> URL {
        let dir = try store.stemsDir()
            .appendingPathComponent(id, isDirectory: true)
        try FileManager.default.createDirectory(
            at: dir, withIntermediateDirectories: true
        )
        let file = dir.appendingPathComponent("drums.wav")
        try Data(repeating: 0xAB, count: bytes).write(to: file)
        try FileManager.default.setAttributes(
            [.modificationDate: modified], ofItemAtPath: dir.path
        )
        return dir
    }

    func testNoEvictionUnderLimit() throws {
        _ = try makeAnalysis("a", bytes: 100, modified: Date())
        _ = try makeAnalysis("b", bytes: 100, modified: Date())
        let evicted = store.enforceStemCacheLimit(maxBytes: 1_000)
        XCTAssertTrue(evicted.isEmpty)
    }

    func testEvictsOldestFirstUntilUnderLimit() throws {
        let old = try makeAnalysis(
            "old", bytes: 400, modified: Date(timeIntervalSinceNow: -3_000)
        )
        let mid = try makeAnalysis(
            "mid", bytes: 400, modified: Date(timeIntervalSinceNow: -2_000)
        )
        let new = try makeAnalysis(
            "new", bytes: 400, modified: Date(timeIntervalSinceNow: -1_000)
        )

        // 1200 total, cap 900 — evicting "old" alone gets to 800.
        let evicted = store.enforceStemCacheLimit(maxBytes: 900)

        XCTAssertEqual(evicted, ["old"])
        XCTAssertFalse(FileManager.default.fileExists(atPath: old.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: mid.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: new.path))
    }

    func testKeepingGuardSkipsProtectedId() throws {
        let old = try makeAnalysis(
            "old", bytes: 400, modified: Date(timeIntervalSinceNow: -3_000)
        )
        let mid = try makeAnalysis(
            "mid", bytes: 400, modified: Date(timeIntervalSinceNow: -2_000)
        )
        _ = try makeAnalysis(
            "new", bytes: 400, modified: Date(timeIntervalSinceNow: -1_000)
        )

        let evicted = store.enforceStemCacheLimit(
            maxBytes: 900, keeping: ["old"]
        )

        // "old" is protected, so the next-coldest goes instead.
        XCTAssertEqual(evicted, ["mid"])
        XCTAssertTrue(FileManager.default.fileExists(atPath: old.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: mid.path))
    }

    func testEvictsMultipleWhenOneIsNotEnough() throws {
        _ = try makeAnalysis(
            "a", bytes: 400, modified: Date(timeIntervalSinceNow: -3_000)
        )
        _ = try makeAnalysis(
            "b", bytes: 400, modified: Date(timeIntervalSinceNow: -2_000)
        )
        _ = try makeAnalysis(
            "c", bytes: 400, modified: Date(timeIntervalSinceNow: -1_000)
        )

        // Cap 500: must drop a AND b (1200 -> 800 -> 400).
        let evicted = store.enforceStemCacheLimit(maxBytes: 500)
        XCTAssertEqual(evicted, ["a", "b"])
    }

    func testCachedStemHitRefreshesRecency() throws {
        let stale = Date(timeIntervalSinceNow: -10_000)
        let dir = try makeAnalysis("hit", bytes: 10, modified: stale)
        let stem = BundleStem(
            role: "drums", url: "x.wav", codec: "wav", sampleRateHz: 44_100
        )

        let hit = store.cachedStem(for: stem, analysisId: "hit")

        XCTAssertNotNil(hit)
        let modified = try FileManager.default
            .attributesOfItem(atPath: dir.path)[.modificationDate] as? Date
        XCTAssertNotNil(modified)
        XCTAssertGreaterThan(modified!, Date(timeIntervalSinceNow: -60))
    }
}
