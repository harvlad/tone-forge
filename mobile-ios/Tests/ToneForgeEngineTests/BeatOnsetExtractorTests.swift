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
}
