// TransportClockTests.swift
//
// Verify the D-005 master clock: play advances song-time, pause
// freezes it, stop resets, seek preserves state. Uses a manual host
// time provider so the tests are hermetic and deterministic.

import XCTest
@testable import ToneForgeMobile

final class TransportClockTests: XCTestCase {

    /// Simulated host clock. Ticks-per-second is the default the clock
    /// picks from mach_timebase; we bypass it by providing the tick
    /// values directly.
    private final class ManualHostTime: @unchecked Sendable {
        var ticks: UInt64 = 0
    }

    private func makeClock(host: ManualHostTime) -> TransportClock {
        TransportClock(hostTimeProvider: { host.ticks })
    }

    private var ticksPerSecond: Double { TransportClock.ticksPerSecond() }

    func testStartsStopped() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        XCTAssertEqual(c.state, .stopped)
        XCTAssertEqual(c.nowSongSeconds, 0)
    }

    func testPlayAdvancesSongTime() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.play()
        host.ticks = UInt64(ticksPerSecond * 1.5)   // simulate 1.5 s
        XCTAssertEqual(c.nowSongSeconds, 1.5, accuracy: 0.0001)
    }

    func testPauseFreezes() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.play()
        host.ticks = UInt64(ticksPerSecond * 2.0)
        c.pause()
        // Advance host time by 5 s; song time must not move.
        host.ticks = UInt64(ticksPerSecond * 7.0)
        XCTAssertEqual(c.nowSongSeconds, 2.0, accuracy: 0.0001)
    }

    func testResumeContinuesFromPause() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.play()
        host.ticks = UInt64(ticksPerSecond * 2.0)
        c.pause()
        host.ticks = UInt64(ticksPerSecond * 5.0)  // paused for 3 s
        c.play()
        host.ticks = UInt64(ticksPerSecond * 6.0)  // played 1 more second
        XCTAssertEqual(c.nowSongSeconds, 3.0, accuracy: 0.0001)
    }

    func testStopResets() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.play()
        host.ticks = UInt64(ticksPerSecond * 4.0)
        c.stop()
        XCTAssertEqual(c.state, .stopped)
        XCTAssertEqual(c.nowSongSeconds, 0)
    }

    func testSeekWhileStoppedMovesAnchor() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.seek(to: 42)
        XCTAssertEqual(c.state, .stopped)
        // Stopped clock always reads 0 regardless of seek.
        XCTAssertEqual(c.nowSongSeconds, 0)
    }

    func testSeekWhilePausedMovesPosition() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.play()
        host.ticks = UInt64(ticksPerSecond * 1.0)
        c.pause()
        c.seek(to: 10)
        XCTAssertEqual(c.nowSongSeconds, 10.0, accuracy: 0.0001)
    }

    func testSeekWhilePlayingMovesAndKeepsPlaying() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.play()
        host.ticks = UInt64(ticksPerSecond * 1.0)
        c.seek(to: 30)
        // Time should now advance from 30.
        host.ticks = UInt64(ticksPerSecond * 2.5)   // +1.5 s host
        XCTAssertEqual(c.nowSongSeconds, 31.5, accuracy: 0.0001)
    }

    func testSeekNegativeRunsCountInWindow() {
        // Sketch-record count-in: transport seeks to -barDuration and
        // plays through zero. Song time must pass through the negative
        // window rather than clamping at 0.
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.play()
        c.seek(to: -2)
        XCTAssertEqual(c.nowSongSeconds, -2.0, accuracy: 0.0001)
        host.ticks = UInt64(ticksPerSecond * 1.5)
        XCTAssertEqual(c.nowSongSeconds, -0.5, accuracy: 0.0001)
        host.ticks = UInt64(ticksPerSecond * 3.0)
        XCTAssertEqual(c.nowSongSeconds, 1.0, accuracy: 0.0001)
    }

    func testPlayIsIdempotent() {
        let host = ManualHostTime()
        let c = makeClock(host: host)
        c.play()
        host.ticks = UInt64(ticksPerSecond * 1.0)
        // Second play() must NOT reset the anchor — song time should
        // keep advancing normally.
        c.play()
        host.ticks = UInt64(ticksPerSecond * 2.0)
        XCTAssertEqual(c.nowSongSeconds, 2.0, accuracy: 0.0001)
    }
}
