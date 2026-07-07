// BarMath.swift
//
// Bar arithmetic over the analysed timeline (redesign Phase 5).
// Downbeats are the source of truth when the analysis produced
// them; otherwise a fixed tempo grid (tempoBpm + beatsPerBar)
// approximates. Feeds Learn's Section View ("bar 5: Bb", "4 bars
// each"), the Jam chord display ("Next Chord in 2 bars"), and the
// practice progress readout ("Progress 8/32 bars").

import Foundation

public enum BarMath {

    /// 0-based bar index at time `t`. With downbeats: the index of
    /// the last downbeat at or before `t` (clamped to 0 before the
    /// first). With a tempo fallback: `t` divided into fixed-length
    /// bars from time zero. Nil when neither source is available.
    public static func barIndex(
        at t: Double,
        downbeats: [Double],
        tempoBpm: Double? = nil,
        beatsPerBar: Int = 4
    ) -> Int? {
        if !downbeats.isEmpty {
            var idx = 0
            for (i, d) in downbeats.enumerated() where d <= t {
                idx = i
            }
            return idx
        }
        guard let dur = barDuration(tempoBpm: tempoBpm, beatsPerBar: beatsPerBar)
        else { return nil }
        return max(0, Int(t / dur))
    }

    /// Whole bars from `now` forward to `target` (0 when target is
    /// within the current bar or behind). Nil without timing data.
    public static func barsUntil(
        _ target: Double,
        from now: Double,
        downbeats: [Double],
        tempoBpm: Double? = nil,
        beatsPerBar: Int = 4
    ) -> Int? {
        guard
            let a = barIndex(
                at: now, downbeats: downbeats,
                tempoBpm: tempoBpm, beatsPerBar: beatsPerBar
            ),
            let b = barIndex(
                at: target, downbeats: downbeats,
                tempoBpm: tempoBpm, beatsPerBar: beatsPerBar
            )
        else { return nil }
        return max(0, b - a)
    }

    /// Number of bars in [start, end). Downbeat-driven when
    /// available, else tempo grid. Zero without timing data.
    public static func barCount(
        start: Double,
        end: Double,
        downbeats: [Double],
        tempoBpm: Double? = nil,
        beatsPerBar: Int = 4
    ) -> Int {
        boundaries(
            start: start, end: end, downbeats: downbeats,
            tempoBpm: tempoBpm, beatsPerBar: beatsPerBar
        ).count
    }

    /// Bar start times inside [start, end). The region start always
    /// opens bar 1 (an intro pickup shorter than a bar still counts
    /// as a bar); subsequent boundaries come from downbeats strictly
    /// inside the region, or the tempo grid.
    static func boundaries(
        start: Double,
        end: Double,
        downbeats: [Double],
        tempoBpm: Double?,
        beatsPerBar: Int
    ) -> [Double] {
        guard end > start else { return [] }
        let epsilon = 1e-3
        if !downbeats.isEmpty {
            var out = [start]
            for d in downbeats where d > start + epsilon && d < end - epsilon {
                out.append(d)
            }
            return out
        }
        guard let dur = barDuration(tempoBpm: tempoBpm, beatsPerBar: beatsPerBar)
        else { return [] }
        var out: [Double] = []
        var t = start
        while t < end - epsilon {
            out.append(t)
            t += dur
        }
        return out
    }

    private static func barDuration(
        tempoBpm: Double?, beatsPerBar: Int
    ) -> Double? {
        guard let bpm = tempoBpm, bpm > 0, beatsPerBar > 0 else { return nil }
        return 60.0 / bpm * Double(beatsPerBar)
    }
}

// MARK: - Section bar strip

/// One numbered bar of a section, with the chord sounding at its
/// start. Feeds the Learn Section View's bar strip.
public struct SectionBar: Sendable, Equatable {
    /// 1-based bar number within the section.
    public let number: Int
    public let startSec: Double
    public let endSec: Double
    /// Chord active at the bar start; nil in chord-less gaps.
    public let chordSymbol: String?

    public init(
        number: Int, startSec: Double, endSec: Double, chordSymbol: String?
    ) {
        self.number = number
        self.startSec = startSec
        self.endSec = endSec
        self.chordSymbol = chordSymbol
    }
}

public enum SectionBars {

    /// Numbered bar strip for a section. Bars come from downbeats
    /// (tempo-grid fallback); each carries the chord that is sounding
    /// at its start. Empty when no timing data exists.
    public static func bars(
        section: SectionEvent,
        downbeats: [Double],
        chords: [ChordEvent],
        tempoBpm: Double? = nil,
        beatsPerBar: Int = 4
    ) -> [SectionBar] {
        let starts = BarMath.boundaries(
            start: section.start, end: section.end,
            downbeats: downbeats, tempoBpm: tempoBpm,
            beatsPerBar: beatsPerBar
        )
        let epsilon = 1e-3
        return starts.enumerated().map { i, t in
            let barEnd = i + 1 < starts.count ? starts[i + 1] : section.end
            let chord = chords.first {
                $0.start <= t + epsilon && t + epsilon < $0.end
            }
            return SectionBar(
                number: i + 1,
                startSec: t,
                endSec: barEnd,
                chordSymbol: chord?.symbol
            )
        }
    }
}
