// TempoEstimator.swift
//
// Beat Capture (D-024): derive BPM from onset times when no song is
// loaded. Human input is sparse and syncopated, so a single median IOI
// is a poor beat estimate (the median of a mix of eighths and whole
// notes lands on neither). Instead search for the base beat that best
// explains every inter-onset interval as an integer multiple of itself,
// then fold into a musical range. Confidence is the fraction of IOIs
// that land on that grid.

import Foundation

public enum TempoEstimator {

    /// Alignment tolerance: an IOI counts as "on grid" when its ratio to
    /// the base beat is within this fraction of the nearest integer.
    private static let alignTolerance = 0.15

    /// Estimate tempo from onset times.
    /// - Returns: `(bpm, confidence)`; confidence in [0, 1]. Callers
    ///   treat low confidence as "ask the user".
    public static func estimate(
        onsetTimesSec: [Double],
        minBPM: Double = 60,
        maxBPM: Double = 180
    ) -> (bpm: Double, confidence: Double) {
        guard onsetTimesSec.count >= 2 else { return (0, 0) }

        let sorted = onsetTimesSec.sorted()
        var iois: [Double] = []
        iois.reserveCapacity(sorted.count - 1)
        for i in 1..<sorted.count {
            let d = sorted[i] - sorted[i - 1]
            if d > 1e-3 { iois.append(d) }
        }
        guard !iois.isEmpty else { return (0, 0) }

        // Candidate base beats: each IOI and its subdivisions (an IOI may
        // be the beat, or 2–4 beats spanning a rest). The true beat is
        // whichever explains the most IOIs as clean integer multiples.
        // Floor keeps the grid from getting so dense every interval
        // "aligns" (which would overfit ragged human timing).
        var candidates: [Double] = []
        for ioi in iois {
            for div in 1...4 {
                let base = ioi / Double(div)
                if base >= 0.1 { candidates.append(base) }
            }
        }

        var bestBase = 0.0
        var bestCoverage = -1.0
        for base in candidates {
            let (coverage, _) = score(iois: iois, base: base)
            // Prefer higher coverage; on a tie prefer the larger base
            // (the simpler explanation — fewer subdivisions).
            if coverage > bestCoverage
                || (coverage == bestCoverage && base > bestBase) {
                bestBase = base
                bestCoverage = coverage
            }
        }
        guard bestBase > 1e-3 else { return (0, 0) }

        let bpm = fold(60.0 / bestBase, minBPM: minBPM, maxBPM: maxBPM)
        return (bpm, max(0, bestCoverage))
    }

    /// Coverage (fraction of IOIs on the base grid) and mean normalised
    /// error of a candidate base beat.
    private static func score(
        iois: [Double], base: Double
    ) -> (coverage: Double, error: Double) {
        guard base > 1e-3 else { return (0, .infinity) }
        var aligned = 0
        var errorSum = 0.0
        for ioi in iois {
            let ratio = ioi / base
            let n = ratio.rounded()
            guard n >= 1 else { continue }
            let normErr = abs(ratio - n) / n
            errorSum += normErr
            if normErr <= alignTolerance { aligned += 1 }
        }
        return (Double(aligned) / Double(iois.count), errorSum / Double(iois.count))
    }

    /// Fold a tempo into [minBPM, maxBPM] by octave (×2 / ÷2).
    static func fold(_ bpm: Double, minBPM: Double, maxBPM: Double) -> Double {
        guard bpm > 0 else { return 0 }
        var b = bpm
        while b < minBPM { b *= 2 }
        while b > maxBPM { b /= 2 }
        return b
    }
}
