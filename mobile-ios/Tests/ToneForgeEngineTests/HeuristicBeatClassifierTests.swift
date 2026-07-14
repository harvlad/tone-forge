// HeuristicBeatClassifierTests.swift
//
// Beat Capture (D-024): gates the drum threshold tree on hand-built
// OnsetFeatures (deterministic, no DSP variance) plus a couple of
// synthetic-audio round trips through OnsetFeatures.extract for the
// unambiguous kick/hat cases.

import XCTest
@testable import ToneForgeEngine

final class HeuristicBeatClassifierTests: XCTestCase {

    private let sr: Double = 48_000
    private let classifier = HeuristicBeatClassifier()

    // MARK: - Direct feature exemplars

    private func feat(
        centroidHz: Float,
        zcr: Float = 0.1,
        attackSec: Double = 0.002,
        durationSec: Double = 0.1,
        pitchedness: Float = 0.2,
        lowBandRatio: Float = 0.05,
        peakRMS: Float = 0.3
    ) -> OnsetFeatures {
        OnsetFeatures(
            centroidHz: centroidHz, zcr: zcr, attackSec: attackSec,
            durationSec: durationSec, pitchedness: pitchedness,
            lowBandRatio: lowBandRatio, peakRMS: peakRMS
        )
    }

    func testKickFromLowBandEnergy() {
        let f = feat(centroidHz: 120, zcr: 0.02, durationSec: 0.14,
                     pitchedness: 0.6, lowBandRatio: 0.8)
        XCTAssertEqual(classifier.classify(f).role, .kick)
    }

    func testDarkBeatboxKickCommitsEvenWithWeakLowBand() {
        // Phone-mic beatbox kick: very dark centroid but sub-bass rolled
        // off (lowBandRatio well under the 808 threshold). Must still
        // read as kick, not collapse to perc.
        let f = feat(centroidHz: 180, zcr: 0.05, durationSec: 0.12,
                     pitchedness: 0.5, lowBandRatio: 0.22)
        XCTAssertEqual(classifier.classify(f).role, .kick)
    }

    func testClosedHatFromBrightShortNoise() {
        let f = feat(centroidHz: 8000, zcr: 0.4, attackSec: 0.001,
                     durationSec: 0.05, pitchedness: 0.05)
        XCTAssertEqual(classifier.classify(f).role, .closedHat)
    }

    func testOpenHatFromBrightLongNoise() {
        let f = feat(centroidHz: 8000, zcr: 0.4, attackSec: 0.001,
                     durationSec: 0.2, pitchedness: 0.05)
        XCTAssertEqual(classifier.classify(f).role, .openHat)
    }

    func testRimFromSharpBrightClick() {
        let f = feat(centroidHz: 3000, zcr: 0.1, attackSec: 0.004,
                     durationSec: 0.04, pitchedness: 0.2)
        XCTAssertEqual(classifier.classify(f).role, .rim)
    }

    func testSnareFromMidNoiseShortDecay() {
        let f = feat(centroidHz: 1500, zcr: 0.3, durationSec: 0.08,
                     pitchedness: 0.1)
        XCTAssertEqual(classifier.classify(f).role, .snare)
    }

    func testClapFromMidNoiseLongDecay() {
        let f = feat(centroidHz: 1500, zcr: 0.3, durationSec: 0.2,
                     pitchedness: 0.1)
        XCTAssertEqual(classifier.classify(f).role, .clap)
    }

    func testAmbiguousLowMidSnapsToNearestDrum() {
        // Pitched, no clean percussive cue: rather than dumping to perc,
        // the tree snaps to the nearest real drum by brightness so the
        // sketch reads as an intentional beat. Low-mid centroid → kick.
        let f = feat(centroidHz: 1000, zcr: 0.05, durationSec: 0.3,
                     pitchedness: 0.8, lowBandRatio: 0.2)
        XCTAssertEqual(classifier.classify(f).role, .kick)
    }

    func testLowConfidenceCollapsesToPerc() {
        // Raw grade below the floor must resolve to perc.
        let strict = HeuristicBeatClassifier(confidenceFloor: 0.95)
        let f = feat(centroidHz: 1500, zcr: 0.3, durationSec: 0.08,
                     pitchedness: 0.1)
        XCTAssertEqual(strict.classify(f).role, .perc)
    }

    func testConfidenceAlwaysInUnitRange() {
        let cases = [
            feat(centroidHz: 120, lowBandRatio: 0.8),
            feat(centroidHz: 8000, zcr: 0.4, durationSec: 0.05),
            feat(centroidHz: 1500, pitchedness: 0.1),
            feat(centroidHz: 1000, pitchedness: 0.9),
        ]
        for f in cases {
            let c = classifier.classify(f)
            XCTAssertGreaterThanOrEqual(c.confidence, 0)
            XCTAssertLessThanOrEqual(c.confidence, 1)
        }
    }

    // MARK: - Synthetic audio round trip

    private func seededNoise(_ n: Int, seed: UInt64 = 11) -> [Float] {
        var rng = SplitMix64(seed: seed)
        return (0..<n).map { _ in Float(rng.nextSymmetricDouble()) }
    }

    /// Low sine burst — should read as kick (low-band dominant).
    private func kickAudio() -> [Float] {
        let n = Int(0.14 * sr)
        return (0..<n).map { i in
            let t = Double(i) / sr
            return 0.9 * Float(sin(2 * .pi * 55 * t) * exp(-t / 0.05))
        }
    }

    /// Very short bright noise burst — should read as a hat.
    private func hatAudio() -> [Float] {
        let n = Int(0.05 * sr)
        let noise = seededNoise(n)
        return (0..<n).map { i in
            noise[i] * 0.5 * Float(exp(-Double(i) / sr / 0.012))
        }
    }

    func testKickAudioExtractsKick() {
        let f = OnsetFeatures.extract(kickAudio(), sampleRate: sr)
        XCTAssertEqual(classifier.classify(f).role, .kick)
    }

    func testHatAudioExtractsHat() {
        let f = OnsetFeatures.extract(hatAudio(), sampleRate: sr)
        let role = classifier.classify(f).role
        XCTAssertTrue(role == .closedHat || role == .openHat,
                      "expected a hat, got \(role)")
    }
}
