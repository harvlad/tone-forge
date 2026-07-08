// TimeSigEstimator.swift
//
// Beats-per-bar estimate from the analysed beat/downbeat grids
// (D-022 Learn redesign). The backend doesn't emit a time signature,
// but downbeats partition the beat list into bars — the modal beat
// count per bar IS the numerator for any song with a stable meter.
// Feeds the Learn tab's "4/4" stat chip.

import Foundation

public enum TimeSigEstimator {

    /// Modal number of beats between consecutive downbeats, or nil
    /// when there isn't enough timing data (fewer than two downbeats
    /// or no beats). Ties break toward the smaller numerator so a
    /// 50/50 4-vs-8 ambiguity reads as 4.
    public static func numerator(
        beats: [Double], downbeats: [Double]
    ) -> Int? {
        guard downbeats.count >= 2, !beats.isEmpty else { return nil }
        let epsilon = 1e-3

        var counts: [Int: Int] = [:]
        for i in 0..<(downbeats.count - 1) {
            let barStart = downbeats[i] - epsilon
            let barEnd = downbeats[i + 1] - epsilon
            let n = beats.lazy.filter { $0 >= barStart && $0 < barEnd }.count
            if n > 0 { counts[n, default: 0] += 1 }
        }

        return counts.min { a, b in
            (a.value, -a.key) > (b.value, -b.key)
        }?.key
    }
}
