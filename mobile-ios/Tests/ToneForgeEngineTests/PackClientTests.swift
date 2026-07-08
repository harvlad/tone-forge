// PackClientTests.swift
//
// URLProtocol-mock coverage for the curated pack client:
//   - fetchCatalog decodes `{ "packs": [...] }` → `[SamplePackCatalogEntry]`.
//   - Catalog entry defaults (missing `tags`, `padCount`, `family`).
//   - fetchManifest returns a SamplePack + correct URL construction.
//   - Path-traversal packId rejected client-side.
//   - HTTP 500 → PackClientError.httpStatus.
//   - download(...) writes manifest + pads to `cacheRoot/{packId}/`
//     and yields progress events + a terminal `isComplete` event.
//   - Fully cached pack short-circuits without any network hits.
//
// Uses a route-based MockURLProtocol so a single test can stub
// distinct responses for the catalog, manifest, and each pad URL.

import XCTest
@testable import ToneForgeEngine

final class PackClientTests: XCTestCase {

    private var session: URLSession!
    private var tmpDir: URL!

    override func setUp() {
        super.setUp()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [PackMockURLProtocol.self]
        session = URLSession(configuration: config)
        PackMockURLProtocol.reset()

        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("packclient-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(
            at: tmpDir, withIntermediateDirectories: true
        )
    }

    override func tearDown() {
        PackMockURLProtocol.reset()
        session = nil
        if let dir = tmpDir { try? FileManager.default.removeItem(at: dir) }
        tmpDir = nil
        super.tearDown()
    }

    // MARK: - Catalog

    func testFetchCatalogDecodesWrapper() async throws {
        let entries = [
            SamplePackCatalogEntry(
                packId: "shoegaze-textures",
                name: "Shoegaze Textures",
                family: .pads,
                paletteHint: "purple",
                tags: ["ambient", "dreampop"],
                sizeBytes: nil,
                coverUrl: nil,
                description: "Detuned pads.",
                padCount: 8
            ),
            SamplePackCatalogEntry(
                packId: "lo-fi-hiphop",
                name: "Lo-Fi Hip Hop",
                family: .mixed,
                paletteHint: "amber",
                tags: ["lofi"],
                sizeBytes: nil,
                coverUrl: nil,
                description: "Dusty.",
                padCount: 8
            ),
        ]
        struct Wrapper: Encodable { let packs: [SamplePackCatalogEntry] }
        let body = try JSONEncoder().encode(Wrapper(packs: entries))
        PackMockURLProtocol.stubPath(
            "/api/sample-packs", status: 200, body: body
        )

        let client = PackClient(session: session)
        let out = try await client.fetchCatalog(
            baseURL: URL(string: "https://example.com")!
        )
        XCTAssertEqual(out.count, 2)
        XCTAssertEqual(out[0].packId, "shoegaze-textures")
        XCTAssertEqual(out[0].family, .pads)
        XCTAssertEqual(out[1].padCount, 8)
    }

    func testFetchCatalogDefaultsMissingFields() async throws {
        // Missing family, tags, padCount, description.
        let json = """
        { "packs": [ { "packId": "sparse", "name": "Sparse Pack" } ] }
        """
        PackMockURLProtocol.stubPath(
            "/api/sample-packs", status: 200, body: Data(json.utf8)
        )
        let client = PackClient(session: session)
        let out = try await client.fetchCatalog(
            baseURL: URL(string: "https://example.com")!
        )
        XCTAssertEqual(out.count, 1)
        XCTAssertEqual(out[0].family, .mixed)   // default
        XCTAssertEqual(out[0].tags, [])         // default
        XCTAssertEqual(out[0].padCount, 0)      // default
        XCTAssertNil(out[0].description)
    }

    func testFetchCatalogDecodesPhase10Fields() async throws {
        // A "new" catalog carrying the Phase 10 browse facets.
        let json = """
        { "packs": [ {
            "packId": "rich",
            "name": "Rich Pack",
            "family": "pads",
            "tags": ["ambient"],
            "genres": ["shoegaze", "dream pop"],
            "moods": ["dreamy"],
            "coverUrl": "/api/sample-packs/rich/cover",
            "previewUrl": "/api/sample-packs/rich/preview",
            "padCount": 8
        } ] }
        """
        PackMockURLProtocol.stubPath(
            "/api/sample-packs", status: 200, body: Data(json.utf8)
        )
        let client = PackClient(session: session)
        let out = try await client.fetchCatalog(
            baseURL: URL(string: "https://example.com")!
        )
        XCTAssertEqual(out.count, 1)
        XCTAssertEqual(out[0].genres, ["shoegaze", "dream pop"])
        XCTAssertEqual(out[0].moods, ["dreamy"])
        XCTAssertEqual(out[0].previewUrl, "/api/sample-packs/rich/preview")
        XCTAssertEqual(out[0].coverUrl, "/api/sample-packs/rich/cover")
    }

    func testFetchCatalogOldCatalogDefaultsPhase10Fields() async throws {
        // A pre-Phase-10 catalog (no genres/moods/previewUrl) must
        // still decode; the new fields default to empty/nil.
        let json = """
        { "packs": [ {
            "packId": "legacy",
            "name": "Legacy Pack",
            "family": "percussion",
            "tags": ["oldschool"],
            "padCount": 8
        } ] }
        """
        PackMockURLProtocol.stubPath(
            "/api/sample-packs", status: 200, body: Data(json.utf8)
        )
        let client = PackClient(session: session)
        let out = try await client.fetchCatalog(
            baseURL: URL(string: "https://example.com")!
        )
        XCTAssertEqual(out.count, 1)
        XCTAssertEqual(out[0].genres, [])
        XCTAssertEqual(out[0].moods, [])
        XCTAssertNil(out[0].previewUrl)
    }

    func testFetchCatalogSurfacesHttpErrorStatus() async {
        PackMockURLProtocol.stubPath("/api/sample-packs", status: 500, body: Data())
        let client = PackClient(session: session)
        do {
            _ = try await client.fetchCatalog(
                baseURL: URL(string: "https://example.com")!
            )
            XCTFail("expected httpStatus")
        } catch let PackClientError.httpStatus(code) {
            XCTAssertEqual(code, 500)
        } catch {
            XCTFail("expected PackClientError.httpStatus, got \(error)")
        }
    }

    // MARK: - Manifest

    func testFetchManifestBuildsCanonicalPath() async throws {
        let pack = SamplePack(
            packId: "shoegaze-textures",
            name: "Shoegaze Textures",
            family: .pads,
            paletteHint: "purple",
            pads: []
        )
        let body = try JSONEncoder().encode(pack)
        PackMockURLProtocol.stubPath(
            "/api/sample-packs/shoegaze-textures", status: 200, body: body
        )

        let client = PackClient(session: session)
        let out = try await client.fetchManifest(
            baseURL: URL(string: "https://example.com")!,
            packId: "shoegaze-textures"
        )
        XCTAssertEqual(out.packId, "shoegaze-textures")

        let seen = try XCTUnwrap(PackMockURLProtocol.lastRequestedURL)
        XCTAssertEqual(seen.path, "/api/sample-packs/shoegaze-textures")
    }

    func testFetchManifestRejectsPathTraversalPackId() async {
        let client = PackClient(session: session)
        do {
            _ = try await client.fetchManifest(
                baseURL: URL(string: "https://example.com")!,
                packId: "../etc/passwd"
            )
            XCTFail("expected invalidPackId")
        } catch let PackClientError.invalidPackId(id) {
            XCTAssertEqual(id, "../etc/passwd")
        } catch {
            XCTFail("expected PackClientError.invalidPackId, got \(error)")
        }
    }

    // MARK: - Download

    func testDownloadWritesManifestAndPadsAndYieldsProgress() async throws {
        let pack = SamplePack(
            packId: "tiny",
            name: "Tiny",
            family: .percussion,
            paletteHint: nil,
            pads: [
                SamplePad(padIdx: 0, name: "Kick", family: .percussion,
                          filename: "00_kick.m4a"),
                SamplePad(padIdx: 1, name: "Snare", family: .percussion,
                          filename: "01_snare.m4a"),
            ]
        )
        PackMockURLProtocol.stubPath(
            "/api/sample-packs/tiny",
            status: 200,
            body: try JSONEncoder().encode(pack)
        )
        // Pad bytes — just some non-empty payload so file sizes are > 0.
        let kickBytes = Data(repeating: 0x11, count: 128)
        let snareBytes = Data(repeating: 0x22, count: 256)
        PackMockURLProtocol.stubPath(
            "/api/sample-packs/tiny/pads/00_kick.m4a",
            status: 200, body: kickBytes
        )
        PackMockURLProtocol.stubPath(
            "/api/sample-packs/tiny/pads/01_snare.m4a",
            status: 200, body: snareBytes
        )

        let client = PackClient(session: session)
        var events: [PackDownloadProgress] = []
        for try await p in client.download(
            baseURL: URL(string: "https://example.com")!,
            packId: "tiny",
            cacheRoot: tmpDir
        ) {
            events.append(p)
        }

        XCTAssertGreaterThanOrEqual(events.count, 2,
            "expected at least one incremental + one terminal event")
        XCTAssertTrue(events.last?.isComplete == true)
        XCTAssertEqual(events.last?.padsCompleted, 2)
        XCTAssertEqual(events.last?.padsTotal, 2)
        XCTAssertGreaterThan(events.last?.bytesDownloaded ?? 0, 0)

        // Files landed under cacheRoot/tiny/…
        let packDir = tmpDir.appendingPathComponent("tiny")
        let manifest = packDir.appendingPathComponent("manifest.json")
        let kick = packDir.appendingPathComponent("pads/00_kick.m4a")
        let snare = packDir.appendingPathComponent("pads/01_snare.m4a")
        XCTAssertTrue(FileManager.default.fileExists(atPath: manifest.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: kick.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: snare.path))

        // Manifest decodes back to the pack we wrote.
        let round = try JSONDecoder().decode(
            SamplePack.self,
            from: Data(contentsOf: manifest)
        )
        XCTAssertEqual(round.packId, "tiny")
        XCTAssertEqual(round.pads.count, 2)
    }

    func testDownloadShortCircuitsWhenFullyCached() async throws {
        // Pre-populate the cache dir as if a previous download had
        // completed. Then confirm download() emits only a single
        // terminal event and never hits the network.
        let packDir = tmpDir.appendingPathComponent("preloaded")
        let padsDir = packDir.appendingPathComponent("pads")
        try FileManager.default.createDirectory(
            at: padsDir, withIntermediateDirectories: true
        )
        let pack = SamplePack(
            packId: "preloaded",
            name: "Preloaded",
            family: .textures,
            paletteHint: nil,
            pads: [
                SamplePad(padIdx: 0, name: "A", family: .textures,
                          filename: "00_a.m4a"),
            ]
        )
        try JSONEncoder().encode(pack).write(
            to: packDir.appendingPathComponent("manifest.json")
        )
        try Data(repeating: 0xAB, count: 64).write(
            to: padsDir.appendingPathComponent("00_a.m4a")
        )

        // Intentionally stub NOTHING — a network hit would blow up.
        let client = PackClient(session: session)
        var events: [PackDownloadProgress] = []
        for try await p in client.download(
            baseURL: URL(string: "https://example.com")!,
            packId: "preloaded",
            cacheRoot: tmpDir
        ) {
            events.append(p)
        }

        XCTAssertEqual(events.count, 1)
        XCTAssertTrue(events.first?.isComplete == true)
        XCTAssertEqual(events.first?.padsCompleted, 1)
        XCTAssertNil(PackMockURLProtocol.lastRequestedURL,
                     "cached path must not hit the network")
    }
}

// MARK: - PackMockURLProtocol

/// Route-based URLProtocol shim. Distinct stubs per URL path so a
/// single test can wire the catalog + manifest + N pad endpoints.
private final class PackMockURLProtocol: URLProtocol {

    struct Stub {
        let status: Int
        let body: Data
    }

    nonisolated(unsafe) static var stubsByPath: [String: Stub] = [:]
    nonisolated(unsafe) static var lastRequestedURL: URL?

    static func stubPath(_ path: String, status: Int, body: Data) {
        stubsByPath[path] = Stub(status: status, body: body)
    }

    static func reset() {
        stubsByPath = [:]
        lastRequestedURL = nil
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let url = request.url!
        PackMockURLProtocol.lastRequestedURL = url

        let stub = PackMockURLProtocol.stubsByPath[url.path]
        let status = stub?.status ?? 404
        let body = stub?.body ?? Data()

        let resp = HTTPURLResponse(
            url: url,
            statusCode: status,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Length": String(body.count)]
        )!
        client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
