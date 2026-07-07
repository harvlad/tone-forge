// DSPTestSupport.swift
//
// Shared spectral helpers for the DSP test suites. Test-only — kept
// out of the engine target so shipping code can't grow a dependency
// on it.

import Foundation
import Accelerate
@testable import ToneForgeEngine

enum DSPTestSupport {

    /// Magnitude spectrum of `x` via a complex DFT (imag = 0).
    /// Returns count/2 bins (DC…Nyquist). `x.count` must be a power
    /// of two supported by vDSP_DFT.
    static func magnitudeSpectrum(_ x: [Float]) -> [Float] {
        let n = x.count
        precondition(n > 0 && (n & (n - 1)) == 0, "power-of-two input required")
        guard let setup = vDSP_DFT_zop_CreateSetup(nil, vDSP_Length(n), .FORWARD) else {
            fatalError("vDSP_DFT setup failed for n=\(n)")
        }
        defer { vDSP_DFT_DestroySetup(setup) }

        let zeros = [Float](repeating: 0, count: n)
        var outReal = [Float](repeating: 0, count: n)
        var outImag = [Float](repeating: 0, count: n)
        vDSP_DFT_Execute(setup, x, zeros, &outReal, &outImag)

        var mags = [Float](repeating: 0, count: n / 2)
        for i in 0..<(n / 2) {
            mags[i] = sqrt(outReal[i] * outReal[i] + outImag[i] * outImag[i])
        }
        return mags
    }

    /// 4-term Blackman–Harris window (−92 dB sidelobes) — needed for
    /// the alias gate, where Hann's −31 dB sidelobes would swamp a
    /// −60 dB threshold.
    static func blackmanHarris(_ n: Int) -> [Float] {
        let a0: Double = 0.35875, a1 = 0.48829, a2 = 0.14128, a3 = 0.01168
        var w = [Float](repeating: 0, count: n)
        for i in 0..<n {
            let t = 2.0 * Double.pi * Double(i) / Double(n - 1)
            w[i] = Float(a0 - a1 * cos(t) + a2 * cos(2 * t) - a3 * cos(3 * t))
        }
        return w
    }

    /// Element-wise multiply.
    static func applyWindow(_ x: [Float], _ w: [Float]) -> [Float] {
        precondition(x.count == w.count)
        var out = [Float](repeating: 0, count: x.count)
        vDSP_vmul(x, 1, w, 1, &out, 1, vDSP_Length(x.count))
        return out
    }

    /// 20·log10(a/b) with a floor to avoid -inf.
    static func dB(_ a: Float, over b: Float) -> Float {
        20 * log10(max(a, 1e-12) / max(b, 1e-12))
    }
}
