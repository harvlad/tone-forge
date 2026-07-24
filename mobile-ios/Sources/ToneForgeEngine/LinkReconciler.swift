// LinkReconciler.swift
//
// Pure math for reconciling Ableton Link's global tempo/phase grid with
// our song-time transport (PERFORM_PARITY spec 2A). No LinkKit import:
// the ABLLink C calls live in the app-target LinkController; this is the
// testable core it delegates the arithmetic to.
//
// Integration model (A): when Link is on, the session tempo drives
// playback. Stems time-stretch to the Link tempo (rate = linkBpm /
// songBpm — the engine already stretches via timePitch), and the song's
// bar downbeat is nudged to align with Link's phase so we play in the
// same bar as everyone else on the session.

import Foundation

public enum LinkReconciler {

    /// Playback-rate multiplier that time-stretches song content to the
    /// Link session tempo. rate = linkBpm / songBpm. Clamped to a sane
    /// stretch window so a wildly different session tempo doesn't drive
    /// the time-pitch unit into artefact territory (the caller can widen
    /// this once the stretch quality is proven on device).
    public static func stretchRatio(
        linkBpm: Double,
        songBpm: Double,
        minRatio: Double = 0.5,
        maxRatio: Double = 2.0
    ) -> Double? {
        guard linkBpm > 0, songBpm > 0 else { return nil }
        return max(minRatio, min(maxRatio, linkBpm / songBpm))
    }

    /// Shortest signed song-time nudge (seconds) that aligns our bar
    /// phase to Link's. Positive = advance the transport, negative =
    /// pull it back. Both phases are 0..1 within a bar. The result is
    /// wrapped to ±half a bar so we always take the shorter path (never
    /// jump most of a bar to catch a phase that's barely ahead).
    public static func phaseNudgeSeconds(
        linkBarPhase: Double,
        songBarPhase: Double,
        beatsPerBar: Int,
        beatDuration: Double
    ) -> Double? {
        guard beatsPerBar > 0, beatDuration > 0 else { return nil }
        let barSeconds = beatDuration * Double(beatsPerBar)
        var diff = (linkBarPhase - songBarPhase).truncatingRemainder(dividingBy: 1)
        // Wrap to (-0.5, 0.5] so we take the shortest correction.
        if diff > 0.5 { diff -= 1 }
        if diff <= -0.5 { diff += 1 }
        return diff * barSeconds
    }

    /// Whether a phase error is small enough to leave alone — avoids
    /// hunting/jitter when the transport is already locked. `tolerance`
    /// is a fraction of a bar (default ~1/128 note in 4/4).
    public static func isPhaseLocked(
        linkBarPhase: Double,
        songBarPhase: Double,
        tolerance: Double = 0.01
    ) -> Bool {
        var diff = abs((linkBarPhase - songBarPhase).truncatingRemainder(dividingBy: 1))
        if diff > 0.5 { diff = 1 - diff }
        return diff <= tolerance
    }
}
