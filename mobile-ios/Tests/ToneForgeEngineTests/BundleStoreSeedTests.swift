// BundleStoreSeedTests.swift
//
// Demo-bundle seeding (PERFORM_PARITY spec 3.1): copies shipped
// {id}/bundle.json + {id}/stems/{role}.{ext} into the local store,
// is idempotent via a one-shot marker, and honors user deletion.
// Uses the rootOverride seam plus a temp "resource" dir — nothing
// touches the real app bundle.

import XCTest
@testable import ToneForgeEngine

final class BundleStoreSeedTests: XCTestCase {

    private var root: URL!
    private var resourceRoot: URL!
    private var store: BundleStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("seed-store-\(UUID().uuidString)")
        resourceRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("seed-res-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: resourceRoot, withIntermediateDirectories: true)
        store = BundleStore(rootOverride: root)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
        try? FileManager.default.removeItem(at: resourceRoot)
    }

    /// Build a demo dir `{id}/bundle.json` + `{id}/stems/{role}.wav`
    /// under the resource root.
    private func makeDemo(_ id: String, roles: [String]) throws {
        let dir = resourceRoot.appendingPathComponent(id, isDirectory: true)
        let stems = dir.appendingPathComponent("stems", isDirectory: true)
        try FileManager.default.createDirectory(at: stems, withIntermediateDirectories: true)

        let bundle = SongBundle(
            bundleVersion: 1,
            analysisId: id,
            meta: BundleMeta(
                title: "Demo \(id)", artist: "Tone Forge",
                sourceUrl: "", durationSec: 30, tempoBpm: 120,
                detectedKey: "C", license: "CC0"
            ),
            timeline: BundleTimeline(),
            stems: roles.map { BundleStem(role: $0, url: nil, codec: "wav", sampleRateHz: 48_000) },
            presets: [:]
        )
        let enc = JSONEncoder()
        try enc.encode(bundle).write(to: dir.appendingPathComponent("bundle.json"))
        for role in roles {
            try Data(repeating: 0xAB, count: 64)
                .write(to: stems.appendingPathComponent("\(role).wav"))
        }
    }

    func testSeedCopiesBundleAndStems() throws {
        try makeDemo("demo1", roles: ["drums", "bass"])

        let seeded = store.seedBundledDemos(from: resourceRoot)
        XCTAssertEqual(seeded, ["demo1"])

        // Bundle is now listable and each stem is a cache hit.
        let listed = try store.listLocalBundles()
        XCTAssertEqual(listed.map(\.analysisId), ["demo1"])
        let bundle = try XCTUnwrap(store.loadBundle(analysisId: "demo1"))
        for stem in bundle.stems {
            XCTAssertNotNil(store.cachedStem(for: stem, analysisId: "demo1"),
                            "expected cache hit for \(stem.role)")
        }
    }

    func testSeedIsIdempotent() throws {
        try makeDemo("demo1", roles: ["drums"])
        XCTAssertEqual(store.seedBundledDemos(from: resourceRoot), ["demo1"])
        // Second call: marker present → no-op even though resource remains.
        XCTAssertEqual(store.seedBundledDemos(from: resourceRoot), [])
    }

    func testDeletionSticksAcrossReseed() throws {
        try makeDemo("demo1", roles: ["drums"])
        XCTAssertEqual(store.seedBundledDemos(from: resourceRoot), ["demo1"])

        store.deleteLocal(analysisId: "demo1")
        XCTAssertTrue(try store.listLocalBundles().isEmpty)

        // Marker guards the re-seed — a user delete is not undone.
        XCTAssertEqual(store.seedBundledDemos(from: resourceRoot), [])
        XCTAssertTrue(try store.listLocalBundles().isEmpty)
    }

    func testMarkerVersionBumpForcesReseed() throws {
        try makeDemo("demo1", roles: ["drums"])
        XCTAssertEqual(store.seedBundledDemos(from: resourceRoot, markerVersion: "v1"), ["demo1"])
        store.deleteLocal(analysisId: "demo1")
        // New marker version → seeds again (ships new demo content).
        XCTAssertEqual(store.seedBundledDemos(from: resourceRoot, markerVersion: "v2"), ["demo1"])
    }

    func testEmptyResourceDirWritesMarkerNoOp() throws {
        XCTAssertEqual(store.seedBundledDemos(from: resourceRoot), [])
        // Marker written → still a no-op, and no crash on a demo added later
        // under the same version (deliberately: same-version content is frozen).
        try makeDemo("late", roles: ["drums"])
        XCTAssertEqual(store.seedBundledDemos(from: resourceRoot), [])
    }
}
