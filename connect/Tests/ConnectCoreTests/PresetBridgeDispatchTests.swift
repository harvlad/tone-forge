//
// PresetBridgeDispatchTests.swift
//
// Exercises the inbound-frame dispatcher of PresetBridge without
// standing up a real WebSocket. We rely on `dispatch(_:)` being
// internal (via @testable import ConnectCore) so the test can call
// it directly with synthesized JSON-equivalent dictionaries.
//
// Callbacks fire on the main queue. Tests use XCTestExpectation +
// short-timeout waits rather than DispatchQueue.main.sync, which
// would deadlock the main-queue dispatch inside the implementation.
//

import XCTest
@testable import ConnectCore

final class PresetBridgeDispatchTests: XCTestCase {

    private func makeBridge() -> PresetBridge {
        // URL is never opened: dispatch() is pure and reads no socket.
        return PresetBridge(
            serverURL: URL(string: "ws://127.0.0.1:1/")!,
            sessionId: "test"
        )
    }

    func testPresetPushFiresCallbackWithPayload() {
        let bridge = makeBridge()
        let exp = expectation(description: "preset push delivered")
        bridge.onPresetPush = { preset in
            XCTAssertEqual(preset["analysis_id"] as? String, "abc-123")
            XCTAssertEqual(preset["instrument"] as? String, "guitar")
            exp.fulfill()
        }
        bridge.dispatch([
            "type": "preset_push",
            "preset": [
                "analysis_id": "abc-123",
                "instrument": "guitar",
            ],
        ])
        wait(for: [exp], timeout: 1.0)
    }

    func testPresetPushWithoutPresetKeyIsIgnored() {
        let bridge = makeBridge()
        bridge.onPresetPush = { _ in
            XCTFail("callback fired on malformed frame")
        }
        bridge.dispatch(["type": "preset_push"])
        // Give the main queue a beat to drain any spurious dispatch.
        let drain = expectation(description: "drain")
        DispatchQueue.main.async { drain.fulfill() }
        wait(for: [drain], timeout: 1.0)
    }

    func testSetGainAcceptsDouble() {
        let bridge = makeBridge()
        let exp = expectation(description: "gain delivered")
        bridge.onGainChange = { gain in
            XCTAssertEqual(gain, 0.5, accuracy: 0.0001)
            exp.fulfill()
        }
        bridge.dispatch(["type": "set_gain", "gain": Double(0.5)])
        wait(for: [exp], timeout: 1.0)
    }

    func testSetGainAcceptsNSNumber() {
        let bridge = makeBridge()
        let exp = expectation(description: "gain delivered")
        bridge.onGainChange = { gain in
            XCTAssertEqual(gain, 0.25, accuracy: 0.0001)
            exp.fulfill()
        }
        bridge.dispatch(["type": "set_gain", "gain": NSNumber(value: 0.25)])
        wait(for: [exp], timeout: 1.0)
    }

    /// Values outside [0, 1] must be clamped before reaching the
    /// callback. The server clamps too, but we double-clamp because
    /// a future malformed broadcast is cheaper to defang here than
    /// to chase a runaway gain into the audio graph.
    func testSetGainClampsAboveOne() {
        let bridge = makeBridge()
        let exp = expectation(description: "gain delivered")
        bridge.onGainChange = { gain in
            XCTAssertEqual(gain, 1.0, accuracy: 0.0001)
            exp.fulfill()
        }
        bridge.dispatch(["type": "set_gain", "gain": Double(5.0)])
        wait(for: [exp], timeout: 1.0)
    }

    func testSetGainClampsBelowZero() {
        let bridge = makeBridge()
        let exp = expectation(description: "gain delivered")
        bridge.onGainChange = { gain in
            XCTAssertEqual(gain, 0.0, accuracy: 0.0001)
            exp.fulfill()
        }
        bridge.dispatch(["type": "set_gain", "gain": Double(-0.5)])
        wait(for: [exp], timeout: 1.0)
    }

    func testSetGainWithoutNumericValueDoesNotFire() {
        let bridge = makeBridge()
        bridge.onGainChange = { _ in
            XCTFail("callback fired on non-numeric gain")
        }
        bridge.dispatch(["type": "set_gain", "gain": "loud"])
        let drain = expectation(description: "drain")
        DispatchQueue.main.async { drain.fulfill() }
        wait(for: [drain], timeout: 1.0)
    }

    func testVersionMismatchFiresCallbackWithRequiredVersion() {
        let bridge = makeBridge()
        let exp = expectation(description: "mismatch delivered")
        bridge.onVersionMismatch = { required in
            XCTAssertEqual(required, 7)
            exp.fulfill()
        }
        bridge.dispatch([
            "type": ConnectProtocol.MessageType.versionMismatch,
            "required": 7,
        ])
        wait(for: [exp], timeout: 1.0)
    }

    func testHelloAckIsTolerated() {
        // hello_ack is a status-only frame; we just want to confirm it
        // doesn't trip any callback or crash the dispatcher.
        let bridge = makeBridge()
        bridge.onPresetPush = { _ in XCTFail("preset callback on hello_ack") }
        bridge.onGainChange = { _ in XCTFail("gain callback on hello_ack") }
        bridge.onVersionMismatch = { _ in XCTFail("mismatch callback on hello_ack") }
        bridge.dispatch([
            "type": ConnectProtocol.MessageType.helloAck,
            "protocol_version": 1,
        ])
        let drain = expectation(description: "drain")
        DispatchQueue.main.async { drain.fulfill() }
        wait(for: [drain], timeout: 1.0)
    }

    func testUnknownFrameTypeIsIgnored() {
        let bridge = makeBridge()
        bridge.onPresetPush = { _ in XCTFail("preset callback on unknown type") }
        bridge.onGainChange = { _ in XCTFail("gain callback on unknown type") }
        bridge.onVersionMismatch = { _ in XCTFail("mismatch callback on unknown type") }
        bridge.dispatch(["type": "definitely_not_a_real_type", "blah": 42])
        let drain = expectation(description: "drain")
        DispatchQueue.main.async { drain.fulfill() }
        wait(for: [drain], timeout: 1.0)
    }
}
