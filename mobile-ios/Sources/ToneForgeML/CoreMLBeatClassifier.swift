// CoreMLBeatClassifier.swift
//
// Beat Capture (D-024): the Core ML side of the classifier seam. Loads
// a compiled `.mlmodelc` drum classifier and adapts it to the engine's
// `ModelBackedBeatClassifier` by supplying the `infer` closure over the
// canonical `OnsetFeatures.featureVector`. Isolating Core ML here keeps
// ToneForgeEngine dependency-free and portable.
//
// Model contract (produced by tools/BeatModelTrainer):
//   inputs  — one Double feature per OnsetFeatures.featureNames entry
//   outputs — "role" (String label), "roleProbability" ([String: Double])

import Foundation
import CoreML
import ToneForgeEngine

public enum CoreMLBeatClassifier {

    /// Output feature names from the trained model.
    private static let labelKey = "role"
    private static let probabilityKey = "roleProbability"

    /// Build a `ModelBackedBeatClassifier` that runs `modelURL`, falling
    /// back to the heuristic when the model can't load, inference fails,
    /// or confidence is below `confidenceFloor` (verdict collapses to
    /// `.perc` for safety, matching the heuristic floor).
    ///
    /// - Returns: a model-backed classifier, or a pure heuristic one if
    ///   the model at `modelURL` fails to load.
    public static func make(
        modelURL: URL,
        confidenceFloor: Double = 0.3
    ) -> BeatClassifier {
        guard let model = try? MLModel(contentsOf: modelURL) else {
            return HeuristicBeatClassifier()
        }
        let names = OnsetFeatures.featureNames

        let infer: ModelBackedBeatClassifier.Inference = { vector in
            guard vector.count == names.count else { return nil }
            var dict: [String: Double] = [:]
            for (i, name) in names.enumerated() { dict[name] = vector[i] }
            guard let provider = try? MLDictionaryFeatureProvider(dictionary: dict),
                  let out = try? model.prediction(from: provider),
                  let label = out.featureValue(for: labelKey)?.stringValue,
                  let role = DrumRole(rawValue: label) else {
                return nil
            }
            // Probability of the predicted label, when the model exposes
            // it; default to 1 so a hard label still classifies.
            var confidence = 1.0
            if let probs = out.featureValue(for: probabilityKey)?.dictionaryValue as? [String: Double],
               let p = probs[label] {
                confidence = p
            }
            if confidence < confidenceFloor {
                return BeatClassification(role: .perc, confidence: confidence)
            }
            return BeatClassification(role: role, confidence: confidence)
        }

        return ModelBackedBeatClassifier(infer: infer)
    }
}
