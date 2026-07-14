// GoalTimerTests.swift
//
// Pure timer transitions with injected dates — no sleeping.

import XCTest
@testable import JamDesktopCore

final class GoalTimerTests: XCTestCase {

    private let t0 = Date(timeIntervalSince1970: 1_700_000_000)

    func testIdleBeforeStart() {
        let timer = GoalTimer()
        XCTAssertFalse(timer.isRunning)
        XCTAssertEqual(timer.elapsedMinutes(now: t0), 0)
        XCTAssertEqual(timer.progress(now: t0), 0)
        XCTAssertFalse(timer.isComplete(now: t0))
    }

    func testElapsedAndProgress() {
        var timer = GoalTimer(goalMinutes: 15)
        timer.start(now: t0)
        let sevenAndHalf = t0.addingTimeInterval(7.5 * 60)
        XCTAssertEqual(timer.elapsedMinutes(now: sevenAndHalf), 7.5, accuracy: 0.001)
        XCTAssertEqual(timer.progress(now: sevenAndHalf), 0.5, accuracy: 0.001)
        XCTAssertFalse(timer.isComplete(now: sevenAndHalf))
    }

    func testCompletesAtGoalAndClamps() {
        var timer = GoalTimer(goalMinutes: 15)
        timer.start(now: t0)
        let twenty = t0.addingTimeInterval(20 * 60)
        XCTAssertTrue(timer.isComplete(now: twenty))
        XCTAssertEqual(timer.progress(now: twenty), 1.0)
    }

    func testRestartResetsElapsed() {
        var timer = GoalTimer(goalMinutes: 15)
        timer.start(now: t0)
        // Re-enter the view 10 minutes later: fresh session budget
        // (web resets sessionStartedAt on every enter).
        let reenter = t0.addingTimeInterval(10 * 60)
        timer.start(now: reenter)
        XCTAssertEqual(timer.elapsedMinutes(now: reenter), 0)
    }

    func testStopZeroesElapsed() {
        var timer = GoalTimer()
        timer.start(now: t0)
        timer.stop()
        XCTAssertFalse(timer.isRunning)
        XCTAssertEqual(timer.elapsedMinutes(now: t0.addingTimeInterval(300)), 0)
    }

    func testLabelFormat() {
        var timer = GoalTimer(goalMinutes: 15)
        timer.start(now: t0)
        let fourMin = t0.addingTimeInterval(4 * 60 + 30)
        XCTAssertEqual(timer.label(now: fourMin), "15 min · 4 min in")
    }

    func testClockGoingBackwardsClampsToZero() {
        var timer = GoalTimer()
        timer.start(now: t0)
        XCTAssertEqual(timer.elapsedMinutes(now: t0.addingTimeInterval(-60)), 0)
    }
}
