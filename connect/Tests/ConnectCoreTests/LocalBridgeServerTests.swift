//
// LocalBridgeServerTests.swift
//
// Coverage for the loopback WS listener's three security gates and the
// happy path into PresetBridge.dispatch:
//   * Origin allowlist — pure validator + a real socket that presents
//     a hostile Origin and must never complete the app-level handshake.
//   * Session-token hello — pure validator + a real socket that passes
//     the Origin gate but presents the wrong session id and gets
//     dropped without a hello_ack.
//   * Happy path — allowed Origin + matching hello ⇒ hello_ack with
//     transport=local, then a set_gain frame that must surface through
//     the SAME PresetBridge.onGainChange callback the relay path uses,
//     and a telemetry broadcast that must reach the local peer.
//
// Integration tests bind port 0 (ephemeral) so parallel/repeated runs
// can't collide with a real Connect instance squatting 17995.
//

import XCTest
import Network
@testable import ConnectCore

final class LocalBridgeServerTests: XCTestCase {

    // MARK: - Pure validators

    func testOriginAllowlist() {
        XCTAssertTrue(LocalBridgeServer.isOriginAllowed("https://jamn.app"))
        XCTAssertTrue(LocalBridgeServer.isOriginAllowed("http://127.0.0.1:8000"))
        XCTAssertTrue(LocalBridgeServer.isOriginAllowed("http://localhost:8000"))
        // Any other origin — including lookalikes and subdomains — is out.
        XCTAssertFalse(LocalBridgeServer.isOriginAllowed("https://evil.example"))
        XCTAssertFalse(LocalBridgeServer.isOriginAllowed("https://jamn.app.evil.example"))
        XCTAssertFalse(LocalBridgeServer.isOriginAllowed("https://sub.jamn.app"))
        XCTAssertFalse(LocalBridgeServer.isOriginAllowed("http://jamn.app"))
        // Missing Origin (non-browser client) is rejected too.
        XCTAssertFalse(LocalBridgeServer.isOriginAllowed(nil))
        XCTAssertFalse(LocalBridgeServer.isOriginAllowed(""))
    }

    func testHelloValidator() {
        let good: [String: Any] = ["type": "hello", "session_id": "s1", "role": "browser"]
        XCTAssertTrue(LocalBridgeServer.isValidHello(good, expectedSessionId: "s1"))
        XCTAssertFalse(LocalBridgeServer.isValidHello(good, expectedSessionId: "other"))
        XCTAssertFalse(LocalBridgeServer.isValidHello(["type": "set_gain", "gain": 0.5], expectedSessionId: "s1"))
        XCTAssertFalse(LocalBridgeServer.isValidHello(["type": "hello"], expectedSessionId: "s1"))
        XCTAssertFalse(LocalBridgeServer.isValidHello(["type": "hello", "session_id": ""], expectedSessionId: ""))
    }

    // MARK: - Socket-level helpers

    private func startServer(sessionId: String) -> LocalBridgeServer? {
        let server = LocalBridgeServer(port: 0, sessionId: sessionId)
        server.start()
        // Poll for the ephemeral bind to complete. stateUpdateHandler
        // sets `port` on .ready.
        let deadline = Date().addingTimeInterval(3.0)
        while server.port == 0 && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        }
        return server.port != 0 ? server : nil
    }

    /// Minimal WS test client. Collects decoded JSON frames and flags
    /// connection death so tests can assert on either.
    private final class TestClient {
        let connection: NWConnection
        let queue = DispatchQueue(label: "test.local-bridge-client")
        var frames: [[String: Any]] = []
        var died = false
        var onFrame: (([String: Any]) -> Void)?
        var onDeath: (() -> Void)?

        init(port: UInt16, origin: String?) {
            let params = NWParameters.tcp
            let ws = NWProtocolWebSocket.Options()
            ws.autoReplyPing = true
            if let origin = origin {
                ws.setAdditionalHeaders([("Origin", origin)])
            }
            params.defaultProtocolStack.applicationProtocols.insert(ws, at: 0)
            // A WS client over Network.framework must dial a .url
            // endpoint — with a plain hostPort endpoint the upgrade
            // request never goes out and the connection aborts
            // (verified empirically; errno 53 on the client side).
            connection = NWConnection(
                to: NWEndpoint.url(URL(string: "ws://127.0.0.1:\(port)")!),
                using: params
            )
        }

        func start(onReady: (() -> Void)? = nil) {
            connection.stateUpdateHandler = { [weak self] state in
                switch state {
                case .ready:
                    onReady?()
                case .failed, .cancelled:
                    self?.died = true
                    self?.onDeath?()
                default:
                    break
                }
            }
            receiveLoop()
            connection.start(queue: queue)
        }

        private func receiveLoop() {
            connection.receiveMessage { [weak self] data, _, _, error in
                guard let self = self else { return }
                if error != nil {
                    self.died = true
                    self.onDeath?()
                    return
                }
                if let data = data,
                   let obj = try? JSONSerialization.jsonObject(with: data),
                   let dict = obj as? [String: Any] {
                    self.frames.append(dict)
                    self.onFrame?(dict)
                }
                self.receiveLoop()
            }
        }

        func sendJSON(_ dict: [String: Any]) {
            let data = try! JSONSerialization.data(withJSONObject: dict)
            let meta = NWProtocolWebSocket.Metadata(opcode: .text)
            let ctx = NWConnection.ContentContext(identifier: "text", metadata: [meta])
            connection.send(content: data, contentContext: ctx,
                            isComplete: true,
                            completion: .contentProcessed { _ in })
        }

        func cancel() { connection.cancel() }
    }

    // MARK: - Integration: Origin rejection

    func testHostileOriginNeverCompletesHandshake() throws {
        guard let server = startServer(sessionId: "sess-origin") else {
            XCTFail("listener failed to bind ephemeral port"); return
        }
        defer { server.stop() }

        let client = TestClient(port: server.port, origin: "https://evil.example")
        let dead = expectation(description: "connection rejected")
        dead.assertForOverFulfill = false
        client.onDeath = { dead.fulfill() }
        client.start(onReady: {
            // Even if the TCP layer reports ready before the WS
            // upgrade concludes, a valid-looking hello must go
            // unanswered — the upgrade was rejected.
            client.sendJSON(["type": "hello", "role": "browser",
                             "session_id": "sess-origin", "protocol_version": 2])
        })
        wait(for: [dead], timeout: 5.0)
        XCTAssertTrue(client.frames.isEmpty,
                      "hostile origin must never receive a frame, got \(client.frames)")
        client.cancel()
    }

    // MARK: - Integration: bad session token

    func testWrongSessionTokenIsDropped() throws {
        guard let server = startServer(sessionId: "sess-token") else {
            XCTFail("listener failed to bind ephemeral port"); return
        }
        defer { server.stop() }

        let client = TestClient(port: server.port, origin: "https://jamn.app")
        let dead = expectation(description: "connection dropped after bad hello")
        dead.assertForOverFulfill = false
        client.onDeath = { dead.fulfill() }
        client.start(onReady: {
            client.sendJSON(["type": "hello", "role": "browser",
                             "session_id": "WRONG", "protocol_version": 2])
        })
        wait(for: [dead], timeout: 5.0)
        XCTAssertFalse(client.frames.contains { ($0["type"] as? String) == "hello_ack" },
                       "bad token must not be acked")
        client.cancel()
    }

    // MARK: - Integration: happy path through PresetBridge.dispatch

    func testHelloThenSetGainDispatchesAndTelemetryMirrors() throws {
        // Relay never started — PresetBridge here is only the dispatch
        // core + telemetry mirror, exactly the wiring AppDelegate does.
        let bridge = PresetBridge(sessionId: "sess-happy")
        let server = LocalBridgeServer(port: 0, sessionId: "sess-happy")
        bridge.attachLocalBridge(server)
        defer { bridge.stop() }

        let deadline = Date().addingTimeInterval(3.0)
        while server.port == 0 && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        }
        XCTAssertNotEqual(server.port, 0, "listener failed to bind")

        let gotGain = expectation(description: "set_gain dispatched to onGainChange")
        var receivedGain: Float = -1
        bridge.onGainChange = { gain in
            receivedGain = gain
            gotGain.fulfill()
        }

        let gotAck = expectation(description: "hello_ack with transport=local")
        let gotMeter = expectation(description: "input_meter broadcast reaches local peer")
        let client = TestClient(port: server.port, origin: "https://jamn.app")
        client.onFrame = { dict in
            let type = dict["type"] as? String
            if type == "hello_ack" {
                XCTAssertEqual(dict["transport"] as? String, "local")
                XCTAssertEqual(dict["protocol_version"] as? Int, ConnectProtocol.version)
                gotAck.fulfill()
                // Authenticated — now exercise the browser→Connect
                // direction with the same frame jam.js sends.
                client.sendJSON(["type": "set_gain", "gain": 0.42])
            } else if type == "input_meter" {
                gotMeter.fulfill()
            }
        }
        client.start(onReady: {
            client.sendJSON(["type": "hello", "role": "browser",
                             "session_id": "sess-happy", "protocol_version": 2,
                             "transport": "local"])
        })

        wait(for: [gotAck, gotGain], timeout: 5.0)
        XCTAssertEqual(receivedGain, 0.42, accuracy: 0.0001)

        // Connect→browser direction: telemetry broadcast must reach
        // the authenticated local peer (sendInputMeter has no relay
        // task here, so local mirroring is the only route).
        bridge.sendInputMeter(peakDbfs: -12.0, rmsDbfs: -20.0)
        wait(for: [gotMeter], timeout: 5.0)
        client.cancel()
    }
}
