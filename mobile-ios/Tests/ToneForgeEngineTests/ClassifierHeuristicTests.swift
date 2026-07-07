// ClassifierHeuristicTests.swift
//
// Sanity-gates the heuristic decision tree on synthetic material with
// unambiguous ground truth. These are canonical exemplars, not edge
// cases — the tree only has to get the obvious ones right (the pad
// sheet's user override covers the rest, and D-018 reserves real
// accuracy work for the Core ML seam).

import XCTest
@testable import ToneForgeEngine

final class ClassifierHeuristicTests: XCTestCase {

    private let sr: Double = 48_000
    private let classifier = HeuristicClassifier()

    // MARK: - Synthesis

    private func seededNoise(_ n: Int, seed: UInt64 = 7) -> [Float] {
        var rng = SplitMix64(seed: seed)
        return (0..<n).map { _ in Float(rng.nextSymmetricDouble()) }
    }

    /// Snare-ish: white noise, instant attack, 40 ms decay.
    private func drumHit(_ seconds: Double = 0.4) -> [Float] {
        let n = Int(seconds * sr)
        let noise = seededNoise(n)
        return (0..<n).map { i in
            noise[i] * 0.8 * Float(exp(-Double(i) / sr / 0.04))
        }
    }

    /// Held sawtooth-ish tone (fundamental + 4 harmonics), flat level.
    private func heldTone(
        _ seconds: Double, hz: Double, amplitude: Float = 0.5
    ) -> [Float] {
        let n = Int(seconds * sr)
        return (0..<n).map { i in
            let t = Double(i) / sr
            var s = 0.0
            for h in 1...5 { s += sin(2 * .pi * hz * Double(h) * t) / Double(h) }
            return amplitude * Float(s / 2)
        }
    }

    private func silence(_ seconds: Double) -> [Float] {
        [Float](repeating: 0, count: Int(seconds * sr))
    }

    // MARK: - Per-class exemplars

    func testDrumHitClassifiesAsPercussion() {
        let (cls, conf) = classifier.classify(samples: drumHit(), sampleRate: sr)
        XCTAssertEqual(cls, .percussion)
        XCTAssertGreaterThanOrEqual(conf, 0.5)
    }

    func testHeldToneClassifiesAsSustainedNote() {
        let (cls, conf) = classifier.classify(
            samples: heldTone(2.5, hz: 220), sampleRate: sr
        )
        XCTAssertEqual(cls, .sustainedNote)
        XCTAssertGreaterThanOrEqual(conf, 0.6)
    }

    func testLongSteadyNoiseClassifiesAsTexture() {
        let pad = seededNoise(Int(3.0 * sr)).map { $0 * 0.4 }
        let (cls, conf) = classifier.classify(samples: pad, sampleRate: sr)
        XCTAssertEqual(cls, .texture)
        XCTAssertGreaterThanOrEqual(conf, 0.5)
    }

    func testShortPitchedEventClassifiesAsVocalChop() {
        let (cls, conf) = classifier.classify(
            samples: heldTone(0.6, hz: 330), sampleRate: sr
        )
        XCTAssertEqual(cls, .vocalChop)
        XCTAssertGreaterThanOrEqual(conf, 0.5)
    }

    func testNoteSequenceClassifiesAsPhrase() {
        var samples: [Float] = []
        for hz in [220.0, 294.0, 330.0, 392.0] {
            samples += heldTone(0.25, hz: hz) + silence(0.12)
        }
        let (cls, conf) = classifier.classify(samples: samples, sampleRate: sr)
        XCTAssertEqual(cls, .phrase)
        XCTAssertGreaterThanOrEqual(conf, 0.4)
    }

    func testSpectrallyMovingShortEventClassifiesAsSpeechWord() {
        // A pitched event whose spectrum jumps every 80 ms — a crude
        // formant-transition stand-in.
        var samples: [Float] = []
        for (i, hz) in [180.0, 900.0, 250.0, 1400.0, 300.0].enumerated() {
            samples += heldTone(0.08, hz: hz, amplitude: i % 2 == 0 ? 0.5 : 0.25)
        }
        let (cls, _) = classifier.classify(samples: samples, sampleRate: sr)
        XCTAssertEqual(cls, .speechWord)
    }

    // MARK: - Degenerate / contract

    func testTooShortInputIsUnknownZeroConfidence() {
        let (cls, conf) = classifier.classify(
            samples: [Float](repeating: 0.1, count: 512), sampleRate: sr
        )
        XCTAssertEqual(cls, .unknown)
        XCTAssertEqual(conf, 0)
    }

    func testConfidenceAlwaysInUnitRange() {
        let inputs: [[Float]] = [
            drumHit(),
            heldTone(2.5, hz: 220),
            seededNoise(Int(3.0 * sr)),
            heldTone(0.6, hz: 330),
            seededNoise(2048),
        ]
        for input in inputs {
            let (_, conf) = classifier.classify(samples: input, sampleRate: sr)
            XCTAssertGreaterThanOrEqual(conf, 0)
            XCTAssertLessThanOrEqual(conf, 1)
        }
    }

    func testDeterministic() {
        let input = drumHit() + heldTone(0.5, hz: 440)
        let a = classifier.classify(samples: input, sampleRate: sr)
        let b = classifier.classify(samples: input, sampleRate: sr)
        XCTAssertEqual(a.0, b.0)
        XCTAssertEqual(a.1, b.1)
    }
}
