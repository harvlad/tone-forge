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

    /// Snare-like onset: no low body, mid-band noise burst. The
    /// heuristic never calls this a kick, so no rescue fires.
    private func snareLike() -> OnsetFeatures {
        OnsetFeatures(
            centroidHz: 3200, zcr: 0.09, attackSec: 0.010,
            durationSec: 0.1, pitchedness: 0.4, lowBandRatio: 0.0,
            peakRMS: 0.4
        )
    }

    func testModelVerdictWins() {
        let clf = ModelBackedBeatClassifier { _ in
            BeatClassification(role: .clap, confidence: 0.99)
        }
        let out = clf.classify(snareLike())
        XCTAssertEqual(out.role, .clap)
        XCTAssertEqual(out.confidence, 0.99, accuracy: 1e-9)
    }

    /// Beatbox kick through a laptop mic: brightened centroid but real
    /// low-band body (lowBandRatio ≥ 0.2). The E-GMD-trained model calls
    /// it snare — the heuristic kick rescue must override.
    func testKickRescueOverridesModelSnare() {
        let f = OnsetFeatures(
            centroidHz: 2040, zcr: 0.033, attackSec: 0.015,
            durationSec: 0.11, pitchedness: 0.51, lowBandRatio: 0.20,
            peakRMS: 0.032
        )
        let clf = ModelBackedBeatClassifier { _ in
            BeatClassification(role: .snare, confidence: 0.63)
        }
        XCTAssertEqual(clf.classify(f).role, .kick)
    }

    /// Tonal mouth "boom" with no measured low band (mic roll-off):
    /// pitched, dark-ish centroid. Heuristic tonal-boom rule says kick
    /// at exactly the rescue confidence floor — must still override.
    func testTonalBoomRescuedFromModelSnare() {
        let f = OnsetFeatures(
            centroidHz: 1964, zcr: 0.027, attackSec: 0.015,
            durationSec: 0.12, pitchedness: 0.55, lowBandRatio: 0.0,
            peakRMS: 0.133
        )
        let clf = ModelBackedBeatClassifier { _ in
            BeatClassification(role: .snare, confidence: 0.74)
        }
        XCTAssertEqual(clf.classify(f).role, .kick)
    }

    /// A genuine snare (bright, noisy, no low body) keeps the model
    /// verdict — rescue must not fire.
    func testRealSnareNotRescued() {
        let clf = ModelBackedBeatClassifier { _ in
            BeatClassification(role: .snare, confidence: 0.66)
        }
        let out = clf.classify(snareLike())
        XCTAssertEqual(out.role, .snare)
        XCTAssertEqual(out.confidence, 0.66, accuracy: 1e-9)
    }

    /// Model already says kick: verdict passes through untouched.
    func testModelKickVerdictUntouched() {
        let clf = ModelBackedBeatClassifier { _ in
            BeatClassification(role: .kick, confidence: 0.9)
        }
        let out = clf.classify(features(centroid: 120))
        XCTAssertEqual(out.role, .kick)
        XCTAssertEqual(out.confidence, 0.9, accuracy: 1e-9)
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
