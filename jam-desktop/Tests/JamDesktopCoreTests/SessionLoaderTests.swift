// SessionLoaderTests.swift
//
// Network-first / cache-fallback semantics with a stub fetcher and a
// temp-dir BundleStore (rootOverride keeps the suite hermetic):
//   - fetch success persists the bundle for next time;
//   - fetch failure falls back to the cached copy;
//   - fetch failure with no cache rethrows the network error;
//   - cached stems short-circuit the download pipeline;
//   - a stem whose URL never materializes surfaces stemMissing.

import XCTest
@testable import JamDesktopCore
import ToneForgeEngine

final class SessionLoaderTests: XCTestCase {

    private struct StubError: Error, Equatable {}

    private struct StubFetcher: BundleFetching {
        let result: Result<SongBundle, StubError>
        func fetch(from backend: URL, analysisId: String) async throws -> SongBundle {
            try result.get()
        }
    }

    private var tempRoot: URL!
    private var store: BundleStore!
    private let backend = URL(string: "https://example.com")!

    override func setUp() {
        super.setUp()
        tempRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("session-loader-tests-\(UUID().uuidString)")
        store = BundleStore(rootOverride: tempRoot)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempRoot)
        super.tearDown()
    }

    private func makeBundle(
        analysisId: String = "song-1",
        stems: [BundleStem] = []
    ) -> SongBundle {
        SongBundle(
            bundleVersion: 1,
            analysisId: analysisId,
            meta: BundleMeta(
                title: "Night Drive", artist: "Some Artist",
                sourceUrl: "", durationSec: 60
            ),
            timeline: BundleTimeline(),
            stems: stems,
            presets: [:]
        )
    }

    // MARK: - Bundle load

    func testFetchSuccessPersistsToCache() async throws {
        let bundle = makeBundle()
        let loader = SessionLoader(
            store: store, fetcher: StubFetcher(result: .success(bundle))
        )
        let loaded = try await loader.loadBundle(analysisId: "song-1", backend: backend)
        XCTAssertEqual(loaded, bundle)
        XCTAssertEqual(try store.loadBundle(analysisId: "song-1"), bundle)
    }

    func testFetchFailureFallsBackToCache() async throws {
        let bundle = makeBundle()
        try store.saveBundle(bundle)
        let loader = SessionLoader(
            store: store, fetcher: StubFetcher(result: .failure(StubError()))
        )
        let loaded = try await loader.loadBundle(analysisId: "song-1", backend: backend)
        XCTAssertEqual(loaded, bundle)
    }

    func testFetchFailureWithoutCacheRethrows() async {
        let loader = SessionLoader(
            store: store, fetcher: StubFetcher(result: .failure(StubError()))
        )
        do {
            _ = try await loader.loadBundle(analysisId: "nope", backend: backend)
            XCTFail("expected StubError")
        } catch {
            XCTAssertTrue(error is StubError)
        }
    }

    // MARK: - Stems

    func testCachedStemsShortCircuitDownload() async throws {
        let stem = BundleStem(
            role: "drums", url: "/api/serve-file?p=drums.wav",
            codec: "wav", sampleRateHz: 44100
        )
        let bundle = makeBundle(stems: [stem])
        // Pre-seed the cache file where cachedStem() looks.
        let local = try store.stemLocalURL(
            analysisId: "song-1", role: "drums", ext: "wav"
        )
        try Data([0x52, 0x49, 0x46, 0x46]).write(to: local)

        let loader = SessionLoader(
            store: store, fetcher: StubFetcher(result: .success(bundle))
        )
        let urls = try await loader.materializeStems(bundle: bundle, backend: backend)
        XCTAssertEqual(urls["drums"], local)
    }

    func testStemsWithoutURLsAreSkipped() async throws {
        // A bundle whose stems all lack URLs (analysis-only session)
        // materializes to an empty map without touching the network.
        let stem = BundleStem(role: "drums", url: nil, codec: "wav", sampleRateHz: 44100)
        let bundle = makeBundle(stems: [stem])
        let loader = SessionLoader(
            store: store, fetcher: StubFetcher(result: .success(bundle))
        )
        let urls = try await loader.materializeStems(bundle: bundle, backend: backend)
        XCTAssertTrue(urls.isEmpty)
    }
}
