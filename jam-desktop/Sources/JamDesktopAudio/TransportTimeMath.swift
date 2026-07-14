// TransportTimeMath.swift
//
// Rate-aware song-time → host-time delay math, ported verbatim from
// the mobile app. At practice rate r a song-time delta of Δ
// song-seconds spans Δ / r wall-clock seconds — the transport clock
// advances slower, so scheduled events (click track, quantized pad
// hits in M5) must sit proportionally further out in host time.

import Foundation

public enum TransportTimeMath {

    /// Wall-clock seconds until song-time `targetSong` is reached,
    /// given the clock currently reads `nowSong` and advances at
    /// `rate` song-seconds per wall-clock second. Rate is floored at
    /// a tiny epsilon so a rogue zero can't divide by zero.
    public static func scaledDelaySeconds(
        targetSong: Double, nowSong: Double, rate: Double
    ) -> Double {
        (targetSong - nowSong) / max(rate, 0.0001)
    }

    /// Host-time tick offset until `targetSong`, or nil when the
    /// delay is negligible (≤ 1 ms wall-clock) — the caller should
    /// schedule immediately with `at: nil`. Also nil for past targets
    /// (callers decide their own staleness policy from the unscaled
    /// song-time delta).
    public static func hostDelayTicks(
        targetSong: Double, nowSong: Double,
        rate: Double, ticksPerSecond: Double
    ) -> UInt64? {
        let delaySec = scaledDelaySeconds(
            targetSong: targetSong, nowSong: nowSong, rate: rate)
        if delaySec <= 0.001 { return nil }
        return UInt64(delaySec * ticksPerSecond)
    }
}
