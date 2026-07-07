// LoopRegion.swift
//
// A/B loop region for section practice (redesign Phase 5). Pure
// value type — the mobile AppState owns one as @Published state and
// applies `wrapTarget(now:)` from its 30 Hz UI tick, wrapping via
// the regular seek path so stems/metronome/chords all re-anchor
// together. Tick-driven looping means up to ~33 ms of wrap jitter,
// which is fine for practice loops (documented in D-018).

import Foundation

/// Half-open time region [startSec, endSec) in song seconds.
public struct LoopRegion: Sendable, Equatable {
    public let startSec: Double
    public let endSec: Double

    /// Nil unless the region has positive length.
    public init?(startSec: Double, endSec: Double) {
        guard endSec > startSec else { return nil }
        self.startSec = startSec
        self.endSec = endSec
    }

    public var lengthSec: Double { endSec - startSec }

    public func contains(_ t: Double) -> Bool {
        t >= startSec && t < endSec
    }

    /// Where the transport should jump, if anywhere, given the
    /// current playhead. Returns `startSec` only when the playhead
    /// sits within `maxOvershoot` seconds *past* the end — i.e. it
    /// just crossed the boundary while playing. A scrub landing far
    /// beyond the region does not snap back (returns nil), so the
    /// user can always escape the loop by seeking away.
    public func wrapTarget(
        now: Double,
        maxOvershoot: Double = 1.0
    ) -> Double? {
        guard now >= endSec, now < endSec + maxOvershoot else { return nil }
        return startSec
    }
}
