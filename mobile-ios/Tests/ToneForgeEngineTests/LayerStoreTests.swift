// LayerStoreTests.swift
//
// Unit tests for `LayerStore` — the on-disk persistence layer for
// LayerTimeline JSON files. Tests use an isolated temp root per test
// so nothing pollutes the real Application Support dir.

import XCTest
@testable import ToneForgeEngine

final class LayerStoreTests: XCTestCase {

    private var tmpRoot: URL!
    private var store: LayerStore!

    override func setUp() {
        super.setUp()
        tmpRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("layerstore-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(
            at: tmpRoot, withIntermediateDirectories: true
        )
        store = LayerStore(root: tmpRoot)
    }

    override func tearDown() {
        if let root = tmpRoot { try? FileManager.default.removeItem(at: root) }
        tmpRoot = nil
        store = nil
        super.tearDown()
    }

    // MARK: - Paths

    func testLayersDirCreatedUnderRoot() throws {
        let dir = try store.layersDir()
        XCTAssertTrue(FileManager.default.fileExists(atPath: dir.path))
        XCTAssertTrue(dir.path.contains("toneforge/layers"))
    }

    func testSongDirNested() throws {
        let dir = try store.songDir(analysisId: "abc123")
        XCTAssertTrue(FileManager.default.fileExists(atPath: dir.path))
        XCTAssertTrue(dir.path.hasSuffix("abc123"))
    }

    // MARK: - Save + load

    func testSaveLoadRoundTrip() throws {
        let tl = makeTimeline(analysisId: "song-A", padIdxs: [0, 1, 2])
        try store.save(tl)
        let round = try store.load(analysisId: "song-A", layerId: tl.layerId)
        XCTAssertEqual(round, tl)
    }

    func testLoadMissingThrows() {
        do {
            _ = try store.load(analysisId: "nope", layerId: "nope")
            XCTFail("expected notFound")
        } catch LayerStore.StoreError.notFound {
            // ok
        } catch {
            XCTFail("expected notFound, got \(error)")
        }
    }

    // MARK: - List

    func testListReturnsAllLayersNewestFirst() throws {
        let a = makeTimeline(analysisId: "song-B", padIdxs: [0], createdAtEpoch: 1000)
        let b = makeTimeline(analysisId: "song-B", padIdxs: [1], createdAtEpoch: 2000)
        let c = makeTimeline(analysisId: "song-B", padIdxs: [2], createdAtEpoch: 3000)
        try store.save(a)
        try store.save(b)
        try store.save(c)

        let listed = store.list(analysisId: "song-B")
        XCTAssertEqual(listed.count, 3)
        // newest first
        XCTAssertEqual(listed[0].layerId, c.layerId)
        XCTAssertEqual(listed[1].layerId, b.layerId)
        XCTAssertEqual(listed[2].layerId, a.layerId)
    }

    func testListIsolatesPerAnalysisId() throws {
        let a = makeTimeline(analysisId: "song-A", padIdxs: [0])
        let b = makeTimeline(analysisId: "song-B", padIdxs: [1])
        try store.save(a)
        try store.save(b)

        XCTAssertEqual(store.list(analysisId: "song-A").map { $0.layerId }, [a.layerId])
        XCTAssertEqual(store.list(analysisId: "song-B").map { $0.layerId }, [b.layerId])
    }

    func testListSkipsCorruptFiles() throws {
        let good = makeTimeline(analysisId: "song-X", padIdxs: [0, 1])
        try store.save(good)

        // Drop a garbage .json alongside the good one.
        let dir = try store.songDir(analysisId: "song-X")
        try Data("not json".utf8).write(
            to: dir.appendingPathComponent("garbage.json")
        )

        let listed = store.list(analysisId: "song-X")
        XCTAssertEqual(listed.count, 1)
        XCTAssertEqual(listed.first?.layerId, good.layerId)
    }

    // MARK: - Sketch sentinel

    func testSketchSentinelSaveListIsolated() throws {
        // Sketch layers live under the `__sketch__` sentinel and use
        // the exact same layout as real songs — and never bleed into
        // a real analysisId's listing.
        let sketch = makeTimeline(
            analysisId: LayerStore.sketchAnalysisId, padIdxs: [0, 3]
        )
        let song = makeTimeline(analysisId: "song-real", padIdxs: [1])
        try store.save(sketch)
        try store.save(song)

        let sketchListed = store.list(analysisId: LayerStore.sketchAnalysisId)
        XCTAssertEqual(sketchListed.map { $0.layerId }, [sketch.layerId])
        XCTAssertEqual(
            store.list(analysisId: "song-real").map { $0.layerId },
            [song.layerId]
        )

        // Round-trips + deletes like any other id.
        let round = try store.load(
            analysisId: LayerStore.sketchAnalysisId, layerId: sketch.layerId
        )
        XCTAssertEqual(round, sketch)
        try store.delete(
            analysisId: LayerStore.sketchAnalysisId, layerId: sketch.layerId
        )
        XCTAssertTrue(store.list(analysisId: LayerStore.sketchAnalysisId).isEmpty)
    }

    // MARK: - Delete

    func testDeleteRemovesFile() throws {
        let tl = makeTimeline(analysisId: "song-C", padIdxs: [0])
        try store.save(tl)
        XCTAssertEqual(store.list(analysisId: "song-C").count, 1)

        try store.delete(analysisId: "song-C", layerId: tl.layerId)
        XCTAssertEqual(store.list(analysisId: "song-C").count, 0)
    }

    func testDeleteMissingIsNoOp() throws {
        // Shouldn't throw.
        try store.delete(analysisId: "song-Z", layerId: "does-not-exist")
    }

    // MARK: - Rename

    func testRenameUpdatesNameOnDisk() throws {
        let tl = makeTimeline(analysisId: "song-D", padIdxs: [0])
        try store.save(tl)

        try store.rename(analysisId: "song-D", layerId: tl.layerId, to: "My Take")
        let round = try store.load(analysisId: "song-D", layerId: tl.layerId)
        XCTAssertEqual(round.name, "My Take")
        // Events untouched.
        XCTAssertEqual(round.events, tl.events)
        XCTAssertEqual(round.layerId, tl.layerId)
    }

    func testRenamePreservesSketchMetadata() throws {
        // rename() is mutate-and-save — the optional sketch metadata
        // must survive the rewrite.
        let tl = LayerTimeline(
            layerId: UUID().uuidString,
            analysisId: LayerStore.sketchAnalysisId,
            name: "Take",
            createdAtEpoch: 1_700_000_000,
            durationSec: 4.0,
            events: [
                LayerEvent(
                    kind: .sampleOn,
                    songTimeSec: 0,
                    params: LayerEvent.Params(padIdx: 0)
                ),
            ],
            activePackId: "starter",
            sketchTempoBpm: 96,
            sketchTimeSigNumerator: 4,
            packName: "Starter"
        )
        try store.save(tl)

        try store.rename(
            analysisId: LayerStore.sketchAnalysisId,
            layerId: tl.layerId,
            to: "Renamed"
        )
        let round = try store.load(
            analysisId: LayerStore.sketchAnalysisId, layerId: tl.layerId
        )
        XCTAssertEqual(round.name, "Renamed")
        XCTAssertEqual(round.sketchTempoBpm, 96)
        XCTAssertEqual(round.sketchTimeSigNumerator, 4)
        XCTAssertEqual(round.packName, "Starter")
        XCTAssertEqual(round.events, tl.events)
    }

    // MARK: - Helpers

    private func makeTimeline(
        analysisId: String,
        padIdxs: [Int],
        createdAtEpoch: Double = 1_700_000_000
    ) -> LayerTimeline {
        let events = padIdxs.enumerated().map { i, pad in
            LayerEvent(
                kind: .sampleOn,
                songTimeSec: Double(i) * 0.5,
                params: LayerEvent.Params(padIdx: pad, velocity: 1.0)
            )
        }
        return LayerTimeline(
            layerId: UUID().uuidString,
            analysisId: analysisId,
            name: "Test",
            createdAtEpoch: createdAtEpoch,
            durationSec: Double(padIdxs.count) * 0.5,
            events: events,
            activePackId: "test-pack"
        )
    }
}
