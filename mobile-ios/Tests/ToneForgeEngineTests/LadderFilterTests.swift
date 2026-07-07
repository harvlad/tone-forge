// LadderFilterTests.swift
//
// Pins the ZDF ladder's frequency response: unity passband, −24 dB/oct
// rolloff (measured between warp-safe points), resonance peaking, and
// numerical stability. Response measured from the impulse response at
// small amplitude, where the tanh loop is linear.

import XCTest
import Accelerate
@testable import ToneForgeEngine

final class LadderFilterTests: XCTestCase {

    private let fs: Double = 48_000
    private let n = 16_384  // DFT size; Δf ≈ 2.93 Hz

    /// |H(f)| sampled from the impulse response, normalised so the
    /// input amplitude cancels.
    private func response(cutoffHz: Double, resonance: Double) -> [Float] {
        var filter = LadderFilter()
        filter.configure(cutoffHz: cutoffHz, resonance: resonance, sampleRate: fs)
        let amp: Float = 0.01  // linear region of fastTanh
        var x = [Float](repeating: 0, count: n)
        x[0] = amp
        x.withUnsafeMutableBufferPointer { buf in
            filter.process(buf.baseAddress!, count: n)
        }
        var mags = DSPTestSupport.magnitudeSpectrum(x)
        for i in mags.indices { mags[i] /= amp }
        return mags
    }

    private func bin(_ hz: Double) -> Int {
        Int((hz * Double(n) / fs).rounded())
    }

    func testPassbandUnity() {
        let mags = response(cutoffHz: 500, resonance: 0)
        // 50 Hz = one decade below cutoff: |H| ≈ 0 dB.
        let db = DSPTestSupport.dB(mags[bin(50)], over: 1)
        XCTAssertEqual(db, 0, accuracy: 0.5, "passband should be unity")
    }

    func testMinus24dBPerOctaveRolloff() {
        // Measure one octave (2 kHz → 4 kHz) well above a 500 Hz
        // cutoff (asymptotic region) and well below Nyquist (bilinear
        // warp still small). Analytic prediction incl. warp ≈ 23.9 dB.
        let mags = response(cutoffHz: 500, resonance: 0)
        let dbAt2k = DSPTestSupport.dB(mags[bin(2_000)], over: 1)
        let dbAt4k = DSPTestSupport.dB(mags[bin(4_000)], over: 1)
        let slope = dbAt2k - dbAt4k
        XCTAssertEqual(slope, 24, accuracy: 2.0, "4-pole ladder must roll off −24 dB/oct")
    }

    func testFourPoleNotTwoPole() {
        // Explicitly distinguish from a 2-pole (−12 dB/oct) response.
        let mags = response(cutoffHz: 500, resonance: 0)
        let slope = DSPTestSupport.dB(mags[bin(2_000)], over: 1)
            - DSPTestSupport.dB(mags[bin(4_000)], over: 1)
        XCTAssertGreaterThan(slope, 18, "slope way too shallow for a 4-pole")
    }

    func testResonancePeaksAtCutoff() {
        let flat = response(cutoffHz: 1_000, resonance: 0)
        let peaked = response(cutoffHz: 1_000, resonance: 0.8)
        let flatAtFc = flat[bin(1_000)]
        let peakedAtFc = peaked[bin(1_000)]
        XCTAssertGreaterThan(
            DSPTestSupport.dB(peakedAtFc, over: flatAtFc), 6,
            "resonance 0.8 should peak ≥ 6 dB at cutoff"
        )
    }

    func testResonanceReducesDCGain() {
        // Ladder DC gain = 1/(1+k); with k = 4·0.5 = 2 → −9.5 dB.
        let mags = response(cutoffHz: 1_000, resonance: 0.5)
        let dcDb = DSPTestSupport.dB(mags[bin(30)], over: 1)
        XCTAssertEqual(dcDb, -9.5, accuracy: 1.0)
    }

    func testStabilityAtMaxResonanceWithHotInput() {
        var filter = LadderFilter()
        filter.configure(cutoffHz: 8_000, resonance: 1.0, sampleRate: fs)
        // Deterministic pseudo-noise at full scale.
        var state: UInt64 = 0x9E3779B97F4A7C15
        var x = [Float](repeating: 0, count: 48_000)
        for i in x.indices {
            state = state &* 6364136223846793005 &+ 1442695040888963407
            x[i] = Float(Int64(bitPattern: state >> 11)) / Float(Int64.max >> 11)
        }
        x.withUnsafeMutableBufferPointer { buf in
            filter.process(buf.baseAddress!, count: buf.count)
        }
        for (i, v) in x.enumerated() {
            XCTAssertTrue(v.isFinite, "non-finite output at \(i)")
            XCTAssertLessThan(abs(v), 10, "unbounded output at \(i)")
        }
    }

    func testFastTanhShape() {
        // Linear slope 1 at origin…
        XCTAssertEqual(LadderFilter.fastTanh(0.001), 0.001, accuracy: 1e-6)
        XCTAssertEqual(LadderFilter.fastTanh(0), 0)
        // …odd symmetry…
        XCTAssertEqual(LadderFilter.fastTanh(-0.7), -LadderFilter.fastTanh(0.7))
        // …close to tanh in the musical range…
        for v: Float in [0.25, 0.5, 1.0, 1.5, 2.0] {
            XCTAssertEqual(LadderFilter.fastTanh(v), tanh(v), accuracy: 0.02)
        }
        // …and bounded for hot inputs.
        XCTAssertLessThanOrEqual(LadderFilter.fastTanh(100), 1.0)
    }

    func testResetClearsState() {
        var filter = LadderFilter()
        filter.configure(cutoffHz: 200, resonance: 0.5, sampleRate: fs)
        var x = [Float](repeating: 1, count: 512)
        x.withUnsafeMutableBufferPointer { filter.process($0.baseAddress!, count: 512) }
        filter.reset()
        // After reset, silence in → silence out (no stored energy).
        var silence = [Float](repeating: 0, count: 512)
        silence.withUnsafeMutableBufferPointer { filter.process($0.baseAddress!, count: 512) }
        XCTAssertEqual(silence.map(abs).max()!, 0)
    }
}
