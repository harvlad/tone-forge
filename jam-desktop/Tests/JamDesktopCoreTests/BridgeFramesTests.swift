// BridgeFramesTests.swift
//
// Wire-parity coverage for the v2 frame codec. Fixtures are copied
// byte-for-byte from the emission sites: jam.js (transport_state,
// session_data, load_stems) and ConnectCore/Protocol.swift doc shapes
// (connect_state, latency_report). If a fixture stops decoding,
// the codec drifted from the wire — fix the codec, not the fixture.

import XCTest
@testable import JamDesktopCore

final class BridgeFramesTests: XCTestCase {

    private func fixture(_ name: String) throws -> Data {
        let url = try XCTUnwrap(Bundle.module.url(
            forResource: "Fixtures/\(name)", withExtension: "json"
        ))
        return try Data(contentsOf: url)
    }

    // MARK: - Fixture decode (wire → model)

    func testDecodesJamJsTransportState() throws {
        let frame = try BridgeFrameCodec.decode(fixture("transport_state"))
        guard case .transportState(let t) = frame else {
            return XCTFail("expected transportState, got \(frame)")
        }
        XCTAssertEqual(t.playing, true)
        XCTAssertEqual(t.positionS, 12.34)
        // jam.js sends the minimal shape — richer fields stay nil.
        XCTAssertNil(t.tempoPct)
        XCTAssertNil(t.loopInS)
    }

    func testDecodesJamJsSessionData() throws {
        let frame = try BridgeFrameCodec.decode(fixture("session_data"))
        guard case .sessionData(let s) = frame else {
            return XCTFail("expected sessionData, got \(frame)")
        }
        XCTAssertEqual(s.sessionId, "device-abc")
        XCTAssertEqual(s.song?.title, "Night Drive")
        XCTAssertEqual(s.bpm, 120.0)
        XCTAssertEqual(s.key?.root, 0)
        XCTAssertEqual(s.chordProgression?.count, 2)
        XCTAssertEqual(s.chordProgression?[1].symbol, "G")
        XCTAssertEqual(s.chordProgression?[1].startS, 2.0)
        XCTAssertEqual(s.sectionMarkers?.first?.name, "Verse 1")
    }

    func testDecodesJamJsLoadStems() throws {
        let frame = try BridgeFrameCodec.decode(fixture("load_stems"))
        guard case .loadStems(let l) = frame else {
            return XCTFail("expected loadStems, got \(frame)")
        }
        XCTAssertEqual(l.stems.count, 2)
        XCTAssertEqual(l.stems[0].id, "demucs.drums")
        XCTAssertEqual(l.stems[0].displayName, "Drums")
    }

    func testDecodesConnectState() throws {
        let frame = try BridgeFrameCodec.decode(fixture("connect_state"))
        guard case .connectState(let c) = frame else {
            return XCTFail("expected connectState, got \(frame)")
        }
        XCTAssertEqual(c.state, "running")
        XCTAssertEqual(c.device?.inputName, "MacBook Pro Microphone")
        XCTAssertEqual(c.monitor?.gain, 0.8)
        XCTAssertEqual(c.dsp?.activeChainId, "fender-clean")
    }

    func testDecodesLatencyReportMsFamily() throws {
        // Wire truth: *_ms fields (jam.js:897 reads
        // estimated_round_trip_ms) — NOT protocol.py's stale *_sec.
        let frame = try BridgeFrameCodec.decode(fixture("latency_report"))
        guard case .latencyReport(let r) = frame else {
            return XCTFail("expected latencyReport, got \(frame)")
        }
        XCTAssertEqual(r.estimatedRoundTripMs, 17.9)
        XCTAssertEqual(r.measuredRoundTripMs, 12.4)
        XCTAssertEqual(r.measurementConfidence, "high")
    }

    // MARK: - Encode (model → wire)

    func testHelloEncodesHandshakeFields() throws {
        let hello = HelloFrame(sessionId: "device-abc")
        let json = try JSONSerialization.jsonObject(
            with: BridgeFrameCodec.encode(hello)
        ) as? [String: Any]
        XCTAssertEqual(json?["type"] as? String, "hello")
        XCTAssertEqual(json?["role"] as? String, "connect")
        XCTAssertEqual(json?["session_id"] as? String, "device-abc")
        XCTAssertEqual(json?["protocol_version"] as? Int, 2)
        XCTAssertEqual(json?["client_kind"] as? String, "jam-desktop")
    }

    func testTransportStateEncodesSnakeCase() throws {
        let frame = TransportStateFrame(
            playing: true, positionS: 3.5, tempoPct: 0.75,
            loopInS: 1.0, loopOutS: 5.0
        )
        let json = try JSONSerialization.jsonObject(
            with: BridgeFrameCodec.encode(frame)
        ) as? [String: Any]
        XCTAssertEqual(json?["type"] as? String, "transport_state")
        XCTAssertEqual(json?["v"] as? Int, 2)
        XCTAssertEqual(json?["position_s"] as? Double, 3.5)
        XCTAssertEqual(json?["tempo_pct"] as? Double, 0.75)
        XCTAssertEqual(json?["loop_in_s"] as? Double, 1.0)
        XCTAssertEqual(json?["loop_out_s"] as? Double, 5.0)
    }

    func testRoundTripSessionData() throws {
        let original = try BridgeFrameCodec.decode(fixture("session_data"))
        guard case .sessionData(let s) = original else {
            return XCTFail("expected sessionData")
        }
        let redecoded = try BridgeFrameCodec.decode(BridgeFrameCodec.encode(s))
        XCTAssertEqual(redecoded, original)
    }

    // MARK: - Dispatch edges

    func testUnknownTypePreserved() throws {
        let data = Data(#"{"type": "set_auto_update", "enabled": true}"#.utf8)
        XCTAssertEqual(
            try BridgeFrameCodec.decode(data),
            .unknown(type: "set_auto_update")
        )
    }

    func testMissingTypeThrows() {
        let data = Data(#"{"v": 2}"#.utf8)
        XCTAssertThrowsError(try BridgeFrameCodec.decode(data)) { error in
            XCTAssertEqual(
                error as? BridgeFrameCodec.CodecError, .missingType
            )
        }
    }

    func testSetGainLegacyAliasDecodes() throws {
        let data = Data(#"{"type": "set_gain", "gain": 0.5}"#.utf8)
        guard case .setGain(let g) = try BridgeFrameCodec.decode(data) else {
            return XCTFail("expected setGain")
        }
        XCTAssertEqual(g.gain, 0.5)
    }
}
