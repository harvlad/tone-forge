// CoreMLBeatClassifierTests.swift
//
// Verifies the bundled Core ML drum classifier loads and classifies
// synthetic onsets, and that the loader degrades to the heuristic on a
// bad model URL.

import XCTest
import ToneForgeEngine
@testable import ToneForgeML

final class CoreMLBeatClassifierTests: XCTestCase {

    private let sr = 48_000.0

    func testBundledModelLoads() throws {
        let url = try XCTUnwrap(BeatModel.bundledModelURL(),
                               "bundled BeatClassifier.mlmodelc missing")
        let clf = CoreMLBeatClassifier.make(modelURL: url)
        // Must be model-backed (not a bare heuristic).
        XCTAssertTrue(clf is ModelBackedBeatClassifier)
    }

    func testClassifiesLowSineAsKick() throws {
        let url = try XCTUnwrap(BeatModel.bundledModelURL())
        let clf = CoreMLBeatClassifier.make(modelURL: url)
        // 70 Hz decaying sine ≈ a kick body.
        let slice = decayingSine(freq: 70, decay: 24, dur: 0.13)
        let feat = OnsetFeatures.extract(slice, sampleRate: sr)
        let v = clf.classify(feat)
        XCTAssertEqual(v.role, .kick, "70 Hz body should classify as kick, got \(v.role)")
    }

    func testClassifiesBrightNoiseAsHatOrSnare() throws {
        let url = try XCTUnwrap(BeatModel.bundledModelURL())
        let clf = CoreMLBeatClassifier.make(modelURL: url)
        // Very short high-passed noise ≈ closed hat.
        let slice = highNoise(dur: 0.04)
        let feat = OnsetFeatures.extract(slice, sampleRate: sr)
        let v = clf.classify(feat)
        XCTAssertTrue([.closedHat, .openHat, .snare, .rim, .perc].contains(v.role),
                      "bright short noise should be a bright role, got \(v.role)")
    }

    func testBadURLFallsBackToHeuristic() {
        let bad = URL(fileURLWithPath: "/nonexistent/Model.mlmodelc")
        let clf = CoreMLBeatClassifier.make(modelURL: bad)
        XCTAssertTrue(clf is HeuristicBeatClassifier)
    }

    // MARK: - Synthetic slices

    private func decayingSine(freq: Double, decay: Double, dur: Double) -> [Float] {
        let n = Int(dur * sr)
        var x = [Float](repeating: 0, count: n)
        for i in 0..<n {
            let t = Double(i) / sr
            x[i] = Float(sin(2 * .pi * freq * t) * exp(-decay * t))
        }
        return x
    }

    private func highNoise(dur: Double) -> [Float] {
        let n = Int(dur * sr)
        var x = [Float](repeating: 0, count: n)
        var g = SystemRandomNumberGenerator()
        var prev: Float = 0
        for i in 0..<n {
            let t = Double(i) / sr
            let w = Float.random(in: -1...1, using: &g)
            let hp = w - prev
            prev = w
            x[i] = hp * Float(exp(-80 * t))
        }
        return x
    }
}
