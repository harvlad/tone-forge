// MixerReadoutTests.swift
//
// Unit tests for the mixer's dB readout formatting (Phase 11) —
// the one piece of mixer logic that runs on macOS `swift test`
// (the strips themselves are covered by MixerSnapshotTests on the
// simulator).

import XCTest
@testable import ToneForgeMobile

final class MixerReadoutTests: XCTestCase {

    func testUnityGainIsZeroDb() {
        XCTAssertEqual(MixerReadout.dbString(gainLinear: 1.0), "+0.0 dB")
    }

    func testZeroGainIsNegativeInfinity() {
        XCTAssertEqual(MixerReadout.dbString(gainLinear: 0.0), "-∞ dB")
    }

    func testNegativeGainClampsToNegativeInfinity() {
        XCTAssertEqual(MixerReadout.dbString(gainLinear: -0.5), "-∞ dB")
    }

    func testHalfGainIsMinusSixDb() {
        XCTAssertEqual(MixerReadout.dbString(gainLinear: 0.5), "-6.0 dB")
    }

    func testTenthGainIsMinusTwentyDb() {
        XCTAssertEqual(MixerReadout.dbString(gainLinear: 0.1), "-20.0 dB")
    }
}
