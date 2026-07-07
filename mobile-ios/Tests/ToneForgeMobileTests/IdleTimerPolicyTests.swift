// IdleTimerPolicyTests.swift
//
// Truth table for the P7 idle-timer predicate. The rule: keep the
// screen awake only while the transport runs AND some hands-off
// surface (Launchpad / recorder / capture) is engaged.

import XCTest
@testable import ToneForgeMobile

final class IdleTimerPolicyTests: XCTestCase {

    private func disable(
        playing: Bool, launchpad: Bool = false,
        recorder: Bool = false, capture: Bool = false
    ) -> Bool {
        IdleTimerPolicy.shouldDisableIdleTimer(
            isPlaying: playing,
            launchpadConnected: launchpad,
            recorderActive: recorder,
            captureActive: capture
        )
    }

    func testStoppedTransportNeverHoldsTheScreen() {
        // Whatever else is engaged, a parked transport means the
        // performer isn't mid-take — let the system lock.
        XCTAssertFalse(disable(playing: false))
        XCTAssertFalse(disable(playing: false, launchpad: true))
        XCTAssertFalse(disable(playing: false, recorder: true))
        XCTAssertFalse(disable(playing: false, capture: true))
        XCTAssertFalse(disable(
            playing: false, launchpad: true, recorder: true, capture: true))
    }

    func testPlayingAloneDoesNotHoldTheScreen() {
        // Just listening: the user can tap the screen; normal
        // auto-lock behavior is preserved (battery, App Review).
        XCTAssertFalse(disable(playing: true))
    }

    func testAnyHandsOffSurfaceHoldsTheScreenWhilePlaying() {
        XCTAssertTrue(disable(playing: true, launchpad: true))
        XCTAssertTrue(disable(playing: true, recorder: true))
        XCTAssertTrue(disable(playing: true, capture: true))
        XCTAssertTrue(disable(
            playing: true, launchpad: true, recorder: true, capture: true))
    }
}
