// BeatOnsetExtractor.swift
//
// Beat Capture (D-024): turns a raw performance buffer into a list of
// classified, velocity-scaled hits. Reuses
// `RecordingProcessor.transients()` for onset detection, slices each
// onset up to the next (capped ~140 ms), extracts `OnsetFeatures`,
// classifies, and derives velocity from slice peak RMS relative to
// the loudest hit.

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
    /// Minimum velocity floor so quiet-but-real hits stay audible.
    static let minVelocity: Float = 0.1
    /// Onsets closer than this collapse into one event (loudest wins).
    /// A single mouth/hand percussion hit fires the transient detector
    /// several times ~90 ms apart (attack click + resonant body); merge
    /// them so one "boom" is one hit, not three.
    static let minOnsetGapSec = 0.11
    /// Drop hits quieter than this fraction of the loudest — kills the
    /// breath / room artifacts that sit between real hits. Kept low so a
    /// soft beatbox kick isn't gated out beneath a louder snare/clap.
    static let relativeNoiseFloor: Float = 0.10
    /// Percussive gate: reject onsets that take longer than this to reach
    /// 90% of their envelope peak. Drum hits peak near-instantly (<15 ms);
    /// voiced speech ramps up over tens of milliseconds. (`attackSec` has
    /// 5 ms granularity, so 0.03 = six RMS windows.)
    static let maxAttackSec = 0.03
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
    public static func extract(
        _ samples: [Float],
        sampleRate: Double,
        classifier: BeatClassifier
    ) -> [DetectedHit] {
        guard sampleRate > 0, !samples.isEmpty else { return [] }

        let onsets = RecordingProcessor.transients(samples, sampleRate: sampleRate)
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
        let percussive = deduped.filter {
            $0.feat.attackSec <= maxAttackSec
                && $0.sustain <= maxSustainRatio
                && $0.preRatio <= maxPreOnsetRatio
        }
        guard !percussive.isEmpty else { return [] }

        // Global peak for velocity normalisation + noise gate.
        let globalPeak = percussive.map(\.feat.peakRMS).max() ?? 0
        let floor = globalPeak * relativeNoiseFloor
        let kept = percussive.filter { $0.feat.peakRMS >= floor }
        guard !kept.isEmpty else { return [] }

        return kept.map { raw in
            let c = classifier.classify(raw.feat)
            let velocity: Float
            if globalPeak > 1e-9 {
                velocity = max(minVelocity, min(1, raw.feat.peakRMS / globalPeak))
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
