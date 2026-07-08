// TransportClockRateTests.swift
//
// D-022 practice speed: the clock's rate scales song-time advance,
// and setRate mid-play is glitch-free (position continuous across
// the change). Same manual host-time harness as TransportClockTests.

import XCTest
@testable import ToneForgeMobile

final class TransportClockRateTests: XCTestCase {

    private final class ManualHostTime: @unchecked Sendable {
        var ticks: UInt64 = 0
    }

    private func makeClock(host: ManualHostTime) -> TransportClock {
        TransportClock(hostTimeProvider: { host.ticks })
    }

    private var ticksPerSecond: Double { TransportClock.ticksPerSecond() }

    func testDefaultRateIsOne() {
        let c = makeClock(host: ManualHostTime())
        XCTAssertEqual(c.rate, 1.0)
    }

    func testHalfRateAdvancesHalfSpeed() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.setRate(0.5)
        c.play()
        host.ticks = UInt64(ticksPerSecond * 4.0)   // 4 s wall-clock
        XCTAssertEqual(c.nowSongSeconds, 2.0, accuracy: 0.0001)
    }

    func testMidPlayRateChangeIsContinuous() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.play()
        host.ticks = UInt64(ticksPerSecond * 2.0)   // 2 s at 1.0x → 2.0
        c.setRate(0.5)
        // Position must not jump at the change.
        XCTAssertEqual(c.nowSongSeconds, 2.0, accuracy: 0.0001)
        host.ticks = UInt64(ticksPerSecond * 4.0)   // +2 s at 0.5x → +1.0
        XCTAssertEqual(c.nowSongSeconds, 3.0, accuracy: 0.0001)
        c.setRate(1.0)
        XCTAssertEqual(c.nowSongSeconds, 3.0, accuracy: 0.0001)
        host.ticks = UInt64(ticksPerSecond * 5.0)   // +1 s at 1.0x
        XCTAssertEqual(c.nowSongSeconds, 4.0, accuracy: 0.0001)
    }

    func testRateChangeWhilePausedKeepsPosition() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.play()
        host.ticks = UInt64(ticksPerSecond * 3.0)
        c.pause()
        c.setRate(0.5)
        XCTAssertEqual(c.nowSongSeconds, 3.0, accuracy: 0.0001)
        host.ticks = UInt64(ticksPerSecond * 10.0)
        c.play()
        host.ticks = UInt64(ticksPerSecond * 12.0)  // +2 s at 0.5x
        XCTAssertEqual(c.nowSongSeconds, 4.0, accuracy: 0.0001)
    }

    func testSeekPreservesRate() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.setRate(0.5)
        c.play()
        host.ticks = UInt64(ticksPerSecond * 1.0)
        c.seek(to: 30)
        host.ticks = UInt64(ticksPerSecond * 3.0)   // +2 s at 0.5x
        XCTAssertEqual(c.nowSongSeconds, 31.0, accuracy: 0.0001)
        XCTAssertEqual(c.rate, 0.5)
    }

    func testNonPositiveRateIgnored() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.setRate(0)
        XCTAssertEqual(c.rate, 1.0)
        c.setRate(-1)
        XCTAssertEqual(c.rate, 1.0)
    }

    func testStopResetsPositionNotRate() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.setRate(0.75)
        c.play()
        host.ticks = UInt64(ticksPerSecond * 2.0)
        c.stop()
        XCTAssertEqual(c.nowSongSeconds, 0)
        // Rate is a user setting, not transport state — it survives.
        XCTAssertEqual(c.rate, 0.75)
    }
}
