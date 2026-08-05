// BeatClock.swift
//
// Beat-level phase over the analysed timeline (PERFORM_PARITY spec 1).
// Sits below BarMath (which is bar-level) and feeds beat-synced
// performance FX (gater subdivision, flanger LFO, stopper braking),
// quantized chop/loop launches, and — later — 24 PPQN MIDI clock out
// and Ableton Link phase.
//
// Pure value type: it takes song-time seconds as input rather than
// owning a clock, so it is trivially testable AND safe to call from an
// audio render thread (no actor hop, no allocation on the hot path).
//
// Source of truth mirrors BarMath: the analysis `beats` array drives
// beat phase (absorbs tempo drift / rubato) and `downbeats` drives bar
// phase. When an array is missing or too short, a fixed tempo grid
// from time zero (tempoBpm + beatsPerBar) approximates. With neither,
// the phase accessors return nil and the caller falls back to a free
// (non-synced) rate.
//
// Song-time note: TransportClock already folds playback `rate` into
// songSeconds, and `beatDuration` here is a song-time interval
// (60/bpm), so anything synced through BeatClock stays locked to the
// music regardless of practice-speed changes.

import Foundation

public struct BeatClock: Sendable, Equatable {

    /// Song tempo in BPM, when the analysis produced one. Drives the
    /// grid fallback + `beatDuration`.
    public let tempoBpm: Double?
    /// Beats per bar (time-signature numerator). Defaults to 4.
    public let beatsPerBar: Int
    /// Analysis beat times (seconds). Source of truth for beat phase.
    public let beats: [Double]
    /// Analysis downbeat times (seconds). Source of truth for bar phase.
    public let downbeats: [Double]

    public init(
        tempoBpm: Double? = nil,
        beatsPerBar: Int = 4,
        beats: [Double] = [],
        downbeats: [Double] = []
    ) {
        self.tempoBpm = tempoBpm
        self.beatsPerBar = beatsPerBar
        self.beats = beats
        self.downbeats = downbeats
    }

    /// Convenience: build straight from a bundle timeline + meta tempo.
    public init(timeline: BundleTimeline, tempoBpm: Double?, beatsPerBar: Int = 4) {
        self.init(
            tempoBpm: tempoBpm,
            beatsPerBar: beatsPerBar,
            beats: timeline.beats,
            downbeats: timeline.downbeats
        )
    }

    /// One beat's length in song-seconds (60/bpm), or nil without tempo.
    public var beatDuration: Double? {
        guard let bpm = tempoBpm, bpm > 0 else { return nil }
        return 60.0 / bpm
    }

    /// True when at least one phase source (array or tempo) exists.
    public var hasTiming: Bool {
        beats.count >= 2 || downbeats.count >= 2 || (tempoBpm ?? 0) > 0
    }

    // MARK: - Phase

    /// 0..1 phase within the current beat at song-time `t`. Prefers the
    /// beats array (drift-accurate); falls back to the fixed tempo grid.
    /// Nil when neither source is available.
    public func beatPhase(at t: Double) -> Double? {
        phaseFromMarkers(t, markers: beats) ?? phaseFromGrid(t, period: beatDuration)
    }

    /// 0..1 phase within the current bar at song-time `t`. Prefers the
    /// downbeats array; falls back to a fixed bar grid.
    public func barPhase(at t: Double) -> Double? {
        phaseFromMarkers(t, markers: downbeats)
            ?? phaseFromGrid(t, period: beatDuration.map { $0 * Double(beatsPerBar) })
    }

    // MARK: - Quantization

    /// Song-second of the next grid line at or after `t`, where the grid
    /// spacing is `subdivisionBeats` beats (1.0 = every beat, 0.5 = 1/8
    /// notes in 4/4, 0.25 = 1/16). Used to quantize chop/loop launches
    /// to the beat grid. Requires a tempo; nil otherwise. `t` exactly on
    /// a line returns `t`.
    public func nextBoundary(after t: Double, subdivisionBeats: Double = 1.0) -> Double? {
        guard subdivisionBeats > 0, let beat = beatDuration else { return nil }
        let step = beat * subdivisionBeats
        // Anchor the grid on the first beat marker when present so the
        // quantize grid lines up with the actual downbeat, not song-zero.
        let anchor = beats.first ?? 0
        if t <= anchor { return anchor }
        let n = ((t - anchor) / step).rounded(.up)
        let line = anchor + n * step
        // Guard against float landing a hair before t.
        return line < t ? line + step : line
    }

    // MARK: - Private

    /// Phase within the interval bracketing `t` in a sorted marker
    /// array. Nil when fewer than two markers or `t` is outside the
    /// covered range (caller then tries the grid).
    private func phaseFromMarkers(_ t: Double, markers: [Double]) -> Double? {
        guard markers.count >= 2,
              let first = markers.first, let last = markers.last,
              t >= first, t < last
        else { return nil }
        // Linear scan is fine: called at most once per audio buffer and
        // marker arrays are a few thousand entries; binary search is an
        // easy later optimization if a profile ever flags it.
        var lo = markers[0]
        for m in markers.dropFirst() {
            if t < m {
                let span = m - lo
                guard span > 0 else { return 0 }
                return min(1, max(0, (t - lo) / span))
            }
            lo = m
        }
        return nil
    }

    /// Phase on a fixed grid from time zero with the given period.
    private func phaseFromGrid(_ t: Double, period: Double?) -> Double? {
        guard let p = period, p > 0 else { return nil }
        let phase = (t / p).truncatingRemainder(dividingBy: 1)
        return phase < 0 ? phase + 1 : phase
    }
}
