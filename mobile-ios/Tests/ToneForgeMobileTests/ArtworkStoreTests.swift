// ArtworkStoreTests.swift
//
// Coverage for the on-device album-art store (redesign Phase 2):
//   - save → imageData round trip;
//   - missing art returns nil (the normal case for Files imports);
//   - overwrite replaces the previous image;
//   - delete removes the file;
//   - path separators in an analysisId are sanitized so the file
//     always lands directly under the artwork root;
//   - the appTabRaw settings field defaults, persists, and migrates
//     from the legacy D-019 playSurfaceRaw blob key.

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

// MARK: - AppTab persistence (D-022)

@MainActor
final class AppTabPersistenceTests: XCTestCase {

    private var defaults: UserDefaults!
    private let suiteName = "toneforge.tests.apptab"
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
        XCTAssertEqual(store.appTabRaw, "contribute")
        XCTAssertEqual(SampleSettingsStore.defaultAppTabRaw, "contribute")
    }

    func testTabPersistsAcrossStoreInit() {
        let store1 = SampleSettingsStore(defaults: defaults)
        store1.appTabRaw = "jam"

        let store2 = SampleSettingsStore(defaults: defaults)
        XCTAssertEqual(store2.appTabRaw, "jam")
    }

    func testPreRedesignBlobDecodesWithContributeDefault() throws {
        // Write a current-shape blob, then strip the tab key to
        // simulate a blob from before D-019/D-022.
        let seed = SampleSettingsStore(defaults: defaults)
        seed.currentPackId = "legacy-pack"

        let data = try XCTUnwrap(defaults.data(forKey: blobKey))
        var json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        json.removeValue(forKey: "appTabRaw")
        defaults.set(
            try JSONSerialization.data(withJSONObject: json), forKey: blobKey
        )

        let store = SampleSettingsStore(defaults: defaults)
        XCTAssertEqual(store.appTabRaw, "contribute")
        XCTAssertEqual(store.currentPackId, "legacy-pack")
    }

    func testLegacyPlaySurfaceKeyMigrates() throws {
        // Simulate a D-019 blob: no appTabRaw, legacy playSurfaceRaw
        // set to the retired chordPads surface — it folds into Jam.
        let seed = SampleSettingsStore(defaults: defaults)
        seed.currentPackId = "legacy-pack"

        let data = try XCTUnwrap(defaults.data(forKey: blobKey))
        var json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        json.removeValue(forKey: "appTabRaw")
        json["playSurfaceRaw"] = "chordPads"
        defaults.set(
            try JSONSerialization.data(withJSONObject: json), forKey: blobKey
        )

        let store = SampleSettingsStore(defaults: defaults)
        XCTAssertEqual(store.appTabRaw, "jam")

        // The next save() re-writes the blob with appTabRaw only.
        store.currentPackId = "resaved-pack"
        let resaved = try XCTUnwrap(defaults.data(forKey: blobKey))
        let resavedJson = try XCTUnwrap(
            JSONSerialization.jsonObject(with: resaved) as? [String: Any]
        )
        XCTAssertEqual(resavedJson["appTabRaw"] as? String, "jam")
        XCTAssertNil(resavedJson["playSurfaceRaw"])
    }

    func testLegacySurfaceValuesPassThrough() {
        XCTAssertEqual(AppTab.migratedRaw(fromLegacyPlaySurface: "learn"), "learn")
        XCTAssertEqual(AppTab.migratedRaw(fromLegacyPlaySurface: "jam"), "jam")
        XCTAssertEqual(
            AppTab.migratedRaw(fromLegacyPlaySurface: "contribute"), "contribute"
        )
        XCTAssertEqual(AppTab.migratedRaw(fromLegacyPlaySurface: "chordPads"), "jam")
        XCTAssertEqual(AppTab.migratedRaw(fromLegacyPlaySurface: nil), "contribute")
        XCTAssertEqual(
            AppTab.migratedRaw(fromLegacyPlaySurface: "bogus"), "contribute"
        )
    }
}
