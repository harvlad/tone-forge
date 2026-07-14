// BridgeClientTests.swift
//
// Full bridge state machine against an in-process socket stub:
// hello handshake, joined/version_mismatch transitions, ping->pong,
// reconnect-after-drop, connect_state coalescing, transport_state
// throttling, and inbound intent dispatch. No network.

import XCTest
@testable import JamDesktopCore

// MARK: - Stubs

private final class StubSocket: BridgeSocketConnection, @unchecked Sendable {
    struct Dropped: Error {}

    private let lock = NSLock()
    private var sent: [String] = []
    private var inbound: [String] = []
    private var waiter: CheckedContinuation<String, Error>?
    private(set) var isClosed = false

    func sendText(_ text: String) async throws {
        lock.lock()
        sent.append(text)
        lock.unlock()
    }

    func receiveText() async throws -> String {
        try await withCheckedThrowingContinuation { continuation in
            lock.lock()
            if !inbound.isEmpty {
                let next = inbound.removeFirst()
                lock.unlock()
                continuation.resume(returning: next)
            } else if isClosed {
                lock.unlock()
                continuation.resume(throwing: Dropped())
            } else {
                waiter = continuation
                lock.unlock()
            }
        }
    }

    func close() {
        lock.lock()
        isClosed = true
        let pending = waiter
        waiter = nil
        lock.unlock()
        pending?.resume(throwing: Dropped())
    }

    // Test API

    /// Deliver an inbound frame to the client's receive loop.
    func push(_ json: String) {
        lock.lock()
        if let pending = waiter {
            waiter = nil
            lock.unlock()
            pending.resume(returning: json)
            return
        }
        inbound.append(json)
        lock.unlock()
    }

    var sentTexts: [String] {
        lock.lock()
        defer { lock.unlock() }
        return sent
    }
}

private final class StubConnector: BridgeSocketConnecting, @unchecked Sendable {
    private let lock = NSLock()
    private var sockets: [StubSocket]
    private(set) var openCount = 0

    init(_ sockets: [StubSocket]) { self.sockets = sockets }

    func open(url: URL) async throws -> BridgeSocketConnection {
        lock.lock()
        defer { lock.unlock() }
        openCount += 1
        guard !sockets.isEmpty else { throw StubSocket.Dropped() }
        return sockets.removeFirst()
    }
}

// MARK: - Tests

@MainActor
final class BridgeClientTests: XCTestCase {

    private static let fastConfig: BridgeClientConfig = {
        var config = BridgeClientConfig()
        config.reconnectBaseDelay = 0.02
        config.reconnectMaxDelay = 0.1
        config.connectStateInterval = 0.15
        config.transportStateInterval = 0.15
        return config
    }()

    private func makeClient(
        sockets: [StubSocket]
    ) -> (BridgeClient, StubConnector) {
        let connector = StubConnector(sockets)
        let client = BridgeClient(connector: connector, config: Self.fastConfig)
        return (client, connector)
    }

    private let url = URL(string: "wss://example.test/ws/connect-bridge")!

    @discardableResult
    private func waitUntil(
        timeout: TimeInterval = 2.0,
        _ condition: () -> Bool
    ) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return true }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        return condition()
    }

    private func sentFrames(_ socket: StubSocket) -> [[String: Any]] {
        socket.sentTexts.compactMap {
            try? JSONSerialization.jsonObject(with: Data($0.utf8)) as? [String: Any]
        }
    }

    private func sentTypes(_ socket: StubSocket) -> [String] {
        sentFrames(socket).compactMap { $0["type"] as? String }
    }

    // MARK: Handshake

    func testHelloSentOnConnect() async {
        let socket = StubSocket()
        let (client, _) = makeClient(sockets: [socket])
        client.start(sessionId: "sess-1", url: url)
        defer { client.stop() }

        let sent = await waitUntil { !socket.sentTexts.isEmpty }
        XCTAssertTrue(sent)
        let hello = sentFrames(socket)[0]
        XCTAssertEqual(hello["type"] as? String, "hello")
        XCTAssertEqual(hello["role"] as? String, "connect")
        XCTAssertEqual(hello["client_kind"] as? String, "jam-desktop")
        XCTAssertEqual(hello["protocol_version"] as? Int, 2)
        XCTAssertEqual(hello["session_id"] as? String, "sess-1")
    }

    func testJoinedSetsConnectedWithPeerCount() async {
        let socket = StubSocket()
        let (client, _) = makeClient(sockets: [socket])
        client.start(sessionId: "s", url: url)
        defer { client.stop() }

        socket.push(#"{"type":"joined","peers":2,"session_id":"s"}"#)
        let connected = await waitUntil { client.status == .connected(peers: 2) }
        XCTAssertTrue(connected)
    }

    func testVersionMismatchStopsReconnect() async {
        let socket = StubSocket()
        let (client, connector) = makeClient(sockets: [socket])
        client.start(sessionId: "s", url: url)
        defer { client.stop() }

        socket.push(#"{"type":"version_mismatch","required":3,"client":2}"#)
        let failed = await waitUntil {
            if case .failed = client.status { return true }
            return false
        }
        XCTAssertTrue(failed)

        // Several base delays worth of time: no new connection attempt.
        try? await Task.sleep(nanoseconds: 200_000_000)
        XCTAssertEqual(connector.openCount, 1)
        XCTAssertTrue(socket.isClosed)
    }

    func testPingAnsweredWithPong() async {
        let socket = StubSocket()
        let (client, _) = makeClient(sockets: [socket])
        client.start(sessionId: "s", url: url)
        defer { client.stop() }

        socket.push(#"{"type":"ping","nonce":"abc"}"#)
        let ponged = await waitUntil { self.sentTypes(socket).contains("pong") }
        XCTAssertTrue(ponged)
        let pong = sentFrames(socket).first { $0["type"] as? String == "pong" }
        XCTAssertEqual(pong?["nonce"] as? String, "abc")
    }

    func testReconnectsAfterSocketDrop() async {
        let first = StubSocket()
        let second = StubSocket()
        let (client, connector) = makeClient(sockets: [first, second])
        client.start(sessionId: "s", url: url)
        defer { client.stop() }

        await waitUntil { !first.sentTexts.isEmpty }
        first.close() // simulate server drop

        let reconnected = await waitUntil {
            connector.openCount == 2 && !second.sentTexts.isEmpty
        }
        XCTAssertTrue(reconnected)
        XCTAssertEqual(sentTypes(second).first, "hello")
    }

    func testStopReturnsToIdleAndClosesSocket() async {
        let socket = StubSocket()
        let (client, _) = makeClient(sockets: [socket])
        client.start(sessionId: "s", url: url)
        await waitUntil { !socket.sentTexts.isEmpty }

        client.stop()
        XCTAssertEqual(client.status, .idle)
        XCTAssertTrue(socket.isClosed)
    }

    // MARK: Outbound rate control

    func testConnectStateCoalescesToTrailingLatest() async {
        let socket = StubSocket()
        let (client, _) = makeClient(sockets: [socket])
        client.start(sessionId: "s", url: url)
        defer { client.stop() }
        await waitUntil { !socket.sentTexts.isEmpty }

        client.sendConnectState(ConnectStateFrame(state: "a"))
        client.sendConnectState(ConnectStateFrame(state: "b"))
        client.sendConnectState(ConnectStateFrame(state: "c"))

        // First lands immediately; b is superseded; c flushes trailing.
        let flushed = await waitUntil {
            self.sentTypes(socket).filter { $0 == "connect_state" }.count == 2
        }
        XCTAssertTrue(flushed)
        let states = sentFrames(socket)
            .filter { $0["type"] as? String == "connect_state" }
            .compactMap { $0["state"] as? String }
        XCTAssertEqual(states, ["a", "c"])

        // No extra sends sneak in after the flush.
        try? await Task.sleep(nanoseconds: 200_000_000)
        XCTAssertEqual(
            sentTypes(socket).filter { $0 == "connect_state" }.count, 2)
    }

    func testTransportStateThrottledButDiscreteBypasses() async {
        let socket = StubSocket()
        let (client, _) = makeClient(sockets: [socket])
        client.start(sessionId: "s", url: url)
        defer { client.stop() }
        await waitUntil { !socket.sentTexts.isEmpty }

        // Burst of continuous updates: only the first passes.
        client.sendTransportState(TransportStateFrame(playing: true, positionS: 1))
        client.sendTransportState(TransportStateFrame(playing: true, positionS: 1.01))
        client.sendTransportState(TransportStateFrame(playing: true, positionS: 1.02))
        // Discrete change bypasses the throttle window.
        client.sendTransportState(
            TransportStateFrame(playing: false, positionS: 1.02), discrete: true)

        let done = await waitUntil {
            self.sentTypes(socket).filter { $0 == "transport_state" }.count == 2
        }
        XCTAssertTrue(done)
        let positions = sentFrames(socket)
            .filter { $0["type"] as? String == "transport_state" }
            .compactMap { $0["position_s"] as? Double }
        XCTAssertEqual(positions, [1, 1.02])
    }

    func testSessionDataAndLoadStemsSendImmediately() async {
        let socket = StubSocket()
        let (client, _) = makeClient(sockets: [socket])
        client.start(sessionId: "s", url: url)
        defer { client.stop() }
        await waitUntil { !socket.sentTexts.isEmpty }

        client.sendSessionData(SessionDataFrame(sessionId: "s", bpm: 120))
        client.sendLoadStems(LoadStemsFrame(stems: [
            .init(id: "vocals", url: "https://x/v.m4a", displayName: "Vocals")
        ]))

        let done = await waitUntil {
            let types = self.sentTypes(socket)
            return types.contains("session_data") && types.contains("load_stems")
        }
        XCTAssertTrue(done)
        let stems = sentFrames(socket).first { $0["type"] as? String == "load_stems" }
        let stemList = stems?["stems"] as? [[String: Any]]
        XCTAssertEqual(stemList?.first?["display_name"] as? String, "Vocals")
    }

    // MARK: Inbound dispatch

    func testInboundIntentsDispatchToHandlers() async {
        let socket = StubSocket()
        let (client, _) = makeClient(sockets: [socket])

        var chainIds: [String] = []
        var chainRawEmbeds: [String] = []
        var gains: [Double] = []
        var latencyRequests = 0
        var transports: [TransportStateFrame] = []
        var peerLefts = 0

        client.onApplyChain = { frame, raw in
            chainIds.append(frame.chainId ?? "")
            // Raw JSON must carry the embedded chain spec verbatim.
            if let dict = try? JSONSerialization.jsonObject(with: raw) as? [String: Any],
               let chain = dict["chain"] as? [String: Any] {
                chainRawEmbeds.append(chain["id"] as? String ?? "")
            }
        }
        client.onSetMonitorGain = { gains.append($0) }
        client.onMeasureLatency = { latencyRequests += 1 }
        client.onTransportState = { transports.append($0) }
        client.onPeerLeft = { peerLefts += 1 }

        client.start(sessionId: "s", url: url)
        defer { client.stop() }
        await waitUntil { !socket.sentTexts.isEmpty }

        socket.push(#"{"type":"apply_chain","chain_id":"clean_di","request_id":"r1","chain":{"id":"clean_di","family":"clean","parameters":{"input":{"gain_db":0}}}}"#)
        socket.push(#"{"type":"set_gain","gain":0.5}"#)
        socket.push(#"{"type":"set_monitor_gain","gain":0.8}"#)
        socket.push(#"{"type":"measure_latency","v":2}"#)
        socket.push(#"{"type":"transport_state","playing":true,"position_s":3.5}"#)
        socket.push(#"{"type":"peer_left","reason":"heartbeat_timeout","peers":1}"#)

        let done = await waitUntil { peerLefts == 1 }
        XCTAssertTrue(done)
        XCTAssertEqual(chainIds, ["clean_di"])
        XCTAssertEqual(chainRawEmbeds, ["clean_di"])
        XCTAssertEqual(gains, [0.5, 0.8])
        XCTAssertEqual(latencyRequests, 1)
        XCTAssertEqual(transports.first?.playing, true)
        XCTAssertEqual(transports.first?.positionS, 3.5)
    }

    func testApplyChainSendAndAckRoundTrip() async {
        let socket = StubSocket()
        let (client, _) = makeClient(sockets: [socket])

        var ackRequestIds: [String] = []
        var ackChainEmbeds: [String] = []
        client.onAck = { ack, raw in
            ackRequestIds.append(ack.requestId ?? "")
            if let dict = try? JSONSerialization.jsonObject(with: raw) as? [String: Any],
               let chain = dict["chain"] as? [String: Any] {
                ackChainEmbeds.append(chain["id"] as? String ?? "")
            }
        }

        client.start(sessionId: "s", url: url)
        defer { client.stop() }
        await waitUntil { !socket.sentTexts.isEmpty }

        let rid = client.sendApplyChain(chainId: "tfc.classic_rock")
        XCTAssertTrue(rid.hasPrefix("apply_"))

        let sent = await waitUntil {
            self.sentTypes(socket).contains("apply_chain")
        }
        XCTAssertTrue(sent)
        let frame = sentFrames(socket).first { $0["type"] as? String == "apply_chain" }
        XCTAssertEqual(frame?["chain_id"] as? String, "tfc.classic_rock")
        XCTAssertEqual(frame?["request_id"] as? String, rid)

        // Server ack carries the resolved spec back to the sender.
        socket.push(
            #"{"type":"ack","request_id":"\#(rid)","chain_id":"tfc.classic_rock","chain":{"id":"tfc.classic_rock","parameters":{"input":{"gain_db":2}}}}"#)
        let acked = await waitUntil { !ackRequestIds.isEmpty }
        XCTAssertTrue(acked)
        XCTAssertEqual(ackRequestIds, [rid])
        XCTAssertEqual(ackChainEmbeds, ["tfc.classic_rock"])
    }

    func testUnknownFramesIgnoredWithoutBreakingLoop() async {
        let socket = StubSocket()
        let (client, _) = makeClient(sockets: [socket])
        client.start(sessionId: "s", url: url)
        defer { client.stop() }
        await waitUntil { !socket.sentTexts.isEmpty }

        socket.push(#"{"type":"totally_new_frame","payload":1}"#)
        socket.push(#"{"type":"joined","peers":1}"#)
        let stillAlive = await waitUntil { client.status == .connected(peers: 1) }
        XCTAssertTrue(stillAlive)
    }

    // MARK: URL derivation

    func testBridgeURLDerivation() {
        XCTAssertEqual(
            BridgeClient.bridgeURL(
                backendBaseURL: URL(string: "https://jamn.app")!).absoluteString,
            "wss://jamn.app/ws/connect-bridge")
        XCTAssertEqual(
            BridgeClient.bridgeURL(
                backendBaseURL: URL(string: "http://127.0.0.1:8000")!).absoluteString,
            "ws://127.0.0.1:8000/ws/connect-bridge")
    }
}
