// WSOLAStretchTests.swift
//
// Pins the WSOLA stretcher's contract: unity factor is transparent,
// stretching changes duration to spec without moving pitch (measured
// via normalised autocorrelation with parabolic lag refinement), and
// the whole pipeline is deterministic.

import XCTest
import Accelerate
@testable import ToneForgeEngine

final class WSOLAStretchTests: XCTestCase {

    private let fs: Double = 48_000

    // MARK: - Helpers

    private func sine(freq: Double, seconds: Double, amp: Float = 0.8) -> [Float] {
        let n = Int(seconds * fs)
        var x = [Float](repeating: 0, count: n)
        for i in 0..<n {
            x[i] = amp * Float(sin(2.0 * Double.pi * freq * Double(i) / fs))
        }
        return x
    }

    /// Normalised cross-correlation at lag 0.
    private func ncc(_ a: ArraySlice<Float>, _ b: ArraySlice<Float>) -> Double {
        precondition(a.count == b.count)
        let av = Array(a), bv = Array(b)
        var dot: Float = 0, ea: Float = 0, eb: Float = 0
        vDSP_dotpr(av, 1, bv, 1, &dot, vDSP_Length(av.count))
        vDSP_dotpr(av, 1, av, 1, &ea, vDSP_Length(av.count))
        vDSP_dotpr(bv, 1, bv, 1, &eb, vDSP_Length(bv.count))
        return Double(dot) / max(sqrt(Double(ea) * Double(eb)), 1e-12)
    }

    /// Fundamental estimate from the peak of the normalised
    /// autocorrelation over the central region, refined with
    /// parabolic interpolation for sub-sample lag accuracy.
    private func estimatePitchHz(_ x: [Float], minHz: Double, maxHz: Double) -> Double {
        let minLag = Int(fs / maxHz)
        let maxLag = Int(fs / minHz)
        let window = 16_384
        precondition(x.count >= window + maxLag + 2)
        let offset = (x.count - (window + maxLag)) / 2

        var r = [Double](repeating: 0, count: maxLag + 2)
        x.withUnsafeBufferPointer { buf in
            let base = buf.baseAddress! + offset
            var e0: Float = 0
            vDSP_dotpr(base, 1, base, 1, &e0, vDSP_Length(window))
            for lag in minLag...(maxLag + 1) {
                var dot: Float = 0, el: Float = 0
                vDSP_dotpr(base, 1, base + lag, 1, &dot, vDSP_Length(window))
                vDSP_dotpr(base + lag, 1, base + lag, 1, &el, vDSP_Length(window))
                r[lag] = Double(dot) / max(sqrt(Double(e0) * Double(el)), 1e-12)
            }
        }

        var bestLag = minLag
        for lag in minLag...maxLag where r[lag] > r[bestLag] { bestLag = lag }

        // Parabolic refinement around the integer peak.
        var refined = Double(bestLag)
        if bestLag > minLag && bestLag < maxLag {
            let ym = r[bestLag - 1], y0 = r[bestLag], yp = r[bestLag + 1]
            let denom = ym - 2 * y0 + yp
            if abs(denom) > 1e-12 {
                refined += 0.5 * (ym - yp) / denom
            }
        }
        return fs / refined
    }

    // MARK: - Tests

    func testUnityFactorIsTransparent() {
        var x = sine(freq: 220, seconds: 1.0, amp: 0.5)
        let overtone = sine(freq: 733, seconds: 1.0, amp: 0.3)
        for i in x.indices { x[i] += overtone[i] }

        let y = WSOLAStretch.stretch(x, factor: 1.0, sampleRate: fs)
        XCTAssertEqual(y.count, x.count)

        // Central region: skip one frame at each edge.
        let margin = 2_048
        let corr = ncc(x[margin..<(x.count - margin)], y[margin..<(y.count - margin)])
        XCTAssertGreaterThanOrEqual(corr, 0.999, "unity factor should be transparent")
    }

    func testFactorTwoDoublesLengthAndKeepsPitch() {
        let x = sine(freq: 220, seconds: 1.0)
        let y = WSOLAStretch.stretch(x, factor: 2.0, sampleRate: fs)

        let expected = Double(x.count) * 2
        XCTAssertLessThanOrEqual(
            abs(Double(y.count) - expected), expected * 0.02,
            "stretched length should be within 2% of 2×"
        )

        let pitch = estimatePitchHz(y, minHz: 100, maxHz: 500)
        let cents = 1_200 * log2(pitch / 220)
        XCTAssertLessThanOrEqual(abs(cents), 3, "pitch moved by \(cents) cents")
    }

    func testFactorHalfHalvesLength() {
        let x = sine(freq: 220, seconds: 1.0)
        let y = WSOLAStretch.stretch(x, factor: 0.5, sampleRate: fs)
        let expected = Double(x.count) * 0.5
        XCTAssertLessThanOrEqual(
            abs(Double(y.count) - expected), expected * 0.02,
            "compressed length should be within 2% of half"
        )
    }

    func testDeterministicAcrossRuns() {
        var x = sine(freq: 220, seconds: 0.5, amp: 0.4)
        let partial = sine(freq: 1_337, seconds: 0.5, amp: 0.2)
        for i in x.indices { x[i] += partial[i] }

        let a = WSOLAStretch.stretch(x, factor: 1.37, sampleRate: fs)
        let b = WSOLAStretch.stretch(x, factor: 1.37, sampleRate: fs)
        XCTAssertEqual(a, b, "stretch must be bit-identical run to run")
    }
}
