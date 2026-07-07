// SpectralFreezeTests.swift
//
// Pins the spectral freeze's contract: exact output length, seeded
// bit-identical determinism, seed sensitivity, and spectral fidelity —
// freezing a pure 1 kHz sine must keep the output's dominant bin at
// 1 kHz within one bin at the analysis resolution.

import XCTest
import Accelerate
@testable import ToneForgeEngine

final class SpectralFreezeTests: XCTestCase {

    private let fs: Double = 48_000

    private func sine(freq: Double, seconds: Double, amp: Float = 0.8) -> [Float] {
        let n = Int(seconds * fs)
        var x = [Float](repeating: 0, count: n)
        for i in 0..<n {
            x[i] = amp * Float(sin(2.0 * Double.pi * freq * Double(i) / fs))
        }
        return x
    }

    // MARK: - Tests

    func testSeededDeterminismIsBitIdentical() {
        let x = sine(freq: 440, seconds: 0.5)
        let a = SpectralFreeze.freeze(x, atSample: 4_000, durationSec: 0.7, seed: 99, sampleRate: fs)
        let b = SpectralFreeze.freeze(x, atSample: 4_000, durationSec: 0.7, seed: 99, sampleRate: fs)
        XCTAssertEqual(a, b, "same seed must render bit-identically")
    }

    func testDifferentSeedsProduceDifferentOutput() {
        let x = sine(freq: 440, seconds: 0.5)
        let a = SpectralFreeze.freeze(x, atSample: 4_000, durationSec: 0.5, seed: 1, sampleRate: fs)
        let b = SpectralFreeze.freeze(x, atSample: 4_000, durationSec: 0.5, seed: 2, sampleRate: fs)
        XCTAssertEqual(a.count, b.count)
        XCTAssertNotEqual(a, b, "different seeds should draw different phases")
    }

    func testOutputLengthMatchesRequestedDuration() {
        let x = sine(freq: 440, seconds: 0.25)
        for duration in [0.1, 0.5, 1.0, 1.75] {
            let y = SpectralFreeze.freeze(x, atSample: 2_000, durationSec: duration, seed: 0, sampleRate: fs)
            XCTAssertEqual(y.count, Int((duration * fs).rounded()))
        }
    }

    func testFrozenSineKeepsDominantBinAt1kHz() {
        let x = sine(freq: 1_000, seconds: 0.5)
        let y = SpectralFreeze.freeze(x, atSample: 8_192, durationSec: 1.0, seed: 12_345, sampleRate: fs)

        // DFT of the output's central 2048 samples (same resolution as
        // the analysis frame).
        let n = 2_048
        let start = (y.count - n) / 2
        let segment = Array(y[start..<(start + n)])
        let windowed = DSPTestSupport.applyWindow(segment, DSPTestSupport.blackmanHarris(n))
        let mags = DSPTestSupport.magnitudeSpectrum(windowed)

        var dominant = 0
        for k in 1..<mags.count where mags[k] > mags[dominant] { dominant = k }

        let expectedBin = Int((1_000 * Double(n) / fs).rounded())
        XCTAssertLessThanOrEqual(
            abs(dominant - expectedBin), 1,
            "dominant bin \(dominant) should be within ±1 of \(expectedBin)"
        )
        XCTAssertGreaterThan(mags[dominant], 0, "frozen output must be non-silent")
    }
}
