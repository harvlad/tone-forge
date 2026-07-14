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

    public func classify(_ features: OnsetFeatures) -> BeatClassification {
        if let verdict = infer(features.featureVector) {
            return verdict
        }
        return fallback.classify(features)
    }
}
