// TrimSelection.swift
//
// Pure trim-range math for the Studio waveform view (fractions of the
// full file ↔ seconds). Mirrors studio.html's WaveformTrimmer handle
// behavior: clamped to [0,1], minimum selection width 0.5s.

import Foundation

public struct TrimSelection: Equatable, Sendable {
    /// Full-file duration in seconds the fractions map onto.
    public let duration: Double
    /// Selection bounds as fractions of the full file, 0...1.
    public private(set) var startFraction: Double
    public private(set) var endFraction: Double

    /// Minimum selection width in seconds (web parity).
    public static let minWidthSec = 0.5

    public init(duration: Double) {
        self.duration = max(0, duration)
        self.startFraction = 0
        self.endFraction = 1
    }

    public var startSeconds: Double { startFraction * duration }
    public var endSeconds: Double { endFraction * duration }
    public var selectedSeconds: Double { endSeconds - startSeconds }

    /// True when the whole file is selected — no trim fields should be
    /// sent on the wire.
    public var isFullRange: Bool {
        startFraction <= 0 && endFraction >= 1
    }

    private var minWidthFraction: Double {
        duration > 0 ? min(1, Self.minWidthSec / duration) : 1
    }

    public mutating func dragStart(to fraction: Double) {
        startFraction = min(
            max(0, fraction), endFraction - minWidthFraction)
    }

    public mutating func dragEnd(to fraction: Double) {
        endFraction = max(
            min(1, fraction), startFraction + minWidthFraction)
    }

    /// Slide the whole selection, preserving its width.
    public mutating func move(by deltaFraction: Double) {
        let width = endFraction - startFraction
        var newStart = startFraction + deltaFraction
        newStart = min(max(0, newStart), 1 - width)
        startFraction = newStart
        endFraction = newStart + width
    }

    public mutating func reset() {
        startFraction = 0
        endFraction = 1
    }
}
