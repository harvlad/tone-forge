// SpectralVocoderTests.swift
//
// Gates the channel-vocoder core: OLA/unity transparency when the
// carrier IS the modulator, band selectivity (a sine modulator must
// concentrate a noise carrier's energy at the sine's band), sibilance
// passthrough above the cutoff, exact output length, and bit-exact
// determinism.

import XCTest
import Accelerate
@testable import ToneForgeEngine

final class SpectralVocoderTests: XCTestCase {

    private let fs: Double = 48_000

    // MARK: - Signal helpers

    /// Deterministic LCG noise in ±0.5 — no shared RNG dependency.
    private func whiteNoise(_ count: Int, seed: UInt64) -> [Float] {
        var state = seed
        var out = [Float](repeating: 0, count: count)
        for i in 0..<count {
            state = state &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
            out[i] = Float(state >> 40) / Float(1 << 24) - 0.5
        }
        return out
    }

    private func sine(_ freq: Double, count: Int, amplitude: Float = 0.5) -> [Float] {
        (0..<count).map { amplitude * Float(sin(2 * Double.pi * freq * Double($0) / fs)) }
    }

    /// Normalized zero-lag cross-correlation over a central region.
    private func correlation(_ x: [Float], _ y: [Float], skip: Int) -> Float {
        precondition(x.count == y.count && x.count > 2 * skip)
        let n = vDSP_Length(x.count - 2 * skip)
        var xy: Float = 0
        var xx: Float = 0
        var yy: Float = 0
        x.withUnsafeBufferPointer { xb in
            y.withUnsafeBufferPointer { yb in
                vDSP_dotpr(xb.baseAddress! + skip, 1, yb.baseAddress! + skip, 1, &xy, n)
                vDSP_dotpr(xb.baseAddress! + skip, 1, xb.baseAddress! + skip, 1, &xx, n)
                vDSP_dotpr(yb.baseAddress! + skip, 1, yb.baseAddress! + skip, 1, &yy, n)
            }
        }
        return xy / max(sqrt(xx * yy), 1e-12)
    }

    /// Power of a central 32768-sample slice of `x` inside [lo, hi] Hz.
    private func bandPower(_ x: [Float], lo: Double, hi: Double) -> Float {
        let n = 32_768
        precondition(x.count >= n)
        let start = (x.count - n) / 2
        let mags = DSPTestSupport.magnitudeSpectrum(Array(x[start..<start + n]))
        let hzPerBin = fs / Double(n)
        var power: Float = 0
        for k in mags.indices {
            let f = Double(k) * hzPerBin
            if f >= lo && f <= hi { power += mags[k] * mags[k] }
        }
        return power
    }

    // MARK: - Unity path

    func testUnityPathIsNearTransparent() {
        let x = whiteNoise(48_000, seed: 0xF00D)
        let y = SpectralVocoder.process(
            modulator: x, carrier: x, config: VocoderConfig(), sampleRate: fs
        )
        XCTAssertEqual(y.count, x.count)
        XCTAssertTrue(y.allSatisfy(\.isFinite))
        XCTAssertGreaterThanOrEqual(correlation(x, y, skip: 4_096), 0.9)
    }

    // MARK: - Band selectivity

    func testSineModulatorGatesNoiseCarrier() {
        let modulator = sine(440, count: 48_000)
        let carrier = whiteNoise(48_000, seed: 0xBEEF)
        let y = SpectralVocoder.process(
            modulator: modulator, carrier: carrier,
            config: VocoderConfig(), sampleRate: fs
        )
        // Energy in the band holding 440 Hz vs a band two octaves up.
        let near = bandPower(y, lo: 400, hi: 480)
        let far = bandPower(y, lo: 1_600, hi: 1_920)
        let separation = DSPTestSupport.dB(near.squareRoot(), over: far.squareRoot())
        XCTAssertGreaterThanOrEqual(separation, 12, "band separation only \(separation) dB")
    }

    // MARK: - Sibilance passthrough

    func testSibilancePassesHighModulatorContent() {
        let modulator = sine(8_000, count: 48_000)
        let carrier = sine(200, count: 48_000)
        let y = SpectralVocoder.process(
            modulator: modulator, carrier: carrier,
            config: VocoderConfig(), sampleRate: fs
        )
        XCTAssertTrue(y.allSatisfy(\.isFinite))
        let sibilant = bandPower(y, lo: 7_900, hi: 8_100)
        let total = bandPower(y, lo: 0, hi: fs / 2)
        XCTAssertGreaterThan(total, 0)
        // The 0.3 passthrough of an amplitude-0.5 sine must carry a
        // clearly measurable share of the output.
        XCTAssertGreaterThan(sibilant / total, 0.05, "8 kHz content missing")
    }

    // MARK: - Contract

    func testDeterminismBitIdentical() {
        let modulator = whiteNoise(24_000, seed: 0xCAFE)
        let carrier = sine(220, count: 10_000)
        let a = SpectralVocoder.process(
            modulator: modulator, carrier: carrier,
            config: VocoderConfig(), sampleRate: fs
        )
        let b = SpectralVocoder.process(
            modulator: modulator, carrier: carrier,
            config: VocoderConfig(), sampleRate: fs
        )
        XCTAssertEqual(a, b)
    }

    func testOutputLengthMatchesModulator() {
        // Off-grid length and a carrier much shorter than the
        // modulator (must loop, not truncate).
        let modulator = whiteNoise(12_345, seed: 1)
        let carrier = whiteNoise(1_000, seed: 2)
        let y = SpectralVocoder.process(
            modulator: modulator, carrier: carrier,
            config: VocoderConfig(), sampleRate: fs
        )
        XCTAssertEqual(y.count, modulator.count)
        XCTAssertTrue(y.allSatisfy(\.isFinite))

        let empty = SpectralVocoder.process(
            modulator: [], carrier: carrier, config: VocoderConfig(), sampleRate: fs
        )
        XCTAssertTrue(empty.isEmpty)

        let silentCarrier = SpectralVocoder.process(
            modulator: modulator, carrier: [], config: VocoderConfig(), sampleRate: fs
        )
        XCTAssertEqual(silentCarrier.count, modulator.count)
        XCTAssertTrue(silentCarrier.allSatisfy { $0 == 0 })
    }
}
