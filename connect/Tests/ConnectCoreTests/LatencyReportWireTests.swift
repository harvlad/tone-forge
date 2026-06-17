//
// LatencyReportWireTests.swift
//
// Audio-Ownership Pivot, Phase 4 commit B. Pins the wire-frame
// shape of the v2 `connect_state` and `latency_report` outbound
// frames. The builders are pure (no socket, no thread hop) so we
// can call them straight from the test and round-trip through
// JSONSerialization to assert the exact bytes that would land on
// the WebSocket.
//
// We test the builder, not `PresetBridge.send*`, because the public
// send methods guard on `task != nil` and short-circuit when there
// is no WebSocket — testing them would require either a fake URL
// session or a real backend. The builder is the load-bearing
// piece; if its dict shape is correct, sendJSON's behaviour is
// already exercised by every other test in this suite.
//

import XCTest
@testable import ConnectCore

final class LatencyReportWireTests: XCTestCase {

    // MARK: - latency_report

    /// Helper: estimate-only LatencyReport (no LatencyProbe measurement
    /// taken yet). Matches the v2-baseline frame shape from Phase 4.
    private func estimatedOnlyReport(
        inputSec: Double = 0.0031,
        outputSec: Double = 0.0042,
        bufferSec: Double = 0.0053,
        rtSec: Double = 0.0126
    ) -> AudioEngine.LatencyReport {
        return AudioEngine.LatencyReport(
            inputDeviceLatencySec:  inputSec,
            outputDeviceLatencySec: outputSec,
            bufferDurationSec:      bufferSec,
            estimatedRoundTripSec:  rtSec,
            measuredRoundTripSec:   nil,
            measurementConfidence:  nil
        )
    }

    func testLatencyReportFrameHasCorrectTopLevelEnvelope() {
        let frame = PresetBridge.buildLatencyReportFrame(estimatedOnlyReport())
        XCTAssertEqual(frame["type"] as? String, "latency_report")
        XCTAssertEqual(frame["v"]    as? Int,    2)
    }

    func testLatencyReportConvertsSecondsToMilliseconds() {
        let frame = PresetBridge.buildLatencyReportFrame(estimatedOnlyReport())
        // XCTAssertEqual's accuracy overload needs a non-optional Double;
        // `?? .nan` propagates a missed cast into a failing assertion.
        XCTAssertEqual(frame["input_ms"]                as? Double ?? .nan, 3.1,  accuracy: 0.001)
        XCTAssertEqual(frame["output_ms"]               as? Double ?? .nan, 4.2,  accuracy: 0.001)
        XCTAssertEqual(frame["buffer_ms"]               as? Double ?? .nan, 5.3,  accuracy: 0.001)
        XCTAssertEqual(frame["estimated_round_trip_ms"] as? Double ?? .nan, 12.6, accuracy: 0.001)
    }

    /// When no LatencyProbe measurement has been taken, the measured
    /// fields must be ABSENT from the dict — not present as JSON null.
    /// This is the same omit-rather-than-null contract that
    /// connect_state uses for optional device names.
    func testLatencyReportOmitsMeasuredFieldsWhenNotMeasured() {
        let frame = PresetBridge.buildLatencyReportFrame(estimatedOnlyReport())
        XCTAssertNil(frame["measured_round_trip_ms"])
        XCTAssertNil(frame["measurement_confidence"])
    }

    /// When a LatencyProbe result has populated the cache, both
    /// measured_round_trip_ms (seconds → ms conversion) and
    /// measurement_confidence (passed through verbatim) must be
    /// present on the wire.
    func testLatencyReportIncludesMeasuredFieldsWhenPresent() {
        let report = AudioEngine.LatencyReport(
            inputDeviceLatencySec:  0.005,
            outputDeviceLatencySec: 0.010,
            bufferDurationSec:      0.005333,
            estimatedRoundTripSec:  0.020333,
            measuredRoundTripSec:   0.0124,
            measurementConfidence:  "high"
        )
        let frame = PresetBridge.buildLatencyReportFrame(report)
        XCTAssertEqual(frame["measured_round_trip_ms"] as? Double ?? .nan, 12.4, accuracy: 0.001)
        XCTAssertEqual(frame["measurement_confidence"] as? String, "high")
    }

    /// Low-confidence and no-signal probe outcomes still produce a
    /// measured_round_trip_ms — the UI needs the badge to render
    /// the warning state, and the floor estimate stays alongside.
    func testLatencyReportPropagatesLowConfidenceVerbatim() {
        let report = AudioEngine.LatencyReport(
            inputDeviceLatencySec:  0.005,
            outputDeviceLatencySec: 0.010,
            bufferDurationSec:      0.005333,
            estimatedRoundTripSec:  0.020333,
            measuredRoundTripSec:   0.999,
            measurementConfidence:  "low"
        )
        let frame = PresetBridge.buildLatencyReportFrame(report)
        XCTAssertEqual(frame["measurement_confidence"] as? String, "low")
    }

    /// The wire is JSON; the frame must survive a round trip through
    /// JSONSerialization without losing fields or coercing numerics
    /// into something the Python side won't accept as a number.
    func testLatencyReportFrameRoundTripsAsJson() throws {
        let report = AudioEngine.LatencyReport(
            inputDeviceLatencySec:  0.005,
            outputDeviceLatencySec: 0.010,
            bufferDurationSec:      0.005333,
            estimatedRoundTripSec:  0.020333,
            measuredRoundTripSec:   0.0124,
            measurementConfidence:  "high"
        )
        let frame = PresetBridge.buildLatencyReportFrame(report)
        let data  = try JSONSerialization.data(withJSONObject: frame, options: [])
        let obj   = try JSONSerialization.jsonObject(with: data, options: [])
        guard let parsed = obj as? [String: Any] else {
            XCTFail("round-tripped object was not a dict")
            return
        }
        XCTAssertEqual(parsed["type"] as? String, "latency_report")
        XCTAssertEqual(parsed["v"]    as? Int,    2)
        XCTAssertEqual(parsed["input_ms"]                as? Double ?? .nan, 5.0,    accuracy: 0.001)
        XCTAssertEqual(parsed["output_ms"]               as? Double ?? .nan, 10.0,   accuracy: 0.001)
        XCTAssertEqual(parsed["buffer_ms"]               as? Double ?? .nan, 5.333,  accuracy: 0.001)
        XCTAssertEqual(parsed["estimated_round_trip_ms"] as? Double ?? .nan, 20.333, accuracy: 0.001)
        XCTAssertEqual(parsed["measured_round_trip_ms"]  as? Double ?? .nan, 12.4,   accuracy: 0.001)
        XCTAssertEqual(parsed["measurement_confidence"]  as? String, "high")
    }

    // MARK: - connect_state

    private func sampleSnapshot(
        gain: Float = 0.7,
        muted: Bool = false,
        ampSimEnabled: Bool = true,
        chainId: String? = "fender_clean"
    ) -> AudioEngine.ConnectStateSnapshot {
        return AudioEngine.ConnectStateSnapshot(
            stateName:         "running",
            inputDeviceName:   "Line 6 HX Stomp",
            outputDeviceName:  "MacBook Pro Speakers",
            sampleRate:        48000,
            channelsIn:        2,
            monitorEnabled:    ampSimEnabled && !muted,
            monitorGain:       gain,
            monitorMuted:      muted,
            ampSimEnabled:     ampSimEnabled,
            activeChainId:     chainId
        )
    }

    func testConnectStateFrameHasCorrectTopLevelEnvelope() {
        let frame = PresetBridge.buildConnectStateFrame(sampleSnapshot())
        XCTAssertEqual(frame["type"]  as? String, "connect_state")
        XCTAssertEqual(frame["v"]     as? Int,    2)
        XCTAssertEqual(frame["state"] as? String, "running")
    }

    func testConnectStateFrameContainsNestedDeviceMonitorDsp() {
        let frame  = PresetBridge.buildConnectStateFrame(sampleSnapshot())
        let device  = frame["device"]  as? [String: Any]
        let monitor = frame["monitor"] as? [String: Any]
        let dsp     = frame["dsp"]     as? [String: Any]
        XCTAssertNotNil(device)
        XCTAssertNotNil(monitor)
        XCTAssertNotNil(dsp)

        XCTAssertEqual(device?["input_name"]   as? String, "Line 6 HX Stomp")
        XCTAssertEqual(device?["output_name"]  as? String, "MacBook Pro Speakers")
        XCTAssertEqual(device?["sample_rate"]  as? Double, 48000)
        XCTAssertEqual(device?["channels_in"]  as? Int,    2)

        XCTAssertEqual(monitor?["enabled"]     as? Bool,   true)
        XCTAssertEqual(monitor?["gain"]        as? Double ?? .nan, 0.7, accuracy: 0.0001)
        XCTAssertEqual(monitor?["muted"]       as? Bool,   false)

        XCTAssertEqual(dsp?["amp_sim_enabled"] as? Bool,   true)
        XCTAssertEqual(dsp?["active_chain_id"] as? String, "fender_clean")
    }

    /// Optional device names must be omitted from the dict — not
    /// emitted as JSON `null` — so the Python side's "is the field
    /// present?" checks stay aligned with the protocol doc.
    func testConnectStateFrameOmitsOptionalDeviceNamesWhenNil() {
        let snap = AudioEngine.ConnectStateSnapshot(
            stateName:         "starting",
            inputDeviceName:   nil,
            outputDeviceName:  nil,
            sampleRate:        44100,
            channelsIn:        1,
            monitorEnabled:    false,
            monitorGain:       0.0,
            monitorMuted:      true,
            ampSimEnabled:     false,
            activeChainId:     nil
        )
        let frame  = PresetBridge.buildConnectStateFrame(snap)
        let device = frame["device"] as? [String: Any]
        let dsp    = frame["dsp"]    as? [String: Any]
        XCTAssertNil(device?["input_name"])
        XCTAssertNil(device?["output_name"])
        XCTAssertNil(dsp?["active_chain_id"])
        // Required fields still present.
        XCTAssertEqual(device?["sample_rate"]  as? Double, 44100)
        XCTAssertEqual(device?["channels_in"]  as? Int,    1)
        XCTAssertEqual(dsp?["amp_sim_enabled"] as? Bool,   false)
    }

    func testConnectStateFrameRoundTripsAsJson() throws {
        let frame = PresetBridge.buildConnectStateFrame(
            sampleSnapshot(gain: 0.55, muted: false, ampSimEnabled: true, chainId: "vox_chime")
        )
        let data  = try JSONSerialization.data(withJSONObject: frame, options: [])
        let obj   = try JSONSerialization.jsonObject(with: data, options: [])
        guard let parsed = obj as? [String: Any] else {
            XCTFail("round-tripped object was not a dict")
            return
        }
        XCTAssertEqual(parsed["type"]  as? String, "connect_state")
        XCTAssertEqual(parsed["state"] as? String, "running")
        let monitor = parsed["monitor"] as? [String: Any]
        XCTAssertEqual(monitor?["gain"] as? Double ?? .nan, 0.55, accuracy: 0.0001)
        let dsp = parsed["dsp"] as? [String: Any]
        XCTAssertEqual(dsp?["active_chain_id"] as? String, "vox_chime")
    }
}
