// ClickGrid.swift
//
// Pure click-track math, trimmed port of the mobile MetronomeGrid
// (no subdivision — one click per beat, downbeat accented). Lives in
// JamDesktopCore so the scheduling window walk tests headless; the
// AVFoundation scheduler in JamDesktopAudio consumes it.

import Foundation

public struct ClickGrid: Sendable, Equatable {

    public struct Click: Sendable, Equatable {
        /// Grid index (beat number since song time 0; negative during
        /// count-in).
        public let index: Int
        /// Song-time of the click in seconds.
        public let timeSeconds: Double
        /// True on bar downbeats (index ≡ 0 mod beatsPerBar).
        public let isAccent: Bool
    }

    public let bpm: Double
    public let beatsPerBar: Int

    public init(bpm: Double, beatsPerBar: Int = 4) {
        self.bpm = max(bpm, 1)
        self.beatsPerBar = max(beatsPerBar, 1)
    }

    public var secondsPerClick: Double { 60.0 / bpm }

    /// First grid index whose time is >= `t` (epsilon-tolerant so a
    /// query exactly on a click returns that click, not the next).
    public func clickIndex(onOrAfter t: Double) -> Int {
        Int(((t / secondsPerClick) - 1e-9).rounded(.up))
    }

    public func time(ofClickIndex index: Int) -> Double {
        Double(index) * secondsPerClick
    }

    /// Floored modulo so negative indices (count-in) accent correctly:
    /// -4 is a downbeat in 4/4, -1 is beat 4 of the previous bar.
    public func isAccent(clickIndex index: Int) -> Bool {
        let m = index % beatsPerBar
        return (m < 0 ? m + beatsPerBar : m) == 0
    }

    /// All clicks with index >= `fromClickIndex` and time < `before`
    /// (the rolling look-ahead window walk).
    public func clicks(fromClickIndex: Int, before: Double) -> [Click] {
        var out: [Click] = []
        var idx = fromClickIndex
        while true {
            let t = time(ofClickIndex: idx)
            if t >= before { break }
            out.append(Click(index: idx, timeSeconds: t, isAccent: isAccent(clickIndex: idx)))
            idx += 1
        }
        return out
    }
}
