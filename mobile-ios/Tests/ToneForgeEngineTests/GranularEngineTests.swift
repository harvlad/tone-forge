// GranularEngineTests.swift
//
// Pins the granular renderer's contract: exact output length, seeded
// bit-identical determinism, seed sensitivity, non-silent output, and
// the peak-normalisation guard against grain pileup.

import XCTest
@testable import ToneForgeEngine

final class GranularEngineTests: XCTestCase {

    private let fs: Double = 48_000

    private func source(seconds: Double = 0.5) -> [Float] {
        let n = Int(seconds * fs)
        var x = [Float](repeating: 0, count: n)
        for i in 0..<n {
            let t = Double(i) / fs
            x[i] = Float(0.6 * sin(2.0 * Double.pi * 330 * t)
                + 0.25 * sin(2.0 * Double.pi * 991 * t))
        }
        return x
    }

    private func params(seed: UInt64) -> GranularParams {
        GranularParams(
            grainMs: 60,
            densityHz: 30,
            positionJitter: 0.2,
            pitchSpreadSemis: 4,
            seed: seed
        )
    }

    // MARK: - Tests

    func testSeededDeterminismIsBitIdentical() {
        let x = source()
        let a = GranularEngine.render(x, params: params(seed: 42), durationSec: 1.0, sampleRate: fs)
        let b = GranularEngine.render(x, params: params(seed: 42), durationSec: 1.0, sampleRate: fs)
        XCTAssertEqual(a, b, "same seed must render bit-identically")
    }

    func testDifferentSeedsProduceDifferentOutput() {
        let x = source()
        let a = GranularEngine.render(x, params: params(seed: 1), durationSec: 1.0, sampleRate: fs)
        let b = GranularEngine.render(x, params: params(seed: 2), durationSec: 1.0, sampleRate: fs)
        XCTAssertEqual(a.count, b.count)
        XCTAssertNotEqual(a, b, "different seeds should scatter grains differently")
    }

    func testNonSilentOutputForNonSilentInput() {
        let x = source()
        let y = GranularEngine.render(x, params: params(seed: 7), durationSec: 1.0, sampleRate: fs)
        let peak = y.map(abs).max() ?? 0
        XCTAssertGreaterThan(peak, 0.01, "grains from a non-silent source must be audible")
    }

    func testOutputLengthMatchesRequestedDuration() {
        let x = source()
        for duration in [0.25, 0.5, 1.0, 2.333] {
            let y = GranularEngine.render(x, params: params(seed: 3), durationSec: duration, sampleRate: fs)
            XCTAssertEqual(y.count, Int((duration * fs).rounded()))
        }
    }

    func testPeakNeverExceedsUnityUnderPileup() {
        let x = source()
        var dense = params(seed: 5)
        dense.densityHz = 400
        dense.grainMs = 120
        let y = GranularEngine.render(x, params: dense, durationSec: 1.0, sampleRate: fs)
        let peak = y.map(abs).max() ?? 0
        XCTAssertLessThanOrEqual(peak, 1.0, "pileup guard must peak-normalise")
        XCTAssertGreaterThan(peak, 0.01)
    }
}
