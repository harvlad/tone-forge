// PacksModelTests.swift
//
// Pack browser state against a stub provider and a temp-dir cache:
// catalog fetch populates entries + error state, download progress
// flows through and auto-activates on completion, activation loads
// from the bank and fires the callback.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class PacksModelTests: XCTestCase {

    private var cacheRoot: URL!
    private var model: PacksModel!
    private var stubProvider: StubPackProvider!

    override func setUp() async throws {
        cacheRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("packs-tests-\(UUID())", isDirectory: true)
        try FileManager.default.createDirectory(
            at: cacheRoot, withIntermediateDirectories: true)
        stubProvider = StubPackProvider()
        model = PacksModel(provider: stubProvider, cacheRoot: cacheRoot)
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: cacheRoot)
    }

    // MARK: - Catalog

    func testLoadCatalogPopulatesEntries() async {
        stubProvider.catalog = [
            SamplePackCatalogEntry(
                packId: "pk1", name: "Pack 1", family: .pads, padCount: 4)
        ]
        await model.loadCatalog(baseURL: URL(string: "https://x")!)
        XCTAssertEqual(model.entries.count, 1)
        XCTAssertEqual(model.entries[0].packId, "pk1")
        XCTAssertFalse(model.isLoading)
        XCTAssertNil(model.errorMessage)
    }

    func testLoadCatalogSetsErrorOnFailure() async {
        stubProvider.catalogError = NSError(
            domain: "test", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "Network fail"])
        await model.loadCatalog(baseURL: URL(string: "https://x")!)
        XCTAssertTrue(model.entries.isEmpty)
        XCTAssertEqual(model.errorMessage, "Network fail")
    }

    // MARK: - Download + activation

    func testDownloadProgressAndAutoActivate() async throws {
        // Write a minimal cached pack so loadCached succeeds.
        let pack = SamplePack(
            manifestVersion: 1, packId: "pk1", name: "Pack 1",
            family: .pads, pads: [], license: nil, provenance: nil)
        let packDir = cacheRoot.appendingPathComponent("pk1", isDirectory: true)
        try FileManager.default.createDirectory(
            at: packDir, withIntermediateDirectories: true)
        try JSONEncoder().encode(pack).write(
            to: packDir.appendingPathComponent("manifest.json"))

        stubProvider.downloadEvents = [
            PackDownloadProgress(
                packId: "pk1", padsCompleted: 0, padsTotal: 2,
                bytesDownloaded: 0, bytesTotal: 0, isComplete: false,
                manifestLocalURL: nil, packLocalDir: nil),
            PackDownloadProgress(
                packId: "pk1", padsCompleted: 2, padsTotal: 2,
                bytesDownloaded: 100, bytesTotal: 100, isComplete: true,
                manifestLocalURL: packDir.appendingPathComponent("manifest.json"),
                packLocalDir: packDir),
        ]

        var activatedIds: [String] = []
        model.onPackActivated = { activatedIds.append($0.pack.packId) }

        model.download(baseURL: URL(string: "https://x")!, packId: "pk1")
        // Let stream drain.
        try await Task.sleep(nanoseconds: 100_000_000)

        XCTAssertTrue(model.cachedPackIds.contains("pk1"))
        XCTAssertEqual(activatedIds, ["pk1"])
        XCTAssertNotNil(model.activePack)
        XCTAssertEqual(model.activePack?.pack.packId, "pk1")
    }

    func testActivateCallsCallback() throws {
        let pack = SamplePack(
            manifestVersion: 1, packId: "pk2", name: "Pack 2",
            family: .percussion, pads: [], license: nil, provenance: nil)
        let packDir = cacheRoot.appendingPathComponent("pk2", isDirectory: true)
        try FileManager.default.createDirectory(
            at: packDir, withIntermediateDirectories: true)
        try JSONEncoder().encode(pack).write(
            to: packDir.appendingPathComponent("manifest.json"))
        model.refreshCached()

        var activated: [String] = []
        model.onPackActivated = { activated.append($0.pack.packId) }
        model.activate(packId: "pk2")
        XCTAssertEqual(activated, ["pk2"])
        XCTAssertEqual(model.activePack?.pack.packId, "pk2")
    }
}

// MARK: - Stub

private final class StubPackProvider: PackCatalogProviding, @unchecked Sendable {
    var catalog: [SamplePackCatalogEntry] = []
    var catalogError: Error?
    var downloadEvents: [PackDownloadProgress] = []

    func fetchCatalog(baseURL: URL) async throws -> [SamplePackCatalogEntry] {
        if let err = catalogError { throw err }
        return catalog
    }

    func download(
        baseURL: URL, packId: String, cacheRoot: URL
    ) -> AsyncThrowingStream<PackDownloadProgress, Error> {
        let events = downloadEvents
        return AsyncThrowingStream { cont in
            Task {
                for e in events {
                    cont.yield(e)
                    try? await Task.sleep(nanoseconds: 10_000_000)
                }
                cont.finish()
            }
        }
    }
}
