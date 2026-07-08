// JamPadGrid12Mapping.swift
//
// D-022 Phase 5: the Jam tab's 12 big performance pads, mapped onto
// the 8×8 open-jam grid so every press keeps flowing through
// ModeCoordinator.touchPadDown → ContributionEventBus. That single
// indirection preserves the D-015 invariant for free: session
// capture, Launchpad LED mirroring and replay all keep working
// because the 12-pad surface is just a different window onto the
// same grid.
//
// Layout: with a key, the pads are the first 12 scale tones from the
// key root at/above E2 (so pad 8 is pad 1 an octave up — a proper
// hand-sized instrument). Without a key, chromatic from E2.
//
// Coordinate choice: the open-jam grid ascends +1 semitone per
// column and +5 per row, so semitone offset o from E2 lives at
// (row o/5 + 1, col o%5 + 1) — the canonical spot with col ≤ 5.
// Max needed offset is 30 (root ≤ 11, two octaves of a scale span
// ≤ 12+11=23... conservatively root 11 + 19 = 30) → row ≤ 7, always
// a valid PadIndex.

import Foundation

public enum JamPadGrid12Mapping {

    /// Number of performance pads on the surface.
    public static let padCount = 12

    /// The 12 grid pads for a key, ascending by pitch. Each returned
    /// PadIndex is a valid open-jam grid coordinate whose
    /// `OpenJamGrid.midi(for:)` is the pad's note.
    public static func pads(key: MusicalKey?) -> [PadIndex] {
        offsets(key: key).map { o in
            PadIndex.at(row: o / 5 + 1, col: o % 5 + 1)
        }
    }

    /// Semitone offsets from OPEN_JAM_BASE_MIDI (E2), ascending.
    static func offsets(key: MusicalKey?) -> [Int] {
        guard let key = key else {
            // No key info: chromatic dozen from E2.
            return Array(0..<padCount)
        }
        // First scale root at/above E2, as a semitone offset.
        let root = ((key.root.rawValue - OPEN_JAM_BASE_MIDI) % 12 + 12) % 12
        let intervals = ScaleIntervals.intervals(for: key.scale)
        let n = intervals.count
        return (0..<padCount).map { i in
            root + intervals[i % n] + (i / n) * 12
        }
    }
}
