// ModelBackedBeatClassifier.swift
//
// Beat Capture (D-024): the Core ML drop-in seam. Keeps the engine
// target pure (no Core ML dependency) by taking the model inference as
// an injected closure over the canonical `OnsetFeatures.featureVector`.
// The mobile layer supplies a closure that runs an `MLModel` and maps
// its output to a `BeatClassification`; when that closure returns nil
// (no model bundled, inference error, or below-threshold confidence)
// the verdict falls back to the heuristic classifier. This lets a
// trained model land later without touching extraction, pattern
// building, or the UI.

import Foundation

/// A `BeatClassifier` that consults an injected model first and falls
/// back to a heuristic. The `infer` closure receives the ordered
/// feature vector (see `OnsetFeatures.featureNames`) and returns a
/// verdict, or nil to defer to `fallback`.
public struct ModelBackedBeatClassifier: BeatClassifier {

    /// Model inference over the canonical feature vector. Return nil to
    /// fall back to the heuristic (no model, error, or low confidence).
    public typealias Inference = @Sendable (_ vector: [Double]) -> BeatClassification?

    private let infer: Inference
    private let fallback: BeatClassifier

    public init(
        fallback: BeatClassifier = HeuristicBeatClassifier(),
        infer: @escaping Inference
    ) {
        self.fallback = fallback
        self.infer = infer
    }

    /// Minimum heuristic kick confidence needed to override a non-kick
    /// model verdict (see `classify`).
    static let kickRescueConfidence = 0.5

    public func classify(_ features: OnsetFeatures) -> BeatClassification {
        if let verdict = infer(features.featureVector) {
            // Kick rescue: the trained model has only seen real-drum
            // kicks (heavy sub-150 Hz body). A mouth/beatbox kick through
            // a laptop mic arrives brightened — centroid ~2 kHz,
            // lowBandRatio ~0.2 — and the model calls it snare. The
            // heuristic is tuned for exactly these mic-coloured booms
            // (low-band, dark-centroid, and tonal-boom rules), so when it
            // is confident the onset is a kick, trust it over a non-kick
            // model verdict. Showmanship over 1:1 transcription.
            if verdict.role != .kick {
                let h = HeuristicBeatClassifier().grade(features)
                if h.role == .kick && h.confidence >= Self.kickRescueConfidence {
                    return h
                }
            }
            return verdict
        }
        return fallback.classify(features)
    }
}
