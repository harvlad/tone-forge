// TempoEstimatorTests.swift
//
// Beat Capture (D-024): tempo derivation from onset times.

import XCTest
@testable import ToneForgeEngine

final class TempoEstimatorTests: XCTestCase {

    func testSteady120BPM() {
        // Quarter notes at 120 BPM = 0.5 s apart.
        let onsets = (0..<9).map { Double($0) * 0.5 }
        let (bpm, conf) = TempoEstimator.estimate(onsetTimesSec: onsets)
        XCTAssertEqual(bpm, 120, accuracy: 3)
        XCTAssertGreaterThan(conf, 0.8)
    }

    func testSteady90BPM() {
        // 0.6667 s apart = 90 BPM.
        let step = 60.0 / 90.0
        let onsets = (0..<9).map { Double($0) * step }
        let (bpm, conf) = TempoEstimator.estimate(onsetTimesSec: onsets)
        XCTAssertEqual(bpm, 90, accuracy: 3)
        XCTAssertGreaterThan(conf, 0.8)
    }

    func testFoldsFastTempoIntoRange() {
        // 0.15 s apart = 400 BPM raw → folds to 200→100 within 60–180.
        let onsets = (0..<9).map { Double($0) * 0.15 }
        let (bpm, _) = TempoEstimator.estimate(onsetTimesSec: onsets)
        XCTAssertGreaterThanOrEqual(bpm, 60)
        XCTAssertLessThanOrEqual(bpm, 180)
    }

    func testTooFewOnsetsZeroConfidence() {
        let (_, conf) = TempoEstimator.estimate(onsetTimesSec: [0.0])
        XCTAssertEqual(conf, 0)
    }

    func testIrregularOnsetsLowerConfidence() {
        let steady = (0..<9).map { Double($0) * 0.5 }
        let (_, steadyConf) = TempoEstimator.estimate(onsetTimesSec: steady)
        let jittered = [0.0, 0.31, 0.72, 0.95, 1.6, 1.71, 2.4, 2.55]
        let (_, jitterConf) = TempoEstimator.estimate(onsetTimesSec: jittered)
        XCTAssertLessThan(jitterConf, steadyConf)
    }
}
