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

    // MARK: - apply_chain dispatch

    /// Minimal-but-valid chain frame matching the server's
    /// `_monitor_chain_to_wire` projection. Kept smaller than the
    /// fixture in ChainSpecTests so it stays self-explanatory.
    private func chainFrame(replayed: Bool = false) -> [String: Any] {
        return [
            "type": ConnectProtocol.MessageType.applyChain,
            "chain_id": "tfc.clean_strat",
            "chain": [
                "id": "tfc.clean_strat",
                "family": "clean",
                "display_name": "Clean Strat",
                "description": "",
                "parameters": [
                    "input": ["gain_db": 0, "high_pass_hz": 80],
                    "gain_stage": ["type": "tube_clean", "drive": 0.1, "bias": 0.5],
                    "eq": [
                        "bass_db": 0, "mid_db": -1,
                        "treble_db": 2, "presence_db": 1,
                    ],
                    "comp": [
                        "enabled": true, "ratio": 2.0, "threshold_db": -18,
                        "attack_ms": 5, "release_ms": 80,
                    ],
                    "reverb": ["type": "room", "size": 0.3, "mix": 0.15],
                    "output": ["trim_db": 0],
                ] as [String: Any],
            ] as [String: Any],
            "replayed": replayed,
        ]
    }

    func testApplyChainFiresCallbackWithDecodedSpec() {
        let bridge = makeBridge()
        let exp = expectation(description: "chain spec delivered")
        bridge.onChainApply = { spec in
            XCTAssertEqual(spec.id, "tfc.clean_strat")
            XCTAssertEqual(spec.displayName, "Clean Strat")
            XCTAssertEqual(spec.gainStage.type, .tubeClean)
            XCTAssertTrue(spec.comp.enabled)
            exp.fulfill()
        }
        bridge.dispatch(chainFrame())
        wait(for: [exp], timeout: 1.0)
    }

    func testApplyChainAcceptsReplayFlag() {
        // Replay flag must not gate the callback — the server replays
        // the most-recent chain on (re)join and we want the engine
        // re-programmed even after a reconnect.
        let bridge = makeBridge()
        let exp = expectation(description: "replay chain delivered")
        bridge.onChainApply = { spec in
            XCTAssertEqual(spec.id, "tfc.clean_strat")
            exp.fulfill()
        }
        bridge.dispatch(chainFrame(replayed: true))
        wait(for: [exp], timeout: 1.0)
    }

    func testApplyChainWithoutEnvelopeFallsBackToTopLevel() {
        // Forward-compat: if a future server sends the spec at the
        // top level (no "chain" wrapper), the decoder should still
        // succeed against the outer dict.
        let bridge = makeBridge()
        let exp = expectation(description: "flat chain frame delivered")
        bridge.onChainApply = { spec in
            XCTAssertEqual(spec.id, "tfc.flat")
            exp.fulfill()
        }
        bridge.dispatch([
            "type": ConnectProtocol.MessageType.applyChain,
            "id": "tfc.flat",
            "display_name": "Flat",
            "parameters": [
                "input": ["gain_db": 0, "high_pass_hz": 80],
                "gain_stage": ["type": "tube_clean", "drive": 0.1, "bias": 0.5],
                "eq": [
                    "bass_db": 0, "mid_db": 0,
                    "treble_db": 0, "presence_db": 0,
                ],
                "comp": [
                    "enabled": false, "ratio": 2.0, "threshold_db": -18,
                    "attack_ms": 5, "release_ms": 80,
                ],
                "reverb": ["type": "room", "size": 0.3, "mix": 0.1],
                "output": ["trim_db": 0],
            ] as [String: Any],
        ])
        wait(for: [exp], timeout: 1.0)
    }

    func testApplyChainWithMissingIdIsDropped() {
        // Malformed frames must never invoke the callback — the audio
        // engine would re-program against garbage. Bridge logs via
        // onStatus and moves on.
        let bridge = makeBridge()
        bridge.onChainApply = { _ in
            XCTFail("callback fired on malformed apply_chain frame")
        }
        bridge.dispatch([
            "type": ConnectProtocol.MessageType.applyChain,
            "chain": [
                "parameters": [:] as [String: Any],
            ] as [String: Any],
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
