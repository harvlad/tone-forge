// ChordLaneSelection.swift
//
// Picks which chord lane the native chord surfaces (Perform ribbon,
// Learn, Rehearsal, co-open bridge) render. This is the desktop port
// of jam.js `_richestChordLane` (commit d6f483a1): the backend's flat
// `timeline.chords` is the legacy "other" lane, which for many songs
// is a sparse residual ribbon while a guitar/bass lane carries the
// real progression. When the bundle ships per-stem lanes we default to
// the one with the RICHEST coverage instead so the highlight follows
// the song rather than a 10%-covered residual.
//
// Selection rule (bit-for-bit the web rule so the browser and native
// agree on the same lane for the same song):
//   - coverage = summed chord-region seconds, NOT region count, so a
//     lane of many half-beat slivers can't outrank one honest lane;
//   - `vocals` / `drums` lanes are excluded — they trace the melody /
//     hallucinate harmony on unpitched material (jam.js drops them from
//     rawChordsByStem for the same reason);
//   - ties break by stem name (sorted) so the pick is deterministic;
//   - no usable per-stem lane ⇒ fall back to the flat `chords` lane,
//     so legacy bundles behave exactly as before.

import Foundation
import ToneForgeEngine

extension BundleTimeline {

    /// Stems whose "chord" lanes are melody-traced / unpitched noise,
    /// not harmony. Mirrors the jam.js exclusion list.
    private static let nonHarmonicLanes: Set<String> = ["vocals", "drums"]

    /// The per-stem chord lane with the richest coverage, or the flat
    /// `chords` lane when no per-stem harmony lane is available.
    public var richestChordLane: [ChordEvent] {
        guard let byStem = chordsByStem, !byStem.isEmpty else { return chords }

        var best: [ChordEvent]?
        var bestCoverage = -1.0
        // Sorted names → deterministic tie-break, matching web.
        for name in byStem.keys.sorted() {
            if Self.nonHarmonicLanes.contains(name) { continue }
            guard let lane = byStem[name], !lane.isEmpty else { continue }
            let coverage = lane.reduce(0.0) { $0 + max(0, $1.end - $1.start) }
            if coverage > bestCoverage {
                bestCoverage = coverage
                best = lane
            }
        }
        // Every per-stem lane was empty / non-harmonic → legacy lane.
        return best ?? chords
    }
}
