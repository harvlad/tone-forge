// SequencePulse.swift
//
// Animation state for a running sequencer pattern on a pad.
// Emitted by SequencePadManager on each step change, consumed by
// grid UI (flash/meter) and hardware LED driver (color pulse).

import Foundation

public struct SequencePulse: Equatable, Sendable {
    /// Current step index (0..<stepCount).
    public let step: Int

    /// Total steps in the pattern (8, 16, or 32).
    public let stepCount: Int

    /// Duration of each step in seconds (derived from BPM).
    public let secondsPerStep: Double

    /// True on quarter-note boundaries (steps 0, 4, 8, ...).
    public var isDownbeat: Bool { step % 4 == 0 }

    /// Fractional progress through the pattern (0..<1).
    public var progress: Double {
        stepCount > 0 ? Double(step) / Double(stepCount) : 0
    }

    public init(step: Int, stepCount: Int, secondsPerStep: Double) {
        self.step = step
        self.stepCount = stepCount
        self.secondsPerStep = secondsPerStep
    }
}
