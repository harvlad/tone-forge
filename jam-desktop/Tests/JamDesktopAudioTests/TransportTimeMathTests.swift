// TransportTimeMathTests.swift
//
// Rate-scaled delay math: unity passthrough, half-rate doubling,
// immediate-window nil, past-target nil, zero-rate epsilon floor.

import XCTest
@testable import JamDesktopAudio

final class TransportTimeMathTests: XCTestCase {

    func testUnityRatePassthrough() {
        XCTAssertEqual(
            TransportTimeMath.scaledDelaySeconds(targetSong: 3, nowSong: 1, rate: 1.0),
            2.0, accuracy: 1e-9
        )
    }

    func testHalfRateDoublesDelay() {
        XCTAssertEqual(
            TransportTimeMath.scaledDelaySeconds(targetSong: 3, nowSong: 1, rate: 0.5),
            4.0, accuracy: 1e-9
        )
    }

    func testZeroRateFloorsAtEpsilonNotDivideByZero() {
        let d = TransportTimeMath.scaledDelaySeconds(targetSong: 2, nowSong: 1, rate: 0)
        XCTAssertTrue(d.isFinite)
        XCTAssertEqual(d, 1.0 / 0.0001, accuracy: 1)
    }

    func testHostDelayTicksScalesByRate() {
        let tps = 1000.0
        XCTAssertEqual(
            TransportTimeMath.hostDelayTicks(
                targetSong: 2, nowSong: 1, rate: 0.5, ticksPerSecond: tps),
            2000
        )
    }

    func testImmediateWindowReturnsNil() {
        XCTAssertNil(TransportTimeMath.hostDelayTicks(
            targetSong: 1.0005, nowSong: 1, rate: 1.0, ticksPerSecond: 1000))
    }

    func testPastTargetReturnsNil() {
        XCTAssertNil(TransportTimeMath.hostDelayTicks(
            targetSong: 0.5, nowSong: 1, rate: 1.0, ticksPerSecond: 1000))
    }
}
