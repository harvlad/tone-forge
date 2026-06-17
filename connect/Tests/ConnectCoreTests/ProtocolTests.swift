//
// ProtocolTests.swift
//
// Pins the wire-protocol surface. If any value here changes,
// CONNECT_BRIDGE_PROTOCOL_VERSION on the server must change in
// lockstep — otherwise a fielded Connect helper will fall out of
// sync with the backend on the next deploy.
//

import XCTest
@testable import ConnectCore

final class ProtocolTests: XCTestCase {

    /// Version pin. The number itself is not load-bearing; the test
    /// is. Bumping the value is fine — the failure forces the author
    /// to also bump the server constant and the docstring.
    func testProtocolVersionIsPinned() {
        XCTAssertEqual(ConnectProtocol.version, 2,
            "If this value changed, also bump CONNECT_BRIDGE_PROTOCOL_VERSION in tone_forge_api.py")
    }

    /// Message-type strings must stay in sync with the JSON envelope
    /// documented in tone_forge_api.py. Any rename here breaks
    /// browser ↔ server ↔ Connect compatibility.
    func testMessageTypeStringsAreStable() {
        // v1 envelope
        XCTAssertEqual(ConnectProtocol.MessageType.hello,           "hello")
        XCTAssertEqual(ConnectProtocol.MessageType.helloAck,        "hello_ack")
        XCTAssertEqual(ConnectProtocol.MessageType.versionMismatch, "version_mismatch")
        XCTAssertEqual(ConnectProtocol.MessageType.joined,          "joined")
        XCTAssertEqual(ConnectProtocol.MessageType.presetPush,      "preset_push")
        XCTAssertEqual(ConnectProtocol.MessageType.setGain,         "set_gain")
        XCTAssertEqual(ConnectProtocol.MessageType.ping,            "ping")
        XCTAssertEqual(ConnectProtocol.MessageType.pong,            "pong")
        XCTAssertEqual(ConnectProtocol.MessageType.ack,             "ack")
        XCTAssertEqual(ConnectProtocol.MessageType.error,           "error")
        // v2 Audio-Ownership Pivot additions
        XCTAssertEqual(ConnectProtocol.MessageType.sessionData,     "session_data")
        XCTAssertEqual(ConnectProtocol.MessageType.transportState,  "transport_state")
        XCTAssertEqual(ConnectProtocol.MessageType.connectState,    "connect_state")
        XCTAssertEqual(ConnectProtocol.MessageType.latencyReport,   "latency_report")
        XCTAssertEqual(ConnectProtocol.MessageType.inputMeter,      "input_meter")
    }
}
