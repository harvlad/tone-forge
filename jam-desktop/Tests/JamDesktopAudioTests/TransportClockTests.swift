// TransportClockTests.swift
//
// Rate-aware clock semantics with an injected host-time provider —
// no real time, no audio engine. Pins the mobile-ported behavior:
// pause freezes, seek re-anchors, setRate snapshots under the old
// rate so a mid-play tempo change never jumps the playhead.

import XCTest
@testable import JamDesktopAudio

final class TransportClockTests: XCTestCase {

    /// Mutable host-time source. Ticks are in "fake mach ticks" —
    /// tests convert seconds via the real timebase so the clock's
    /// internal ticksPerSecond() math cancels out.
    private final class FakeHost: @unchecked Sendable {
        var ticks: UInt64 = 1_000_000
        func advance(seconds: Double) {
            ticks &+= UInt64(seconds * TransportClock.ticksPerSecond())
        }
    }

    private var host: FakeHost!
    private var clock: TransportClock!

    override func setUp() {
        super.setUp()
        host = FakeHost()
        let h = host!
        clock = TransportClock(hostTimeProvider: { h.ticks })
    }

    func testStoppedReadsZero() {
        XCTAssertEqual(clock.state, .stopped)
        XCTAssertEqual(clock.nowSongSeconds, 0)
        host.advance(seconds: 5)
        XCTAssertEqual(clock.nowSongSeconds, 0)
    }

    func testPlayAdvancesAtUnityRate() {
        clock.play()
        host.advance(seconds: 2)
        XCTAssertEqual(clock.nowSongSeconds, 2.0, accuracy: 0.001)
    }

    func testPauseFreezesPosition() {
        clock.play()
        host.advance(seconds: 1.5)
        clock.pause()
        host.advance(seconds: 10)
        XCTAssertEqual(clock.nowSongSeconds, 1.5, accuracy: 0.001)
        XCTAssertEqual(clock.state, .paused)
    }

    func testResumeContinuesFromPausedPosition() {
        clock.play()
        host.advance(seconds: 1)
        clock.pause()
        host.advance(seconds: 5)   // ignored while paused
        clock.play()
        host.advance(seconds: 2)
        XCTAssertEqual(clock.nowSongSeconds, 3.0, accuracy: 0.001)
    }

    func testStopResetsToZero() {
        clock.play()
        host.advance(seconds: 4)
        clock.stop()
        XCTAssertEqual(clock.nowSongSeconds, 0)
        XCTAssertEqual(clock.state, .stopped)
    }

    func testSeekWhilePlayingReanchors() {
        clock.play()
        host.advance(seconds: 3)
        clock.seek(to: 10)
        host.advance(seconds: 1)
        XCTAssertEqual(clock.nowSongSeconds, 11.0, accuracy: 0.001)
    }

    func testSeekNegativeIsLegal() {
        clock.seek(to: -2)   // count-in window
        clock.play()
        host.advance(seconds: 1)
        XCTAssertEqual(clock.nowSongSeconds, -1.0, accuracy: 0.001)
    }

    func testRateScalesAdvance() {
        clock.setRate(0.5)
        clock.play()
        host.advance(seconds: 4)
        XCTAssertEqual(clock.nowSongSeconds, 2.0, accuracy: 0.001)
    }

    func testMidPlayRateChangeDoesNotJump() {
        clock.play()
        host.advance(seconds: 2)          // position 2.0 at rate 1
        clock.setRate(0.5)                // snapshot under OLD rate
        XCTAssertEqual(clock.nowSongSeconds, 2.0, accuracy: 0.001)
        host.advance(seconds: 2)          // +1.0 song-sec at rate 0.5
        XCTAssertEqual(clock.nowSongSeconds, 3.0, accuracy: 0.001)
    }

    func testPlayIsIdempotent() {
        clock.play()
        host.advance(seconds: 1)
        clock.play()   // must not re-anchor
        host.advance(seconds: 1)
        XCTAssertEqual(clock.nowSongSeconds, 2.0, accuracy: 0.001)
    }
}
