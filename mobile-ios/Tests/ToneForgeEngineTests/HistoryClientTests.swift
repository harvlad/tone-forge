// HistoryClientTests.swift
//
// URLProtocol-mock coverage for `HistoryClient.fetch`:
//   - Wrapper `{ "history": [...] }` decodes into `[HistoryEntry]`.
//   - `limit` and `q` are attached to the query.
//   - nil / empty `q` is omitted.
//   - Non-2xx status surfaces as `HistoryClientError.httpStatus`.
//
// Same URLProtocol shim shape as `ChopsClientTests` — kept private to
// this file to avoid coupling test targets.

import XCTest
@testable import ToneForgeEngine

final class HistoryClientTests: XCTestCase {

    private var session: URLSession!

    override func setUp() {
        super.setUp()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [HistoryMockProtocol.self]
        session = URLSession(configuration: config)
        HistoryMockProtocol.reset()
    }

    override func tearDown() {
        HistoryMockProtocol.reset()
        session = nil
        super.tearDown()
    }

    // MARK: - Decoding

    func testFetchDecodesHistoryWrapper() async throws {
        let entries = [
            HistoryEntry(id: "song-1", timestamp: "2025-01-01T00:00:00Z",
                         name: "Track One", detectedType: "guitar",
                         summary: "warm blues", duration: 187.4,
                         ampFamily: "fender"),
            HistoryEntry(id: "song-2", timestamp: "2025-01-02T00:00:00Z",
                         name: "Track Two"),
        ]
        HistoryMockProtocol.stub(response: .success(history: entries))

        let client = HistoryClient(session: session)
        let result = try await client.fetch(
            baseURL: URL(string: "https://example.com")!,
            query: "guitar",
            limit: 10
        )

        XCTAssertEqual(result.count, 2)
        XCTAssertEqual(result[0].id, "song-1")
        XCTAssertEqual(result[0].ampFamily, "fender")
        XCTAssertEqual(result[1].name, "Track Two")
    }

    // MARK: - URL construction

    func testFetchAppendsQueryAndLimit() async throws {
        HistoryMockProtocol.stub(response: .success(history: []))

        let client = HistoryClient(session: session)
        _ = try await client.fetch(
            baseURL: URL(string: "https://example.com")!,
            query: "blues",
            limit: 25
        )

        let seen = try XCTUnwrap(HistoryMockProtocol.lastRequest?.url)
        XCTAssertEqual(seen.path, "/api/history")
        let comps = URLComponents(url: seen, resolvingAgainstBaseURL: false)
        let query = Dictionary(
            uniqueKeysWithValues: (comps?.queryItems ?? []).map { ($0.name, $0.value ?? "") }
        )
        XCTAssertEqual(query["q"], "blues")
        XCTAssertEqual(query["limit"], "25")
    }

    func testFetchOmitsEmptyQuery() async throws {
        HistoryMockProtocol.stub(response: .success(history: []))

        let client = HistoryClient(session: session)
        _ = try await client.fetch(
            baseURL: URL(string: "https://example.com")!,
            query: nil,
            limit: 50
        )

        let seen = try XCTUnwrap(HistoryMockProtocol.lastRequest?.url)
        let comps = URLComponents(url: seen, resolvingAgainstBaseURL: false)
        let names = (comps?.queryItems ?? []).map { $0.name }
        XCTAssertFalse(names.contains("q"))
        XCTAssertTrue(names.contains("limit"))
    }

    // MARK: - HTTP error surface

    func testFetchSurfacesHttpErrorStatus() async {
        HistoryMockProtocol.stub(response: .httpStatus(503))

        let client = HistoryClient(session: session)
        do {
            _ = try await client.fetch(baseURL: URL(string: "https://example.com")!)
            XCTFail("expected .httpStatus")
        } catch let HistoryClientError.httpStatus(code) {
            XCTAssertEqual(code, 503)
        } catch {
            XCTFail("expected HistoryClientError.httpStatus, got \(error)")
        }
    }
}

// MARK: - Mock protocol

private final class HistoryMockProtocol: URLProtocol {

    enum Stub {
        case success(history: [HistoryEntry])
        case httpStatus(Int)
    }

    nonisolated(unsafe) static var currentStub: Stub?
    nonisolated(unsafe) static var lastRequest: URLRequest?

    static func stub(response: Stub) { currentStub = response }
    static func reset() { currentStub = nil; lastRequest = nil }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        HistoryMockProtocol.lastRequest = request
        guard let stub = HistoryMockProtocol.currentStub else {
            let error = NSError(domain: "HistoryMockProtocol", code: -1,
                                userInfo: [NSLocalizedDescriptionKey: "no stub"])
            client?.urlProtocol(self, didFailWithError: error)
            return
        }
        let url = request.url!
        switch stub {
        case .success(let entries):
            struct Wrapper: Encodable { let history: [HistoryEntry] }
            let body = try! JSONEncoder().encode(Wrapper(history: entries))
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
        }
    }

    override func stopLoading() {}
}
