// BeatModelClientTests.swift
//
// Beat Capture (D-024): the model downloader reconstructs a compiled
// `.mlmodelc` directory from per-file objects + a manifest, verifying
// each member's sha256. A stub URLProtocol serves latest/manifest/file
// so the test needs no network.

import XCTest
import CryptoKit
@testable import ToneForgeEngine

final class BeatModelClientTests: XCTestCase {

    private static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    // Two-member fake model, keyed by member path.
    private static let members: [String: Data] = [
        "model.mlmodel": Data([0, 1, 2, 3, 0xFF]),
        "metadata.json": Data("{\"role\":\"drum\"}".utf8),
    ]

    private func makeSession() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        return URLSession(configuration: config)
    }

    private func stubResponses(version: String, corruptMember: String? = nil) {
        let base = "https://example.test"
        let files = Self.members.map { name, data -> [String: Any] in
            ["path": name, "sha256": Self.sha256(data), "size": data.count]
        }
        StubURLProtocol.responses = [:]
        StubURLProtocol.responses["\(base)/api/beat-model/latest"] =
            try! JSONSerialization.data(withJSONObject: [
                "version": version,
                "sha256": "deadbeef",
                "files": Self.members.count,
                "url": "/api/beat-model/\(version)/manifest",
            ])
        StubURLProtocol.responses["\(base)/api/beat-model/\(version)/manifest"] =
            try! JSONSerialization.data(withJSONObject: [
                "version": version, "files": files,
            ])
        for (name, data) in Self.members {
            var body = data
            if name == corruptMember { body = Data([0xAA]) }  // wrong bytes
            StubURLProtocol.responses[
                "\(base)/api/beat-model/\(version)/file/\(name)"
            ] = body
        }
    }

    private func cleanCache() {
        if let root = BeatModelStore.cacheRoot() {
            try? FileManager.default.removeItem(at: root)
        }
    }

    override func setUp() {
        super.setUp()
        cleanCache()
    }

    override func tearDown() {
        cleanCache()
        StubURLProtocol.responses = [:]
        super.tearDown()
    }

    func testDownloadReconstructsModelDirectory() async throws {
        stubResponses(version: "2026.07.14")
        let client = BeatModelClient(session: makeSession())
        let baseURL = URL(string: "https://example.test")!

        let modelURL = try await client.updateIfAvailable(baseURL: baseURL)
        let url = try XCTUnwrap(modelURL)
        XCTAssertEqual(url.lastPathComponent, "BeatClassifier.mlmodelc")

        let fm = FileManager.default
        for (name, data) in Self.members {
            let member = url.appendingPathComponent(name)
            XCTAssertTrue(fm.fileExists(atPath: member.path), name)
            XCTAssertEqual(try Data(contentsOf: member), data, name)
        }
        // Installed version is now the active model + reported cached.
        XCTAssertTrue(BeatModelStore.isCached(version: "2026.07.14"))
        XCTAssertEqual(
            BeatModelStore.activeModelURL()?.path, url.path
        )
    }

    func testAlreadyCachedVersionSkips() async throws {
        stubResponses(version: "v-cached")
        let client = BeatModelClient(session: makeSession())
        let baseURL = URL(string: "https://example.test")!

        _ = try await client.updateIfAvailable(baseURL: baseURL)
        // Second call: same version already cached → nil (no re-download).
        let second = try await client.updateIfAvailable(baseURL: baseURL)
        XCTAssertNil(second)
    }

    func testChecksumMismatchThrowsAndLeavesNoVersionDir() async {
        stubResponses(version: "v-bad", corruptMember: "model.mlmodel")
        let client = BeatModelClient(session: makeSession())
        let baseURL = URL(string: "https://example.test")!

        do {
            _ = try await client.download(baseURL: baseURL, version: "v-bad")
            XCTFail("expected checksum mismatch to throw")
        } catch {
            // A failed download must not leave a usable version dir.
            XCTAssertFalse(BeatModelStore.isCached(version: "v-bad"))
        }
    }

    func testSafeMemberPathRejectsTraversal() {
        XCTAssertTrue(BeatModelClient.isSafeMemberPath("a/b/c.bin"))
        XCTAssertFalse(BeatModelClient.isSafeMemberPath("/abs"))
        XCTAssertFalse(BeatModelClient.isSafeMemberPath("../escape"))
        XCTAssertFalse(BeatModelClient.isSafeMemberPath("a/../../b"))
        XCTAssertFalse(BeatModelClient.isSafeMemberPath(""))
    }
}

/// Serves canned bodies keyed by absolute URL string; 404s otherwise.
final class StubURLProtocol: URLProtocol {
    static var responses: [String: Data] = [:]

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        let key = request.url?.absoluteString ?? ""
        if let body = StubURLProtocol.responses[key] {
            let resp = HTTPURLResponse(
                url: request.url!, statusCode: 200,
                httpVersion: "HTTP/1.1", headerFields: nil
            )!
            client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: body)
        } else {
            let resp = HTTPURLResponse(
                url: request.url!, statusCode: 404,
                httpVersion: "HTTP/1.1", headerFields: nil
            )!
            client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
        }
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
