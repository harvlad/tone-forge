// TempoEstimator.swift
//
// Beat Capture (D-024): derive BPM from onset times when no song is
// loaded. Uses the median inter-onset interval folded into a musical
// range, with confidence from how consistently the intervals align to
// integer multiples of the base beat.

import Foundation

public enum TempoEstimator {

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

        // Median IOI as the base beat estimate.
        let medianIOI = median(iois)
        guard medianIOI > 1e-3 else { return (0, 0) }

        let bpm = fold(60.0 / medianIOI, minBPM: minBPM, maxBPM: maxBPM)

        // Confidence: fraction of IOIs that sit on the medianIOI grid —
        // i.e. an integer multiple OR unit fraction of the base beat.
        // Score against medianIOI (the actual dominant interval), NOT
        // the folded beatDur: folding shifts the beat by an octave and
        // would make the most common interval score as unaligned.
        // Tolerance scales with the interval so human timing drift on
        // longer gaps is not unfairly penalised.
        var aligned = 0
        for ioi in iois {
            let ratio = ioi / medianIOI
            // Nearest integer multiple (2, 3, …) or unit fraction (½, ⅓).
            let mult = ratio.rounded()
            let frac = (1.0 / ratio).rounded()
            let hitMult = mult >= 1 && abs(ratio - mult) <= 0.2 * mult
            let hitFrac = frac >= 1 && abs((1.0 / ratio) - frac) <= 0.2 * frac
            if hitMult || hitFrac { aligned += 1 }
        }
        let confidence = Double(aligned) / Double(iois.count)

        return (bpm, confidence)
    }

    /// Fold a tempo into [minBPM, maxBPM] by octave (×2 / ÷2).
    static func fold(_ bpm: Double, minBPM: Double, maxBPM: Double) -> Double {
        guard bpm > 0 else { return 0 }
        var b = bpm
        while b < minBPM { b *= 2 }
        while b > maxBPM { b /= 2 }
        return b
    }

    static func median(_ values: [Double]) -> Double {
        guard !values.isEmpty else { return 0 }
        let s = values.sorted()
        let mid = s.count / 2
        if s.count % 2 == 0 {
            return (s[mid - 1] + s[mid]) / 2
        }
        return s[mid]
    }
}
