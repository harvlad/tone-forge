// MonitorMappingTests.swift
//
// Pure mapping surface of the monitor layer: engine snapshot ->
// connect_state frame, latency sec -> ms family, apply_chain wire
// extraction, and MicListener level math. No audio started.

import XCTest
import ConnectCore
import JamDesktopCore
@testable import JamDesktopAudio

@MainActor
final class MonitorMappingTests: XCTestCase {

    func testSnapshotMapsToConnectStateFrame() {
        let snapshot = AudioEngine.ConnectStateSnapshot(
            stateName: "running",
            inputDeviceName: "HX Stomp",
            outputDeviceName: "MacBook Pro Speakers",
            sampleRate: 48_000,
            channelsIn: 2,
            monitorEnabled: true,
            monitorGain: 0.75,
            monitorMuted: false,
            ampSimEnabled: true,
            activeChainId: "tube_break"
        )
        let frame = MonitorController.frame(from: snapshot)
        XCTAssertEqual(frame.state, "running")
        XCTAssertEqual(frame.device?.inputName, "HX Stomp")
        XCTAssertEqual(frame.device?.outputName, "MacBook Pro Speakers")
        XCTAssertEqual(frame.device?.sampleRate, 48_000)
        XCTAssertEqual(frame.device?.channelsIn, 2)
        XCTAssertEqual(frame.monitor?.enabled, true)
        XCTAssertEqual(frame.monitor?.gain ?? 0, 0.75, accuracy: 0.0001)
        XCTAssertEqual(frame.monitor?.muted, false)
        XCTAssertEqual(frame.dsp?.ampSimEnabled, true)
        XCTAssertEqual(frame.dsp?.activeChainId, "tube_break")
    }

    func testLatencyReportConvertsSecondsToMs() {
        let report = AudioEngine.LatencyReport(
            inputDeviceLatencySec: 0.0031,
            outputDeviceLatencySec: 0.0042,
            bufferDurationSec: 0.00533,
            estimatedRoundTripSec: 0.0179,
            measuredRoundTripSec: 0.0124,
            measurementConfidence: "high"
        )
        let frame = MonitorController.frame(from: report)
        XCTAssertEqual(frame.inputMs ?? 0, 3.1, accuracy: 0.0001)
        XCTAssertEqual(frame.outputMs ?? 0, 4.2, accuracy: 0.0001)
        XCTAssertEqual(frame.bufferMs ?? 0, 5.33, accuracy: 0.0001)
        XCTAssertEqual(frame.estimatedRoundTripMs ?? 0, 17.9, accuracy: 0.0001)
        XCTAssertEqual(frame.measuredRoundTripMs ?? 0, 12.4, accuracy: 0.0001)
        XCTAssertEqual(frame.measurementConfidence, "high")
    }

    func testLatencyReportOmitsMeasuredWhenNoProbeRan() {
        let report = AudioEngine.LatencyReport(
            inputDeviceLatencySec: 0.001,
            outputDeviceLatencySec: 0.001,
            bufferDurationSec: 0.005,
            estimatedRoundTripSec: 0.012,
            measuredRoundTripSec: nil,
            measurementConfidence: nil
        )
        let frame = MonitorController.frame(from: report)
        XCTAssertNil(frame.measuredRoundTripMs)
        XCTAssertNil(frame.measurementConfidence)
    }

    func testChainSpecExtractedFromApplyChainFrame() {
        let raw = Data(#"""
        {"type":"apply_chain","chain_id":"tube_break","request_id":"r1",
         "chain":{"id":"tube_break","display_name":"Tube Break",
                  "parameters":{"input":{"gain_db":2.0,"high_pass_hz":80},
                                "eq":{"bass_db":1.5,"mid_db":-1.0,"treble_db":0.5,"presence_db":2.0},
                                "output":{"trim_db":-3.0}}}}
        """#.utf8)
        let spec = MonitorController.chainSpec(fromApplyChainData: raw)
        XCTAssertEqual(spec?.id, "tube_break")
        XCTAssertEqual(spec?.displayName, "Tube Break")
    }

    func testChainSpecNilWhenNoEmbeddedChain() {
        let raw = Data(#"{"type":"apply_chain","chain_id":"x"}"#.utf8)
        XCTAssertNil(MonitorController.chainSpec(fromApplyChainData: raw))
    }

    // MARK: MicListener level math

    func testFullScaleSineLevels() {
        let n = 4800
        let samples = (0..<n).map { Float(sin(2 * Double.pi * 440 * Double($0) / 48_000)) }
        let (peak, rms) = MicListener.levels(samples: samples)
        XCTAssertEqual(peak, 0, accuracy: 0.01)      // full-scale peak
        XCTAssertEqual(rms, -3.01, accuracy: 0.05)   // sine rms = peak - 3.01 dB
    }

    func testSilenceClampsToFloor() {
        let (peak, rms) = MicListener.levels(samples: [Float](repeating: 0, count: 512))
        XCTAssertEqual(peak, MicListener.floorDbfs)
        XCTAssertEqual(rms, MicListener.floorDbfs)
    }

    func testEmptyBufferIsFloor() {
        let (peak, rms) = MicListener.levels(samples: [])
        XCTAssertEqual(peak, MicListener.floorDbfs)
        XCTAssertEqual(rms, MicListener.floorDbfs)
    }

    func testHalfScaleIsMinusSixDb() {
        let (peak, _) = MicListener.levels(samples: [0.5, -0.5, 0.5, -0.5])
        XCTAssertEqual(peak, -6.02, accuracy: 0.01)
    }
}
