// BeatClassifier.swift
//
// Beat Capture (D-024): the classifier seam. Ships with a fast
// heuristic (`HeuristicBeatClassifier`); a CoreML model can drop in
// later behind the same protocol without touching the extraction or
// pattern-building pipeline.

import Foundation

/// One classification verdict for an onset.
public struct BeatClassification: Sendable, Equatable {
    /// Assigned drum role.
    public let role: DrumRole
    /// Confidence in [0, 1]. Low confidence resolves to `.perc`
    /// (prefer a safe percussion hit over a confident wrong one).
    public let confidence: Double

    public init(role: DrumRole, confidence: Double) {
        self.role = role
        self.confidence = confidence
    }
}

/// Maps onset features to a drum role. Implementations must be
/// deterministic and side-effect free (pure), so results are testable
/// and reproducible.
public protocol BeatClassifier: Sendable {
    func classify(_ features: OnsetFeatures) -> BeatClassification
}
