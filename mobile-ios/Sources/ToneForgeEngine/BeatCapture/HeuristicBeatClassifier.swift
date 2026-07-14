// HeuristicBeatClassifier.swift
//
// Beat Capture (D-024): threshold-tree drum classifier. Fast, pure,
// deterministic. Ordering matters — most distinctive cues first
// (low-frequency kick, bright hats), ambiguous mid-band noise last.
// Below `confidenceFloor` the verdict collapses to `.perc` so a
// low-confidence wrong guess never lands on the grid.

import Foundation

/// Heuristic `BeatClassifier` built on onset spectral features.
public struct HeuristicBeatClassifier: BeatClassifier {

    /// Verdicts scoring below this collapse to `.perc`.
    public var confidenceFloor: Double

    public init(confidenceFloor: Double = 0.3) {
        self.confidenceFloor = confidenceFloor
    }

    public func classify(_ f: OnsetFeatures) -> BeatClassification {
        let graded = grade(f)
        if graded.confidence < confidenceFloor {
            return BeatClassification(role: .perc, confidence: graded.confidence)
        }
        return graded
    }

    /// Raw verdict before the `.perc` floor is applied.
    func grade(_ f: OnsetFeatures) -> BeatClassification {
        // Substantial low-frequency energy = a thump, never a bright
        // click. Commit to kick FIRST, even when a slappy attack pushes
        // the whole-slice centroid up into hat territory (a chest beat
        // has a sharp transient but real sub-150 Hz body).
        if f.lowBandRatio >= 0.2 {
            let lowCue = Double(min(f.lowBandRatio / 0.45, 1))
            return BeatClassification(role: .kick, confidence: 0.55 + 0.4 * lowCue)
        }

        // Dark region (below the mid band). A phone mic rolls off the
        // sub-bass, so many low thumps never reach the 808 low-band
        // ratio above — split them by noisiness instead:
        //   tonal / resonant thump (chest boom, beatbox kick) → kick
        //   broadband noisy slap (hand/stomach slap, snare)     → snare
        if f.centroidHz < 400 {
            if f.pitchedness < 0.35 {
                let noisy = Double(min((0.35 - f.pitchedness) / 0.35, 1))
                return BeatClassification(role: .snare, confidence: 0.5 + 0.35 * noisy)
            }
            let darkCue = Double(min((500 - f.centroidHz) / 500, 1))
            let lowCue = Double(min(f.lowBandRatio / 0.45, 1))
            return BeatClassification(
                role: .kick, confidence: 0.6 + 0.4 * max(darkCue, lowCue)
            )
        }

        // Hats: very bright + noisy + no low-end body. The lowBand guard
        // is redundant with the early kick check but keeps the intent
        // explicit — a real hat has essentially zero sub-150 Hz energy.
        if f.centroidHz >= 4000 && f.zcr > 0.12 && f.lowBandRatio < 0.2 {
            let bright = Double(min((f.centroidHz - 4000) / 4000, 1))
            let conf = 0.6 + 0.4 * bright
            if f.durationSec < 0.09 {
                return BeatClassification(role: .closedHat, confidence: conf)
            }
            return BeatClassification(role: .openHat, confidence: conf)
        }

        // Rim: sharp bright click, very short, fast attack.
        if f.centroidHz >= 2200 && f.attackSec <= 0.006 && f.durationSec < 0.06 {
            return BeatClassification(role: .rim, confidence: 0.55)
        }

        // Snare / clap: mid-band body. Real drum noise bursts are highly
        // unpitched, but hand / body slaps are only semi-tonal, so the
        // ceiling is generous (< 0.55) to keep them off the perc bucket.
        // Longer, multi-lobe decay → clap; shorter → snare.
        if f.pitchedness < 0.55 && f.centroidHz >= 400 && f.centroidHz < 4000 {
            let noisy = Double(min((0.55 - f.pitchedness) / 0.55, 1))
            let conf = 0.45 + 0.4 * noisy
            if f.durationSec > 0.12 {
                return BeatClassification(role: .clap, confidence: conf)
            }
            return BeatClassification(role: .snare, confidence: conf)
        }

        // No clean acoustic match. Rather than dumping to perc, snap to
        // the nearest real drum by brightness — the goal is an
        // intentional-sounding beat, not a 1:1 transcription. Biased low
        // so soft body thumps (chest beats) land on kick.
        if f.centroidHz < 1200 {
            return BeatClassification(role: .kick, confidence: 0.45)
        }
        if f.centroidHz < 3000 {
            return BeatClassification(role: .snare, confidence: 0.45)
        }
        return BeatClassification(role: .closedHat, confidence: 0.45)
    }
}
