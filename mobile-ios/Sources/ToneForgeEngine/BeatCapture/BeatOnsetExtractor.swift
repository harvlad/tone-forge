// BeatOnsetExtractor.swift
//
// Beat Capture (D-024): turns a raw performance buffer into a list of
// classified, velocity-scaled hits. Reuses
// `RecordingProcessor.transients()` for onset detection, slices each
// onset up to the next (capped ~140 ms), extracts `OnsetFeatures`,
// classifies, and derives velocity from slice peak RMS relative to
// the loudest hit of the same role (accents = 1, ghosts quieter).

import Foundation

/// One detected, classified percussive event.
public struct DetectedHit: Sendable, Equatable {
    /// Onset time in seconds from the start of the buffer.
    public let timeSec: Double
    /// Assigned drum role.
    public let role: DrumRole
    /// Classifier confidence [0, 1].
    public let confidence: Double
    /// Velocity [0, 1] from relative loudness.
    public let velocity: Float
    /// Features used (retained for correction logging / training).
    public let features: OnsetFeatures

    public init(
        timeSec: Double,
        role: DrumRole,
        confidence: Double,
        velocity: Float,
        features: OnsetFeatures
    ) {
        self.timeSec = timeSec
        self.role = role
        self.confidence = confidence
        self.velocity = velocity
        self.features = features
    }
}

public enum BeatOnsetExtractor {

    /// Max slice length fed to feature extraction (seconds).
    static let maxSliceSec = 0.14
    /// Flux peak-fraction override for the transient detector: a
    /// military-drum ghost note carries only ~1–2% of the accent's
    /// spectral flux (25:1 observed live), so the default 6% gate
    /// swallows soft dynamics. Safe here because the percussive gate
    /// below rejects any non-hit that slips through.
    static let fluxPeakFraction: Float = 0.01
    /// Minimum velocity floor so quiet-but-real hits stay audible.
    static let minVelocity: Float = 0.1
    /// Onsets closer than this collapse into one event (loudest wins).
    /// A single mouth/hand percussion hit fires the transient detector
    /// several times ~90 ms apart (attack click + resonant body); merge
    /// them so one "boom" is one hit, not three.
    static let minOnsetGapSec = 0.11
    /// Drop hits quieter than this fraction of the loudest — kills the
    /// breath / room artifacts that sit between real hits. Kept low so a
    /// soft beatbox kick or ghost note (~1/10 of the accent's level at
    /// the mic) isn't gated out beneath a louder snare/clap; the
    /// percussive gate handles the speech-like junk.
    static let relativeNoiseFloor: Float = 0.05
    /// Percussive gate: reject onsets that take longer than this to reach
    /// 90% of their envelope peak. Drum hits peak near-instantly (<15 ms);
    /// voiced speech ramps up over tens of milliseconds. (`attackSec` has
    /// 5 ms granularity, so 0.03 = six RMS windows.)
    static let maxAttackSec = 0.03
    /// Slow-attack rescue: a beatbox kick ("boom") is a voiced plosive
    /// that ramps over 30–110 ms — slower than `maxAttackSec` — but its
    /// tail still decays hard, unlike speech. Accept moderate attacks up
    /// to this cap when the tail is clearly percussive (see
    /// `rescueMaxSustainRatio`).
    static let rescueMaxAttackSec = 0.12
    /// Tail ceiling for the slow-attack rescue: stricter than
    /// `maxSustainRatio` because a slow attack alone is speech-like —
    /// only a strongly decaying tail proves it was a hit.
    static let rescueMaxSustainRatio: Float = 0.4
    /// Percussive gate: reject onsets whose tail keeps ringing near the
    /// peak — RMS of the last quarter of the slice over the peak RMS.
    /// Percussion decays (kick τ≈50 ms → ratio ≈0.1 at 140 ms); sustained
    /// speech stays near 1. Catches fast-attack plosives followed by
    /// voicing that the attack gate alone would pass.
    static let maxSustainRatio: Float = 0.6
    /// Percussive gate: a real hit rises out of relative quiet — reject
    /// onsets whose 30 ms *pre-onset* RMS is already near the slice peak.
    /// Catches the tail-end of a spoken syllable (preceded by full-level
    /// voicing) that looks percussive in isolation once the voice stops.
    static let preOnsetWindowSec = 0.03
    static let maxPreOnsetRatio: Float = 0.5

    /// Detect and classify every percussive onset in `samples`.
    ///
    /// `detectKick` declares the performer's intent: body-percussion
    /// "kicks" and soft ghost snares are acoustically identical, so the
    /// take alone can't decide. When true (default) the relative
    /// refinement may upgrade a dark brightness cluster to kick; when
    /// false the take is a single-drum performance — refinement is
    /// skipped and any kick verdicts become snares (soft hits stay
    /// ghost notes of the same voice).
    public static func extract(
        _ samples: [Float],
        sampleRate: Double,
        classifier: BeatClassifier,
        detectKick: Bool = true
    ) -> [DetectedHit] {
        guard sampleRate > 0, !samples.isEmpty else { return [] }

        let onsets = RecordingProcessor.transients(
            samples, sampleRate: sampleRate,
            peakFraction: fluxPeakFraction
        )
        guard !onsets.isEmpty else { return [] }

        let maxSliceLen = Int(maxSliceSec * sampleRate)

        // First pass: features + peak per onset.
        struct Raw {
            let time: Double
            let feat: OnsetFeatures
            let sustain: Float
            let preRatio: Float
        }
        let preWindow = max(1, Int(preOnsetWindowSec * sampleRate))
        var raws: [Raw] = []
        raws.reserveCapacity(onsets.count)
        for (idx, start) in onsets.enumerated() {
            let nextOnset = idx + 1 < onsets.count ? onsets[idx + 1] : samples.count
            let end = min(nextOnset, start + maxSliceLen, samples.count)
            guard end > start else { continue }
            let slice = Array(samples[start..<end])
            let feat = OnsetFeatures.extract(slice, sampleRate: sampleRate)
            let preStart = max(0, start - preWindow)
            let pre = rms(samples, from: preStart, to: start)
            raws.append(Raw(
                time: Double(start) / sampleRate,
                feat: feat,
                sustain: tailSustain(slice, peakRMS: feat.peakRMS),
                preRatio: feat.peakRMS > 1e-9 ? pre / feat.peakRMS : 0
            ))
        }
        guard !raws.isEmpty else { return [] }

        // Debounce: collapse onset clusters (one physical hit fires the
        // transient detector several times). Chain on the *previous*
        // onset time so an entire run of closely-spaced onsets collapses
        // to its single loudest slice — not just adjacent pairs.
        var deduped: [Raw] = []
        var prevOnsetTime = -Double.infinity
        for raw in raws {
            if raw.time - prevOnsetTime < minOnsetGapSec,
               let last = deduped.last {
                if raw.feat.peakRMS > last.feat.peakRMS {
                    deduped[deduped.count - 1] = raw
                }
            } else {
                deduped.append(raw)
            }
            prevOnsetTime = raw.time
        }

        // Percussive gate: drop speech-like onsets (slow attack, sustained
        // tail, or no pre-onset quiet) *before* computing the global peak,
        // so loud background speech can't set the noise floor and gate out
        // real quiet hits.
        let percussive = deduped.filter { raw in
            let attackOK = raw.feat.attackSec <= maxAttackSec
                || (raw.feat.attackSec <= rescueMaxAttackSec
                    && raw.sustain <= rescueMaxSustainRatio)
            let pass = attackOK
                && raw.sustain <= maxSustainRatio
                && raw.preRatio <= maxPreOnsetRatio
            if !pass {
                print(String(
                    format: "[BeatOnset] gated t=%.2fs attack=%.3f sustain=%.2f pre=%.2f peak=%.3f",
                    raw.time, raw.feat.attackSec, raw.sustain, raw.preRatio,
                    raw.feat.peakRMS
                ))
            }
            return pass
        }
        guard !percussive.isEmpty else { return [] }

        // Global peak for the noise gate.
        let globalPeak = percussive.map(\.feat.peakRMS).max() ?? 0
        let floor = globalPeak * relativeNoiseFloor
        let kept = percussive.filter { $0.feat.peakRMS >= floor }
        guard !kept.isEmpty else { return [] }

        // Classify first so velocity can normalise *per role*: a chest
        // kick reads far quieter at the mic than a snare clap, so a
        // global normalisation buries every kick at ~0.15 velocity.
        // Per-role, the loudest hit of each role is that role's accent
        // (velocity 1) and its soft embellishments become ghost notes
        // (~0.25–0.4) — accents dominate, dynamics survive.
        let classified = kept.map { raw in
            (raw: raw, verdict: classifier.classify(raw.feat))
        }
        let verdicts: [BeatClassification]
        if detectKick {
            verdicts = refineRelativeRoles(
                feats: classified.map(\.raw.feat),
                verdicts: classified.map(\.verdict)
            )
        } else {
            // Single-drum take: chest/stomach thumps read kick to the
            // per-hit classifier but the performer says otherwise.
            verdicts = classified.map { entry in
                entry.verdict.role == .kick
                    ? BeatClassification(
                        role: .snare, confidence: entry.verdict.confidence
                    )
                    : entry.verdict
            }
        }
        var rolePeak: [DrumRole: Float] = [:]
        for (i, entry) in classified.enumerated() {
            let role = verdicts[i].role
            rolePeak[role] = max(rolePeak[role] ?? 0, entry.raw.feat.peakRMS)
        }

        return classified.enumerated().map { i, entry in
            let (raw, c) = (entry.raw, verdicts[i])
            print(String(
                format: "[BeatOnset] hit t=%.2fs role=%@ conf=%.2f centroid=%.0f low=%.2f pitch=%.2f zcr=%.3f attack=%.3f peak=%.3f",
                raw.time, String(describing: c.role), c.confidence,
                raw.feat.centroidHz, raw.feat.lowBandRatio,
                raw.feat.pitchedness, raw.feat.zcr, raw.feat.attackSec,
                raw.feat.peakRMS
            ))
            let peak = rolePeak[c.role] ?? 0
            let velocity: Float
            if peak > 1e-9 {
                velocity = max(minVelocity, min(1, raw.feat.peakRMS / peak))
            } else {
                velocity = minVelocity
            }
            return DetectedHit(
                timeSec: raw.time,
                role: c.role,
                confidence: c.confidence,
                velocity: velocity,
                features: raw.feat
            )
        }
    }

    // MARK: - Relative role refinement

    /// Verdicts eligible for the relative kick/snare refinement. Hats,
    /// rims and claps are acoustically distinct — leave them alone.
    static let refineRoles: Set<DrumRole> = [.kick, .snare, .perc]
    /// Need enough hits for the two clusters to be meaningful.
    static let refineMinCohort = 6
    /// Each cluster needs at least this many members.
    static let refineMinClusterSize = 2
    /// Bright-cluster mean centroid must exceed the dark-cluster mean by
    /// this ratio before the split is trusted. Chest-beat kicks can
    /// arrive within ~1.37x of the snares (mic brightening), so this
    /// sits just below that; a tight single-timbre take clusters well
    /// under 1.3.
    static let refineMinSeparation: Float = 1.3
    /// Dark-cluster median peak must be at least this fraction of the
    /// bright-cluster median. Soft ghost notes on the *same* instrument
    /// are naturally darker (less HF excitation), so a quiet dark
    /// cluster means ghosts, not kicks — kicks carry the accents and
    /// arrive as loud as (or louder than) the snares.
    static let refineMinLoudnessRatio: Float = 0.5

    /// A chest/mouth "kick" heard alone reads like a snare — in a body
    /// percussion take the kick/snare distinction is *relative*, not
    /// absolute. When the mid-band cohort (kick/snare/perc verdicts)
    /// splits into two well-separated brightness clusters, the dark
    /// cluster is the performer's kick: upgrade those verdicts. Bright-
    /// cluster verdicts stay untouched (a bright hit with real low-band
    /// energy keeps its kick verdict).
    static func refineRelativeRoles(
        feats: [OnsetFeatures],
        verdicts: [BeatClassification]
    ) -> [BeatClassification] {
        let cohort = verdicts.indices.filter {
            refineRoles.contains(verdicts[$0].role)
        }
        guard cohort.count >= refineMinCohort else { return verdicts }
        let cents = cohort.map { feats[$0].centroidHz }
        guard let lo = cents.min(), let hi = cents.max(), hi > lo, lo > 0
        else { return verdicts }

        // 1-D 2-means on centroid (deterministic min/max init).
        var darkMean = lo
        var brightMean = hi
        for _ in 0..<16 {
            let mid = (darkMean + brightMean) / 2
            let dark = cents.filter { $0 <= mid }
            let bright = cents.filter { $0 > mid }
            guard !dark.isEmpty, !bright.isEmpty else { return verdicts }
            let newDark = dark.reduce(0, +) / Float(dark.count)
            let newBright = bright.reduce(0, +) / Float(bright.count)
            if newDark == darkMean && newBright == brightMean { break }
            darkMean = newDark
            brightMean = newBright
        }
        let mid = (darkMean + brightMean) / 2
        let darkIdx = cohort.filter { feats[$0].centroidHz <= mid }
        let brightIdx = cohort.filter { feats[$0].centroidHz > mid }
        guard darkIdx.count >= refineMinClusterSize,
              brightIdx.count >= refineMinClusterSize,
              brightMean >= darkMean * refineMinSeparation
        else { return verdicts }

        // Ghost-note guard: if the dark cluster is much quieter than
        // the bright one it's soft hits on the same instrument, not a
        // second (kick) voice.
        let darkPeak = median(darkIdx.map { feats[$0].peakRMS })
        let brightPeak = median(brightIdx.map { feats[$0].peakRMS })
        guard brightPeak > 0,
              darkPeak >= brightPeak * refineMinLoudnessRatio
        else { return verdicts }

        var out = verdicts
        for i in darkIdx where out[i].role != .kick {
            out[i] = BeatClassification(
                role: .kick,
                confidence: max(out[i].confidence, 0.55)
            )
        }
        return out
    }

    /// Median of a non-empty array (mean of the two middle values for
    /// even counts); 0 when empty.
    private static func median(_ values: [Float]) -> Float {
        guard !values.isEmpty else { return 0 }
        let sorted = values.sorted()
        let mid = sorted.count / 2
        if sorted.count % 2 == 0 {
            return (sorted[mid - 1] + sorted[mid]) / 2
        }
        return sorted[mid]
    }

    /// Plain RMS over `samples[from..<to]`; 0 for an empty range.
    private static func rms(_ samples: [Float], from: Int, to: Int) -> Float {
        guard to > from else { return 0 }
        var sum: Float = 0
        for sample in samples[from..<to] { sum += sample * sample }
        return (sum / Float(to - from)).squareRoot()
    }

    /// RMS of the last quarter of the slice relative to the windowed
    /// peak RMS. ≈0 for decayed percussion, ≈1 for sustained speech.
    private static func tailSustain(_ slice: [Float], peakRMS: Float) -> Float {
        guard peakRMS > 1e-9, slice.count >= 8 else { return 0 }
        let tailStart = slice.count * 3 / 4
        var sum: Float = 0
        for sample in slice[tailStart...] { sum += sample * sample }
        let rms = (sum / Float(slice.count - tailStart)).squareRoot()
        return rms / peakRMS
    }
}
