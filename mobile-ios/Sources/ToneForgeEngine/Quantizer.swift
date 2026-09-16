// Quantizer.swift
//
// Pure snap-to-grid logic for the SampleScheduler. Given a song-time
// tap and a QuantizeMode, returns the song-time the sample should
// actually fire at.
//
// Semantics
// ---------
//   .off      → return t as-is.
//   .eighth   → snap to next 1/8-note boundary (halfway between beats).
//   .quarter  → snap to next beat.
//   .half     → snap to next every-other-beat (aligned to the first
//               downbeat if known, else to beats[0]).
//   .bar      → snap to next downbeat.
//   .phrase   → snap to next section boundary.
//
// Grace window
// ------------
// If `songSeconds` is at most `graceSeconds` after a boundary, we
// return `songSeconds` (play now, reads as on-beat). This is what
// keeps human timing musical: a tap 40 ms late on a beat still fires
// immediately instead of being pushed to the next beat 400 ms out.
// Default 80 ms was picked to comfortably cover typical touch-input
// jitter without straying into "clearly late" territory.
//
// Fallback
// --------
// When the bundle has no beats/downbeats but does have `tempoBpm`, we
// synthesise a grid from t=0 at the appropriate beat interval. When
// there is neither beats nor tempo, the quantizer degrades to .off
// (returns t unchanged) rather than fabricating a nonsense grid.
//
// Past the last analysed boundary the grid is EXTRAPOLATED forward at
// the song tempo from that last boundary (web twin: padengine.js
// quantizeWaitSec anchored at the last real grid time, :1029-1036) —
// a late-song press still lands on-grid instead of firing instantly
// off it. Without tempo there is nothing to extrapolate with, so the
// tap plays now.
//
// Complexity: O(log n) binary search on the boundary grid + O(k) grid
// materialisation where k is bounded (~12 boundaries near t). Called
// once per pad-touch, not per audio buffer.

import Foundation

public enum Quantizer {

    /// Snap `songSeconds` to the next boundary implied by `mode`, or
    /// return `songSeconds` unchanged if within `graceSeconds` of the
    /// last boundary. See file-level doc for full semantics.
    ///
    /// - Parameters:
    ///   - songSeconds: the transport time at which the user tapped.
    ///   - mode: the quantize policy.
    ///   - beats: sorted seconds; from `BundleTimeline.beats`. May be empty.
    ///   - downbeats: sorted seconds; from `BundleTimeline.downbeats`. May be empty.
    ///   - sections: `BundleTimeline.sections`. May be empty.
    ///   - tempoBpm: `BundleMeta.tempoBpm`. Used to synthesise a grid
    ///     when beats/downbeats are absent. `nil` or `<= 0` disables
    ///     the fallback.
    ///   - graceSeconds: how late-past-boundary still counts as "on".
    ///     Default 0.08 s.
    /// - Returns: the song-time the sample should actually fire at.
    ///   Always `>= songSeconds`.
    public static func nextQuantized(
        songSeconds t: Double,
        mode: QuantizeMode,
        beats: [Double],
        downbeats: [Double],
        sections: [SectionEvent],
        tempoBpm: Double?,
        graceSeconds: Double = 0.08
    ) -> Double {
        if mode == .off { return t }
        let grid = gridFor(
            mode: mode,
            beats: beats,
            downbeats: downbeats,
            sections: sections,
            tempoBpm: tempoBpm,
            around: t
        )
        // Past the last analysed boundary: extrapolate the grid forward at
        // tempo from that boundary (web: padengine.js:1029-1036). snap()
        // would return t — instant fire, off the bar grid the loops already
        // ringing were launched on. `.phrase` has no tempo period, so it
        // keeps the play-now tail behavior.
        if let last = grid.last, t > last + graceSeconds,
           let bpm = tempoBpm, bpm > 0,
           let beatsPerUnit = Self.intervalBeats(for: mode) {
            return extrapolatedBoundary(
                t: t, last: last, unit: 60.0 / bpm * beatsPerUnit,
                graceSeconds: graceSeconds)
        }
        return snap(t: t, grid: grid, graceSeconds: graceSeconds)
    }

    /// Grid spacing in BEATS for a tempo-periodic mode; nil for modes
    /// with no tempo period (`.off`, `.phrase` — sections are events,
    /// not a lattice). Single source for extrapolation + syntheticGrid
    /// callers so the two grids can never disagree on spacing.
    static func intervalBeats(for mode: QuantizeMode) -> Double? {
        switch mode {
        case .eighth: return 0.5
        case .quarter: return 1
        case .half: return 2
        case .bar: return 4
        case .off, .phrase: return nil
        }
    }

    /// Next boundary on the constant-tempo lattice extrapolated from the
    /// LAST real grid entry: `last + k·unit`. Within `graceSeconds` past
    /// an (extrapolated) boundary → t, play now — same grace contract as
    /// snap(). Web twin: quantizeWaitSec(songNow, unit, lastBoundary)
    /// (padengine.js:409-414), whose 0-wait grace fold is the `return t`.
    static func extrapolatedBoundary(
        t: Double, last: Double, unit: Double, graceSeconds: Double
    ) -> Double {
        guard unit > 0, t > last else { return t }
        let elapsed = t - last
        let phase = elapsed.truncatingRemainder(dividingBy: unit)
        if phase <= graceSeconds { return t }
        return last + ((elapsed / unit).rounded(.down) + 1) * unit
    }

    // MARK: - Snap

    /// Snap `t` to `grid` with a grace window. Public for testability;
    /// callers should prefer `nextQuantized` which supplies the grid.
    public static func snap(t: Double, grid: [Double], graceSeconds: Double) -> Double {
        guard !grid.isEmpty else { return t }

        // Binary search: find insertion index for t (first idx with
        // grid[idx] > t).
        var lo = 0
        var hi = grid.count
        while lo < hi {
            let mid = (lo &+ hi) >> 1
            if grid[mid] <= t { lo = mid + 1 } else { hi = mid }
        }
        let nextIdx = lo
        let lastIdx = lo - 1

        // Grace: within graceSeconds after the last boundary → play now.
        if lastIdx >= 0, (t - grid[lastIdx]) <= graceSeconds {
            return t
        }
        // Snap forward.
        if nextIdx < grid.count {
            return grid[nextIdx]
        }
        // Past the last known boundary — nothing to snap to.
        return t
    }

    // MARK: - Grid construction

    /// Build the boundary grid for `mode` around `around`. The returned
    /// array is sorted ascending and covers roughly [around-4, around+8]
    /// seconds when a synthetic grid is used; when the bundle-provided
    /// arrays are used, the full arrays are returned since they are
    /// already sorted and cheap to binary-search.
    static func gridFor(
        mode: QuantizeMode,
        beats: [Double],
        downbeats: [Double],
        sections: [SectionEvent],
        tempoBpm: Double?,
        around t: Double
    ) -> [Double] {
        // Pre-first-boundary taps WAIT for the first REAL grid entry
        // (snap() selects grid[0]) — web parity: nextGridTimeSec returns
        // the first grid time when songNow is before it (padengine.js
        // :427 "songNow before the first grid time → the first grid
        // time"), so an intro tap arms until the music's actual downbeat.
        // A previous iOS-only "head-of-timeline" carve-out substituted a
        // synthetic t=0 tempo grid here, so intro taps fired on a lattice
        // the song never plays — off the first real downbeat and off
        // every other platform. The synthetic grid remains ONLY for
        // bundles that carry no grid arrays at all.
        let beatsUsable = beats
        let downbeatsUsable = downbeats

        switch mode {
        case .off:
            return []

        case .phrase:
            // Section starts, sorted; drop invalid.
            return sections.map { $0.start }.sorted()

        case .bar:
            if !downbeatsUsable.isEmpty { return downbeatsUsable }
            return syntheticGrid(intervalBeats: 4, around: t, tempoBpm: tempoBpm)

        case .quarter:
            if !beatsUsable.isEmpty { return beatsUsable }
            return syntheticGrid(intervalBeats: 1, around: t, tempoBpm: tempoBpm)

        case .half:
            if !beatsUsable.isEmpty {
                // Anchor "every other beat" on the first downbeat when
                // known; otherwise on beats[0]. Falls back gracefully
                // when the downbeat isn't exactly a beat value.
                var anchorIdx = 0
                if let firstDb = downbeatsUsable.first,
                   let idx = beatsUsable.firstIndex(where: { abs($0 - firstDb) < 1e-3 })
                {
                    anchorIdx = idx
                }
                var out: [Double] = []
                out.reserveCapacity(beatsUsable.count / 2 + 1)
                for i in beatsUsable.indices where ((i - anchorIdx) & 1) == 0 {
                    out.append(beatsUsable[i])
                }
                return out
            }
            return syntheticGrid(intervalBeats: 2, around: t, tempoBpm: tempoBpm)

        case .eighth:
            if beatsUsable.count >= 2 {
                var out: [Double] = []
                out.reserveCapacity(beatsUsable.count * 2)
                for i in 0..<(beatsUsable.count - 1) {
                    let a = beatsUsable[i]
                    let b = beatsUsable[i + 1]
                    out.append(a)
                    out.append((a + b) * 0.5)
                }
                out.append(beatsUsable[beatsUsable.count - 1])
                return out
            }
            return syntheticGrid(intervalBeats: 0.5, around: t, tempoBpm: tempoBpm)
        }
    }

    /// Synthetic uniform grid at `intervalBeats` (in beats) starting
    /// from t=0, materialised only in a small window around `t` to
    /// keep allocation bounded. Returns [] when tempo is missing.
    static func syntheticGrid(
        intervalBeats: Double,
        around t: Double,
        tempoBpm: Double?
    ) -> [Double] {
        guard let bpm = tempoBpm, bpm > 0, intervalBeats > 0 else { return [] }
        let intervalSec = 60.0 / bpm * intervalBeats
        guard intervalSec > 1e-6 else { return [] }

        let windowStart = max(0, t - 4.0)
        let windowEnd = t + 8.0
        let firstK = Int((windowStart / intervalSec).rounded(.down))

        var out: [Double] = []
        var k = firstK
        while true {
            let val = Double(k) * intervalSec
            if val > windowEnd { break }
            if val >= 0 { out.append(val) }
            k += 1
        }
        return out
    }
}
