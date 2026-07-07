// ArtworkStoreTests.swift
//
// Coverage for the on-device album-art store (redesign Phase 2):
//   - save → imageData round trip;
//   - missing art returns nil (the normal case for Files imports);
//   - overwrite replaces the previous image;
//   - delete removes the file;
//   - path separators in an analysisId are sanitized so the file
//     always lands directly under the artwork root;
//   - the new playSurfaceRaw settings field defaults, persists, and
//     back-fills from pre-redesign blobs.

import XCTest
@testable import ToneForgeMobile

final class ArtworkStoreTests: XCTestCase {

    private var root: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "artwork-tests-\(UUID().uuidString)", isDirectory: true
            )
    }

    override func tearDownWithError() throws {
        if let root { try? FileManager.default.removeItem(at: root) }
        root = nil
    }

    func testSaveThenLoadRoundTrips() throws {
        let store = ArtworkStore(root: root)
        let data = Data([0xFF, 0xD8, 0xFF, 0xE0, 1, 2, 3])

        try store.save(data, analysisId: "abc-123")

        XCTAssertEqual(store.imageData(for: "abc-123"), data)
    }

    func testMissingArtReturnsNil() {
        let store = ArtworkStore(root: root)
        XCTAssertNil(store.imageData(for: "never-saved"))
    }

    func testOverwriteReplacesPreviousImage() throws {
        let store = ArtworkStore(root: root)

        try store.save(Data([1]), analysisId: "id")
        try store.save(Data([2, 3]), analysisId: "id")

        XCTAssertEqual(store.imageData(for: "id"), Data([2, 3]))
    }

    func testDeleteRemovesArt() throws {
        let store = ArtworkStore(root: root)
        try store.save(Data([9]), analysisId: "id")

        store.delete(analysisId: "id")

        XCTAssertNil(store.imageData(for: "id"))
    }

    func testPathSeparatorsInIdStayInsideRoot() throws {
        let store = ArtworkStore(root: root)
        let hostile = "../evil/id"

        try store.save(Data([7]), analysisId: hostile)

        // Sanitizer flattens separators, so the file is a direct
        // child of the artwork root with no traversal components.
        let url = store.url(for: hostile)
        XCTAssertEqual(url.lastPathComponent, ".._evil_id.jpg")
        XCTAssertEqual(
            url.deletingLastPathComponent().standardizedFileURL.path,
            root.standardizedFileURL.path
        )
        XCTAssertEqual(store.imageData(for: hostile), Data([7]))
    }

    func testSanitizeReplacesAllSeparatorVariants() {
        XCTAssertEqual(ArtworkStore.sanitize("a/b\\c:d"), "a_b_c_d")
        XCTAssertEqual(ArtworkStore.sanitize("plain-uuid"), "plain-uuid")
    }
}

// MARK: - PlaySurface persistence

@MainActor
final class PlaySurfacePersistenceTests: XCTestCase {

    private var defaults: UserDefaults!
    private let suiteName = "toneforge.tests.playsurface"
    private let blobKey = "toneforge.sampleSettings"

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: suiteName)
        defaults.removePersistentDomain(forName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        super.tearDown()
    }

    func testFreshStoreDefaultsToContribute() {
        let store = SampleSettingsStore(defaults: defaults)
        XCTAssertEqual(store.playSurfaceRaw, "contribute")
        XCTAssertEqual(SampleSettingsStore.defaultPlaySurfaceRaw, "contribute")
    }

    func testSurfacePersistsAcrossStoreInit() {
        let store1 = SampleSettingsStore(defaults: defaults)
        store1.playSurfaceRaw = "jam"

        let store2 = SampleSettingsStore(defaults: defaults)
        XCTAssertEqual(store2.playSurfaceRaw, "jam")
    }

    func testPreRedesignBlobDecodesWithContributeDefault() throws {
        // Write a current-shape blob, then strip the new key to
        // simulate a blob from before the redesign.
        let seed = SampleSettingsStore(defaults: defaults)
        seed.currentPackId = "legacy-pack"

        let data = try XCTUnwrap(defaults.data(forKey: blobKey))
        var json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        json.removeValue(forKey: "playSurfaceRaw")
        defaults.set(
            try JSONSerialization.data(withJSONObject: json), forKey: blobKey
        )

        let store = SampleSettingsStore(defaults: defaults)
        XCTAssertEqual(store.playSurfaceRaw, "contribute")
        XCTAssertEqual(store.currentPackId, "legacy-pack")
    }
}
