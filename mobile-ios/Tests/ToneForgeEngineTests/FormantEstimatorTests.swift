// FormantEstimatorTests.swift
//
// Gates the LPC envelope estimator: on a synthetic vowel (impulse
// train through two resonators) the two dominant envelope peaks must
// land on the formants within ±5%, and degenerate input (silence)
// must stay finite and flat.

import XCTest
@testable import ToneForgeEngine

final class FormantEstimatorTests: XCTestCase {

    /// Analysis rate for the synthetic vowel: 16 kHz, so order-20 LPC
    /// has ~10 pole pairs across 0–8 kHz — plenty for two formants.
    private let fs: Double = 16_000

    // MARK: - Synthetic vowel

    /// Two-pole resonator y[n] = x[n] + 2r·cos(θ)y[n−1] − r²y[n−2].
    private func resonate(_ x: [Float], centerHz: Double, bandwidthHz: Double) -> [Float] {
        let r = exp(-Double.pi * bandwidthHz / fs)
        let c = Float(2 * r * cos(2 * Double.pi * centerHz / fs))
        let r2 = Float(r * r)
        var y = [Float](repeating: 0, count: x.count)
        var y1: Float = 0
        var y2: Float = 0
        for n in x.indices {
            let v = x[n] + c * y1 - r2 * y2
            y[n] = v
            y2 = y1
            y1 = v
        }
        return y
    }

    /// 100 Hz impulse train through resonators at F1/F2 — the formants
    /// sit exactly on harmonics 7 and 12, a friendly but realistic
    /// vowel-like source.
    private func vowel(f1: Double, f2: Double, samples: Int) -> [Float] {
        var train = [Float](repeating: 0, count: samples)
        let period = Int(fs / 100)
        var i = 0
        while i < samples {
            train[i] = 1
            i += period
        }
        return resonate(resonate(train, centerHz: f1, bandwidthHz: 90),
                        centerHz: f2, bandwidthHz: 90)
    }

    /// Interior local maxima of `env`, sorted by height descending.
    private func peaks(_ env: [Float]) -> [(bin: Int, value: Float)] {
        var found: [(Int, Float)] = []
        for i in 1..<(env.count - 1) where env[i] > env[i - 1] && env[i] > env[i + 1] {
            found.append((i, env[i]))
        }
        return found.sorted { $0.1 > $1.1 }
    }

    // MARK: - Formant recovery

    func testEnvelopePeaksMatchFormants() {
        let f1 = 700.0
        let f2 = 1_200.0
        let signal = vowel(f1: f1, f2: f2, samples: 8_000)
        let frame = Array(signal[2_048..<6_144])

        let binCount = 4_096
        let env = FormantEstimator.lpcEnvelope(
            frame, order: 20, binCount: binCount, sampleRate: fs
        )
        XCTAssertEqual(env.count, binCount)
        XCTAssertTrue(env.allSatisfy(\.isFinite))

        let top = peaks(env).prefix(2)
        XCTAssertEqual(top.count, 2, "expected two formant peaks")
        let hzPerBin = (fs / 2) / Double(binCount - 1)
        let freqs = top.map { Double($0.bin) * hzPerBin }.sorted()
        XCTAssertEqual(freqs[0], f1, accuracy: f1 * 0.05, "F1 off")
        XCTAssertEqual(freqs[1], f2, accuracy: f2 * 0.05, "F2 off")
    }

    func testEnvelopeIsPeakNormalized() {
        let signal = vowel(f1: 700, f2: 1_200, samples: 8_000)
        let frame = Array(signal[2_048..<6_144])
        let env = FormantEstimator.lpcEnvelope(
            frame, order: 20, binCount: 1_024, sampleRate: fs
        )
        XCTAssertEqual(env.max()!, 1.0, accuracy: 1e-4)
        XCTAssertGreaterThan(env.min()!, 0)
    }

    // MARK: - Degenerate input

    func testSilenceYieldsFlatFiniteEnvelope() {
        let silence = [Float](repeating: 0, count: 1_024)
        let env = FormantEstimator.lpcEnvelope(
            silence, order: 20, binCount: 512, sampleRate: fs
        )
        XCTAssertEqual(env.count, 512)
        XCTAssertTrue(env.allSatisfy(\.isFinite))
        XCTAssertEqual(env.min()!, env.max()!, "silence must be flat")
        XCTAssertEqual(env.first!, 1.0, accuracy: 1e-6)
    }

    func testSilenceYieldsZeroCoefficients() {
        let silence = [Float](repeating: 0, count: 1_024)
        let a = FormantEstimator.lpcCoefficients(silence, order: 20)
        XCTAssertEqual(a.count, 20)
        XCTAssertTrue(a.allSatisfy { $0 == 0 })
    }

    func testCoefficientCountAndFiniteness() {
        let signal = vowel(f1: 700, f2: 1_200, samples: 8_000)
        let frame = Array(signal[2_048..<4_096])
        let a = FormantEstimator.lpcCoefficients(frame, order: 20)
        XCTAssertEqual(a.count, 20)
        XCTAssertTrue(a.allSatisfy(\.isFinite))
    }

    func testFrameShorterThanOrderIsSafe() {
        let tiny: [Float] = [0.1, -0.2, 0.3]
        let a = FormantEstimator.lpcCoefficients(tiny, order: 20)
        XCTAssertEqual(a.count, 20)
        XCTAssertTrue(a.allSatisfy { $0 == 0 })
        let env = FormantEstimator.lpcEnvelope(tiny, order: 20, binCount: 64, sampleRate: fs)
        XCTAssertTrue(env.allSatisfy(\.isFinite))
    }
}
