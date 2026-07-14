// GoalTimer.swift
//
// Session goal timer, parity with jam.js's 15-minute soft target
// (_REHEARSAL_SESSION_GOAL_MIN / _updateSessionGoal): starts when the
// rehearsal view is entered, restarts on every re-entry (fresh
// session budget), shows elapsed vs goal, flips complete at 100%.
// Pure value type — callers pass `now`, so tests never sleep.

import Foundation

public struct GoalTimer: Equatable, Sendable {
    /// Soft target, minutes. Web parity: 15.
    public let goalMinutes: Double

    public private(set) var startedAt: Date?

    public init(goalMinutes: Double = 15) {
        self.goalMinutes = max(goalMinutes, 0.1)
        self.startedAt = nil
    }

    public var isRunning: Bool { startedAt != nil }

    /// (Re)start the session clock. Called on rehearsal-view enter;
    /// web resets on every enter, not just cold load.
    public mutating func start(now: Date) {
        startedAt = now
    }

    /// Stop tracking (view leave). Elapsed reads return zero after.
    public mutating func stop() {
        startedAt = nil
    }

    public func elapsedMinutes(now: Date) -> Double {
        guard let startedAt else { return 0 }
        return max(0, now.timeIntervalSince(startedAt) / 60)
    }

    /// 0…1, clamped (web clamps the bar at 100%).
    public func progress(now: Date) -> Double {
        min(1, elapsedMinutes(now: now) / goalMinutes)
    }

    public func isComplete(now: Date) -> Bool {
        progress(now: now) >= 1
    }

    /// "15 min · N min in" (web display string).
    public func label(now: Date) -> String {
        let elapsed = Int(elapsedMinutes(now: now).rounded(.down))
        return "\(Int(goalMinutes)) min · \(elapsed) min in"
    }
}
