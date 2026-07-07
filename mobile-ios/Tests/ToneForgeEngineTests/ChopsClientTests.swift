// ChopsClientTests.swift
//
// URLProtocol-mock coverage for `ChopsClient.fetchChops`:
//   - Happy path decodes wrapper `{ "chops": [...] }` → `[Chop]`.
//   - Query params `stem` + `sliceMode` are appended correctly.
//   - nil / empty stem or sliceMode omitted from the query.
//   - Non-2xx status surfaces as `ChopsClientError.httpStatus`.
//   - Path is `/api/song/{id}/chops` under whatever baseURL is passed.
//
// The mock installs a single URLProtocol on a private URLSession,
// returning canned data / status codes per URL.

import XCTest
@testable import ToneForgeEngine

final class ChopsClientTests: XCTestCase {

    private var session: URLSession!

    override func setUp() {
        super.setUp()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        session = URLSession(configuration: config)
        MockURLProtocol.reset()
    }

    override func tearDown() {
        MockURLProtocol.reset()
        session = nil
        super.tearDown()
    }

    // MARK: - Happy path

    func testFetchChopsDecodesWrapper() async throws {
        let chops = [
            Chop(idx: 0, startSec: 0, endSec: 1, durationSec: 1, chordSymbol: "C"),
            Chop(idx: 1, startSec: 1, endSec: 2, durationSec: 1, chordSymbol: "Am"),
        ]
        MockURLProtocol.stub(response: .success(chops: chops))

        let client = ChopsClient(session: session)
        let result = try await client.fetchChops(
            baseURL: URL(string: "https://example.com")!,
            analysisId: "song-abc",
            stem: "vocals",
            sliceMode: "chord"
        )

        XCTAssertEqual(result.count, 2)
        XCTAssertEqual(result[0].chordSymbol, "C")
        XCTAssertEqual(result[1].idx, 1)
    }

    // MARK: - URL construction

    func testFetchChopsBuildsCanonicalPath() async throws {
        MockURLProtocol.stub(response: .success(chops: []))

        let client = ChopsClient(session: session)
        _ = try await client.fetchChops(
            baseURL: URL(string: "https://example.com")!,
            analysisId: "song-abc",
            stem: "drums",
            sliceMode: "beat"
        )

        let seen = try XCTUnwrap(MockURLProtocol.lastRequest?.url)
        XCTAssertEqual(seen.path, "/api/song/song-abc/chops")
        let comps = URLComponents(url: seen, resolvingAgainstBaseURL: false)
        let query = Dictionary(
            uniqueKeysWithValues: (comps?.queryItems ?? []).map { ($0.name, $0.value ?? "") }
        )
        XCTAssertEqual(query["stem"], "drums")
        XCTAssertEqual(query["sliceMode"], "beat")
    }

    func testFetchChopsOmitsNilAndEmptyQueryParams() async throws {
        MockURLProtocol.stub(response: .success(chops: []))

        let client = ChopsClient(session: session)
        _ = try await client.fetchChops(
            baseURL: URL(string: "https://example.com")!,
            analysisId: "song-1",
            stem: nil,
            sliceMode: ""
        )

        let seen = try XCTUnwrap(MockURLProtocol.lastRequest?.url)
        let comps = URLComponents(url: seen, resolvingAgainstBaseURL: false)
        XCTAssertNil(comps?.queryItems, "expected no query items when both params nil/empty")
    }

    // MARK: - HTTP error surface

    func testFetchChopsSurfacesHttpErrorStatus() async {
        MockURLProtocol.stub(response: .httpStatus(500))

        let client = ChopsClient(session: session)
        do {
            _ = try await client.fetchChops(
                baseURL: URL(string: "https://example.com")!,
                analysisId: "song-1",
                stem: "vocals",
                sliceMode: "chord"
            )
            XCTFail("expected .httpStatus to throw")
        } catch let ChopsClientError.httpStatus(code) {
            XCTAssertEqual(code, 500)
        } catch {
            XCTFail("expected ChopsClientError.httpStatus, got \(error)")
        }
    }

    func testFetchChopsMalformedJSONThrowsDecodingError() async {
        MockURLProtocol.stub(response: .rawBody(Data("{ not json".utf8), status: 200))

        let client = ChopsClient(session: session)
        do {
            _ = try await client.fetchChops(
                baseURL: URL(string: "https://example.com")!,
                analysisId: "song-1"
            )
            XCTFail("expected decode error")
        } catch is DecodingError {
            // expected
        } catch {
            XCTFail("expected DecodingError, got \(error)")
        }
    }
}

// MARK: - MockURLProtocol

/// Minimal URLProtocol shim: canned response per test. Not thread-safe;
/// tests are single-threaded.
private final class MockURLProtocol: URLProtocol {

    enum Stub {
        case success(chops: [Chop])
        case httpStatus(Int)
        case rawBody(Data, status: Int)
    }

    nonisolated(unsafe) static var currentStub: Stub?
    nonisolated(unsafe) static var lastRequest: URLRequest?

    static func stub(response: Stub) {
        currentStub = response
    }

    static func reset() {
        currentStub = nil
        lastRequest = nil
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        MockURLProtocol.lastRequest = request

        guard let stub = MockURLProtocol.currentStub else {
            let error = NSError(domain: "MockURLProtocol", code: -1,
                                userInfo: [NSLocalizedDescriptionKey: "no stub"])
            client?.urlProtocol(self, didFailWithError: error)
            return
        }

        let url = request.url!
        switch stub {
        case .success(let chops):
            struct Wrapper: Encodable { let chops: [Chop] }
            let body = try! JSONEncoder().encode(Wrapper(chops: chops))
            let resp = HTTPURLResponse(url: url, statusCode: 200,
                                      httpVersion: "HTTP/1.1", headerFields: nil)!
            client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: body)
            client?.urlProtocolDidFinishLoading(self)
        case .httpStatus(let code):
            let resp = HTTPURLResponse(url: url, statusCode: code,
                                      httpVersion: "HTTP/1.1", headerFields: nil)!
            client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data())
            client?.urlProtocolDidFinishLoading(self)
        case .rawBody(let body, let status):
            let resp = HTTPURLResponse(url: url, statusCode: status,
                                      httpVersion: "HTTP/1.1", headerFields: nil)!
            client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: body)
            client?.urlProtocolDidFinishLoading(self)
        }
    }

    override func stopLoading() {}
}
