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
        struct Raw { let time: Double; let feat: OnsetFeatures }
        var raws: [Raw] = []
        raws.reserveCapacity(onsets.count)
        for (idx, start) in onsets.enumerated() {
            let nextOnset = idx + 1 < onsets.count ? onsets[idx + 1] : samples.count
            let end = min(nextOnset, start + maxSliceLen, samples.count)
            guard end > start else { continue }
            let slice = Array(samples[start..<end])
            let feat = OnsetFeatures.extract(slice, sampleRate: sampleRate)
            raws.append(Raw(time: Double(start) / sampleRate, feat: feat))
        }
        guard !raws.isEmpty else { return [] }

        // Global peak for velocity normalisation.
        let globalPeak = raws.map(\.feat.peakRMS).max() ?? 0

        return raws.map { raw in
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
}
