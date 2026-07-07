// LayerClientTests.swift
//
// URLProtocol-mock coverage for `LayerClient`:
//   - upload builds POST /api/song/{id}/layers with a JSON body
//     that round-trips into LayerTimeline on the server.
//   - upload decodes the `{ layerId, analysisId, url }` ack.
//   - list decodes `{ layers: [LayerSummary] }`.
//   - fetch decodes a full LayerTimeline body.
//   - Non-2xx responses surface via `LayerClientError.httpStatus`
//     including the FastAPI `detail` field when present.

import XCTest
@testable import ToneForgeEngine

final class LayerClientTests: XCTestCase {

    private var session: URLSession!

    override func setUp() {
        super.setUp()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [LayerMockProtocol.self]
        session = URLSession(configuration: config)
        LayerMockProtocol.reset()
    }

    override func tearDown() {
        LayerMockProtocol.reset()
        session = nil
        super.tearDown()
    }

    // MARK: - Fixtures

    private func makeTimeline(
        layerId: String = "layer-1",
        analysisId: String = "song-abc"
    ) -> LayerTimeline {
        LayerTimeline(
            layerId: layerId,
            analysisId: analysisId,
            name: "Test layer",
            createdAtEpoch: 1_700_000_000,
            durationSec: 12.0,
            events: [
                LayerEvent(
                    kind: .sampleOn,
                    songTimeSec: 0.1,
                    params: LayerEvent.Params(padIdx: 2, velocity: 1.0)
                )
            ],
            activePackId: "starter"
        )
    }

    // MARK: - upload

    func testUploadPostsTimelineAndDecodesAck() async throws {
        LayerMockProtocol.stub(response: .uploadAck(
            layerId: "layer-1",
            analysisId: "song-abc",
            url: "/api/song/song-abc/layers/layer-1"
        ))

        let client = LayerClient(session: session)
        let ack = try await client.upload(
            baseURL: URL(string: "https://example.com/")!,
            timeline: makeTimeline()
        )
        XCTAssertEqual(ack.layerId, "layer-1")
        XCTAssertEqual(ack.analysisId, "song-abc")
        XCTAssertTrue(ack.url.hasSuffix("/layer-1"))

        let request = try XCTUnwrap(LayerMockProtocol.lastRequest)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.path, "/api/song/song-abc/layers")
        XCTAssertEqual(
            request.value(forHTTPHeaderField: "Content-Type"),
            "application/json"
        )
        // The body must round-trip through LayerTimeline so the backend
        // sees exactly the same schema the local disk store writes.
        let body = try XCTUnwrap(LayerMockProtocol.lastBody)
        let decoded = try JSONDecoder().decode(LayerTimeline.self, from: body)
        XCTAssertEqual(decoded.layerId, "layer-1")
        XCTAssertEqual(decoded.events.count, 1)
    }

    func testUploadSurfacesHttpErrorWithDetail() async {
        LayerMockProtocol.stub(response: .httpError(
            status: 400,
            detail: "Layer analysisId does not match URL"
        ))

        let client = LayerClient(session: session)
        do {
            _ = try await client.upload(
                baseURL: URL(string: "https://example.com/")!,
                timeline: makeTimeline()
            )
            XCTFail("expected error")
        } catch let LayerClientError.httpStatus(code, msg) {
            XCTAssertEqual(code, 400)
            XCTAssertEqual(msg, "Layer analysisId does not match URL")
        } catch {
            XCTFail("wrong error: \(error)")
        }
    }

    // MARK: - list

    func testListDecodesSummaries() async throws {
        let summaries = [
            LayerSummary(
                layerId: "a", name: "Alpha",
                createdAtEpoch: 2000, durationSec: 10, eventCount: 4,
                activePackId: "starter"
            ),
            LayerSummary(
                layerId: "b", name: "Bravo",
                createdAtEpoch: 1000, durationSec: 6, eventCount: 2,
                activePackId: nil
            ),
        ]
        LayerMockProtocol.stub(response: .listOk(summaries: summaries))

        let client = LayerClient(session: session)
        let result = try await client.list(
            baseURL: URL(string: "https://example.com/")!,
            analysisId: "song-abc"
        )
        XCTAssertEqual(result.count, 2)
        XCTAssertEqual(result[0].layerId, "a")
        XCTAssertEqual(result[1].eventCount, 2)
        XCTAssertNil(result[1].activePackId)

        let seen = try XCTUnwrap(LayerMockProtocol.lastRequest?.url)
        XCTAssertEqual(seen.path, "/api/song/song-abc/layers")
    }

    // MARK: - fetch

    func testFetchDecodesFullTimeline() async throws {
        LayerMockProtocol.stub(response: .fetchOk(timeline: makeTimeline()))

        let client = LayerClient(session: session)
        let tl = try await client.fetch(
            baseURL: URL(string: "https://example.com/")!,
            analysisId: "song-abc",
            layerId: "layer-1"
        )
        XCTAssertEqual(tl.layerId, "layer-1")
        XCTAssertEqual(tl.events.first?.kind, .sampleOn)

        let seen = try XCTUnwrap(LayerMockProtocol.lastRequest?.url)
        XCTAssertEqual(seen.path, "/api/song/song-abc/layers/layer-1")
    }

    func testFetchMissingLayerSurfaces404() async {
        LayerMockProtocol.stub(response: .httpError(status: 404, detail: "Layer not found"))

        let client = LayerClient(session: session)
        do {
            _ = try await client.fetch(
                baseURL: URL(string: "https://example.com/")!,
                analysisId: "song-abc",
                layerId: "missing"
            )
            XCTFail("expected error")
        } catch let LayerClientError.httpStatus(code, _) {
            XCTAssertEqual(code, 404)
        } catch {
            XCTFail("wrong error: \(error)")
        }
    }
}

// MARK: - Mock protocol

private final class LayerMockProtocol: URLProtocol {

    enum Stub {
        case uploadAck(layerId: String, analysisId: String, url: String)
        case listOk(summaries: [LayerSummary])
        case fetchOk(timeline: LayerTimeline)
        case httpError(status: Int, detail: String?)
    }

    nonisolated(unsafe) static var currentStub: Stub?
    nonisolated(unsafe) static var lastRequest: URLRequest?
    nonisolated(unsafe) static var lastBody: Data?

    static func stub(response: Stub) { currentStub = response }
    static func reset() {
        currentStub = nil
        lastRequest = nil
        lastBody = nil
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        LayerMockProtocol.lastRequest = request
        // URLSession streams the httpBody into the request via
        // `httpBodyStream` for POST — read it here so tests can assert
        // on the actual bytes sent.
        if let stream = request.httpBodyStream {
            stream.open()
            defer { stream.close() }
            var data = Data()
            let bufSize = 1024
            var buf = [UInt8](repeating: 0, count: bufSize)
            while stream.hasBytesAvailable {
                let n = stream.read(&buf, maxLength: bufSize)
                if n <= 0 { break }
                data.append(buf, count: n)
            }
            LayerMockProtocol.lastBody = data
        } else if let body = request.httpBody {
            LayerMockProtocol.lastBody = body
        }

        guard let stub = LayerMockProtocol.currentStub else {
            client?.urlProtocol(self, didFailWithError: NSError(
                domain: "LayerMockProtocol", code: -1,
                userInfo: [NSLocalizedDescriptionKey: "no stub"]))
            return
        }
        let url = request.url!

        switch stub {
        case .uploadAck(let layerId, let analysisId, let ackUrl):
            struct Ack: Encodable {
                let layerId: String
                let analysisId: String
                let url: String
            }
            let body = try! JSONEncoder().encode(
                Ack(layerId: layerId, analysisId: analysisId, url: ackUrl)
            )
            respond(url: url, status: 200, body: body)

        case .listOk(let summaries):
            struct Wrapper: Encodable {
                let analysisId: String
                let layers: [LayerSummary]
            }
            let body = try! JSONEncoder().encode(
                Wrapper(analysisId: "song-abc", layers: summaries)
            )
            respond(url: url, status: 200, body: body)

        case .fetchOk(let timeline):
            let body = try! JSONEncoder().encode(timeline)
            respond(url: url, status: 200, body: body)

        case .httpError(let status, let detail):
            struct DetailBody: Encodable { let detail: String }
            let body = (detail.flatMap {
                try? JSONEncoder().encode(DetailBody(detail: $0))
            }) ?? Data()
            respond(url: url, status: status, body: body)
        }
    }

    private func respond(url: URL, status: Int, body: Data) {
        let resp = HTTPURLResponse(url: url, statusCode: status,
                                   httpVersion: "HTTP/1.1", headerFields: nil)!
        client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
