// BeatOnsetExtractorTests.swift
//
// Beat Capture (D-024): onset detection + classification + velocity
// over a synthetic performance buffer with known hit positions.

import XCTest
@testable import ToneForgeEngine

final class BeatOnsetExtractorTests: XCTestCase {

    private let sr: Double = 48_000
    private let classifier = HeuristicBeatClassifier()

    private func seededNoise(_ n: Int, seed: UInt64 = 5) -> [Float] {
        var rng = SplitMix64(seed: seed)
        return (0..<n).map { _ in Float(rng.nextSymmetricDouble()) }
    }

    /// Low sine burst (kick-like).
    private func kick(_ amp: Float) -> [Float] {
        let n = Int(0.14 * sr)
        return (0..<n).map { i in
            let t = Double(i) / sr
            return amp * Float(sin(2 * .pi * 55 * t) * exp(-t / 0.05))
        }
    }

    /// Short bright noise burst (hat-like).
    private func hat(_ amp: Float, seed: UInt64) -> [Float] {
        let n = Int(0.05 * sr)
        let noise = seededNoise(n, seed: seed)
        return (0..<n).map { i in
            noise[i] * amp * Float(exp(-Double(i) / sr / 0.012))
        }
    }

    private func silence(_ seconds: Double) -> [Float] {
        [Float](repeating: 0, count: Int(seconds * sr))
    }

    /// Voiced speech-like segment: slow attack (~60 ms ramp) into a
    /// sustained harmonic tone with a little noise — a vowel, roughly.
    private func vowel(_ amp: Float, seconds: Double = 0.3, seed: UInt64 = 9) -> [Float] {
        let n = Int(seconds * sr)
        let noise = seededNoise(n, seed: seed)
        return (0..<n).map { i in
            let t = Double(i) / sr
            let ramp = Float(min(1.0, t / 0.06))
            let tone = Float(
                sin(2 * .pi * 180 * t) + 0.5 * sin(2 * .pi * 360 * t))
            return amp * ramp * (0.8 * tone + 0.2 * noise[i])
        }
    }

    func testDetectsAllOnsets() {
        // 4 hits with wide gaps so onset detection is unambiguous.
        var buf: [Float] = []
        buf += silence(0.05)
        buf += kick(0.9); buf += silence(0.4)
        buf += hat(0.5, seed: 1); buf += silence(0.4)
        buf += kick(0.9); buf += silence(0.4)
        buf += hat(0.5, seed: 2); buf += silence(0.3)

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier
        )
        XCTAssertEqual(hits.count, 4)
    }

    func testOnsetTimesApproximate() {
        var buf: [Float] = []
        buf += silence(0.1)
        buf += kick(0.9); buf += silence(0.4)
        buf += kick(0.9); buf += silence(0.3)

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier
        )
        XCTAssertEqual(hits.count, 2)
        // First hit ≈ 0.1 s, second ≈ 0.1 + 0.14 + 0.4 = 0.64 s.
        XCTAssertEqual(hits[0].timeSec, 0.10, accuracy: 0.03)
        XCTAssertEqual(hits[1].timeSec, 0.64, accuracy: 0.03)
    }

    func testVelocityReflectsLoudness() {
        var buf: [Float] = []
        buf += silence(0.05)
        buf += kick(0.9); buf += silence(0.4)   // loud
        buf += kick(0.25); buf += silence(0.3)   // quiet

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier
        )
        XCTAssertEqual(hits.count, 2)
        XCTAssertGreaterThan(hits[0].velocity, hits[1].velocity)
        // Loudest hit normalises to 1.
        XCTAssertEqual(hits[0].velocity, 1.0, accuracy: 0.001)
    }

    func testEmptyBufferNoHits() {
        let hits = BeatOnsetExtractor.extract(
            silence(0.5), sampleRate: sr, classifier: classifier
        )
        XCTAssertTrue(hits.isEmpty)
    }

    func testKickClassifiedInContext() {
        var buf: [Float] = []
        buf += silence(0.05)
        buf += kick(0.9); buf += silence(0.4)

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier
        )
        XCTAssertEqual(hits.first?.role, .kick)
    }

    /// Background speech alone (slow-attack sustained vowels) must not
    /// register any hits — the percussive gate rejects it.
    func testSpeechOnlyProducesNoHits() {
        var buf: [Float] = []
        buf += silence(0.1)
        buf += vowel(0.7, seed: 3); buf += silence(0.15)
        buf += vowel(0.6, seconds: 0.4, seed: 4); buf += silence(0.15)
        buf += vowel(0.8, seed: 5); buf += silence(0.2)

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier
        )
        XCTAssertTrue(
            hits.isEmpty,
            "speech-only buffer produced \(hits.count) hits"
        )
    }

    /// Real hits survive the percussive gate even with louder speech
    /// in between — and speech must not set the noise floor that would
    /// otherwise gate out the quiet hit.
    func testHitsSurviveInterleavedSpeech() {
        var buf: [Float] = []
        buf += silence(0.1)
        buf += kick(0.9); buf += silence(0.3)
        buf += vowel(0.95, seconds: 0.4, seed: 6); buf += silence(0.2)
        buf += hat(0.4, seed: 7); buf += silence(0.3)

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier
        )
        XCTAssertEqual(hits.count, 2, "expected kick+hat only, got \(hits.count)")
        XCTAssertEqual(hits.first?.timeSec ?? -1, 0.10, accuracy: 0.03)
    }
}
