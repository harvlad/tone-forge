// ModelBackedBeatClassifierTests.swift
//
// Core ML seam: the injected inference closure wins when it returns a
// verdict; the heuristic fallback covers nil (no model / low conf).

import XCTest
@testable import ToneForgeEngine

final class ModelBackedBeatClassifierTests: XCTestCase {

    private func features(centroid: Float) -> OnsetFeatures {
        OnsetFeatures(
            centroidHz: centroid, zcr: 0.05, attackSec: 0.002,
            durationSec: 0.08, pitchedness: 0.6, lowBandRatio: 0.5,
            peakRMS: 0.4
        )
    }

    func testFeatureVectorMatchesNameOrder() {
        let f = features(centroid: 120)
        XCTAssertEqual(f.featureVector.count, OnsetFeatures.featureNames.count)
        XCTAssertEqual(f.featureVector[0], 120)      // centroidHz
        XCTAssertEqual(f.featureVector[5], 0.5, accuracy: 1e-6)  // lowBandRatio
    }

    func testModelVerdictWins() {
        let clf = ModelBackedBeatClassifier { _ in
            BeatClassification(role: .clap, confidence: 0.99)
        }
        let out = clf.classify(features(centroid: 120))
        XCTAssertEqual(out.role, .clap)
        XCTAssertEqual(out.confidence, 0.99, accuracy: 1e-9)
    }

    func testNilInferenceFallsBackToHeuristic() {
        let heuristic = HeuristicBeatClassifier()
        let f = features(centroid: 120)  // strong low band -> kick
        let clf = ModelBackedBeatClassifier(fallback: heuristic) { _ in nil }
        XCTAssertEqual(clf.classify(f).role, heuristic.classify(f).role)
    }

    func testInferenceReceivesCanonicalVector() {
        var seen: [Double] = []
        let clf = ModelBackedBeatClassifier { vec in
            seen = vec
            return nil
        }
        let f = features(centroid: 3200)
        _ = clf.classify(f)
        XCTAssertEqual(seen, f.featureVector)
    }
}
