// LearnScorer.swift
//
// Pure scoring core for Learn mode (redesign Phase 8). A practice
// pass is one loop through a section: the user's pad presses are
// buffered as (songTime, chordSymbol) pairs, and when the transport
// wraps we score the pass against the bundle's chord timeline.
//
// Contract (pinned by LearnScorerTests):
//   - A press is a HIT when the pressed chord matches the timeline
//     chord active at the press time. Matching is collapsed identity:
//     same root pitch class (enharmonics collapse via ChordParser)
//     and same collapsed quality (maj7 → maj, min7 → min,
//     dom7 → maj) — playing Dm against a Dm7 timeline counts.
//   - Chord-window edges get ±150 ms grace: a press just before a
//     change may match either neighbour.
//   - accuracy  = hits / presses (0 when no presses).
//   - coverage  = fraction of chord windows in the section with ≥1
//     hit (1.0 when the section has no chords).
//   - isPassing = full coverage AND accuracy ≥ 0.8 AND ≥1 hit —
//     one passing pass marks the section learned.
//   - Streaks fold across the whole practice session (not per pass):
//     `foldStreak` is the single-step reducer the controller applies
//     on every press.

import Foundation

/// One buffered practice press: where the playhead was, and which
/// chord pad the user hit.
public struct LearnPress: Sendable, Equatable {
    public let songTime: Double
    public let symbol: String

    public init(songTime: Double, symbol: String) {
        self.songTime = songTime
        self.symbol = symbol
    }
}

/// Score for one completed practice pass over a section.
public struct LearnPassResult: Sendable, Equatable {
    public let hits: Int
    public let misses: Int
    /// Fraction of the section's chord windows that received at
    /// least one hit. 1.0 for chord-less sections.
    public let coverage: Double
    /// hits / presses; 0 when the pass had no presses.
    public let accuracy: Double
    /// Full coverage + accuracy ≥ 0.8 + at least one hit.
    public let isPassing: Bool

    public init(
        hits: Int,
        misses: Int,
        coverage: Double,
        accuracy: Double,
        isPassing: Bool
    ) {
        self.hits = hits
        self.misses = misses
        self.coverage = coverage
        self.accuracy = accuracy
        self.isPassing = isPassing
    }
}

public enum LearnScorer {

    /// Edge grace around chord-window boundaries.
    public static let edgeGraceSec = 0.15

    /// Accuracy floor for a passing pass.
    public static let passingAccuracy = 0.8

    // MARK: - Chord identity

    /// Collapse fine quality buckets to the practice-pad vocabulary:
    /// sevenths fold onto their triad family (maj7 → maj,
    /// min7 → min, dom7 → maj — the pads offer plain triads).
    static func collapsed(_ quality: ChordQuality) -> ChordQuality {
        switch quality {
        case .maj7, .dom7: return .maj
        case .min7:        return .min
        default:           return quality
        }
    }

    /// Collapsed-identity chord equality. False when either symbol
    /// fails to parse.
    public static func matches(pressed: String, target: String) -> Bool {
        guard let p = ChordParser.parse(pressed),
              let t = ChordParser.parse(target)
        else { return false }
        return p.root == t.root && collapsed(p.quality) == collapsed(t.quality)
    }

    // MARK: - Per-press evaluation

    /// True when the press matches any chord window containing its
    /// time (windows expanded by `graceSec` on both edges). Used by
    /// the practice overlay for the immediate hit/miss flash.
    public static func isHit(
        press: LearnPress,
        chords: [ChordEvent],
        graceSec: Double = edgeGraceSec
    ) -> Bool {
        for chord in chords {
            guard press.songTime >= chord.start - graceSec,
                  press.songTime < chord.end + graceSec
            else { continue }
            if matches(pressed: press.symbol, target: chord.symbol) {
                return true
            }
        }
        return false
    }

    // MARK: - Pass scoring

    /// Score one pass over the section `[sectionStart, sectionEnd)`.
    /// Only chord windows overlapping the section count toward
    /// coverage; presses are judged against those same windows.
    public static func score(
        presses: [LearnPress],
        chords: [ChordEvent],
        sectionStart: Double,
        sectionEnd: Double,
        graceSec: Double = edgeGraceSec
    ) -> LearnPassResult {
        let windows = chords.filter {
            $0.end > sectionStart && $0.start < sectionEnd
        }

        var hits = 0
        var hitWindows = Set<Int>()
        for press in presses {
            var pressHit = false
            for (idx, chord) in windows.enumerated() {
                guard press.songTime >= chord.start - graceSec,
                      press.songTime < chord.end + graceSec
                else { continue }
                if matches(pressed: press.symbol, target: chord.symbol) {
                    pressHit = true
                    hitWindows.insert(idx)
                }
            }
            if pressHit { hits += 1 }
        }

        let misses = presses.count - hits
        let coverage = windows.isEmpty
            ? 1.0
            : Double(hitWindows.count) / Double(windows.count)
        let accuracy = presses.isEmpty
            ? 0.0
            : Double(hits) / Double(presses.count)
        let isPassing = hits > 0
            && coverage >= 1.0
            && accuracy >= passingAccuracy

        return LearnPassResult(
            hits: hits,
            misses: misses,
            coverage: coverage,
            accuracy: accuracy,
            isPassing: isPassing
        )
    }

    // MARK: - Streak

    /// Single-step streak reducer: a hit extends the current run and
    /// may set a new longest; a miss resets the run (longest keeps).
    public static func foldStreak(
        current: Int,
        longest: Int,
        hit: Bool
    ) -> (current: Int, longest: Int) {
        if hit {
            let run = current + 1
            return (run, max(run, longest))
        }
        return (0, longest)
    }
}
