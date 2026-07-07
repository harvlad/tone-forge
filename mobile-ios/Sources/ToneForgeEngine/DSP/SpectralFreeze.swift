// SpectralFreeze.swift
//
// Classic spectral freeze: capture the magnitude spectrum of one
// 2048-sample Hann-windowed frame, then resynthesize arbitrarily long
// audio by pairing those frozen magnitudes with fresh random phases
// every 512-sample synthesis hop. Randomising phase per frame (rather
// than looping the captured frame) removes the metallic buzz of a
// straight loop and yields the characteristic smeared, breathing
// texture.
//
// Transform plumbing (vDSP_DFT, complex-to-complex both ways):
//   analysis   x·hann → DFT → mags[0…N/2]
//   synthesis  spectrum built with explicit conjugate symmetry
//              (X[N−k] = conj(X[k]), imag DC/Nyquist forced to 0) so
//              the inverse transform is exactly real; the imaginary
//              output is discarded by construction, not by fiat.
// Frames are Hann-windowed on the way out and overlap-added; the
// accumulated window sum is divided off so frame edges don't ring.
//
// Determinism: all phases come from a single SplitMix64(seed:) stream
// consumed in frame order, so a given (input, atSample, duration,
// seed, rate) always renders bit-identically.

import Foundation
import Accelerate

public enum SpectralFreeze {

    // MARK: - Tunables

    /// Analysis/synthesis frame length (DFT size).
    static let frameLen = 2048
    /// Synthesis hop (75% overlap).
    static let hop = 512

    // MARK: - Public API

    /// Freeze the spectrum of the 2048-sample frame at `atSample`
    /// (clamped so the frame fits inside `input`; short inputs are
    /// zero-padded) and resynthesize `durationSec` of texture. Output
    /// length is exactly `round(durationSec × sampleRate)` samples.
    public static func freeze(
        _ input: [Float],
        atSample: Int,
        durationSec: Double,
        seed: UInt64,
        sampleRate: Double
    ) -> [Float] {
        let outLen = Int((durationSec * sampleRate).rounded())
        guard outLen > 0 else { return [] }
        var out = [Float](repeating: 0, count: outLen + frameLen)
        guard sampleRate > 0, !input.isEmpty else {
            return Array(out[0..<outLen])
        }

        let n = frameLen
        let half = n / 2
        let window = hannWindow(n)

        guard
            let forward = vDSP_DFT_zop_CreateSetup(nil, vDSP_Length(n), .FORWARD),
            let inverse = vDSP_DFT_zop_CreateSetup(nil, vDSP_Length(n), .INVERSE)
        else {
            return Array(out[0..<outLen])
        }
        defer {
            vDSP_DFT_DestroySetup(forward)
            vDSP_DFT_DestroySetup(inverse)
        }

        // MARK: Analysis — frozen magnitudes

        let start = min(max(atSample, 0), max(0, input.count - n))
        var frame = [Float](repeating: 0, count: n)
        let available = min(n, input.count - start)
        for i in 0..<available { frame[i] = input[start + i] }
        vDSP_vmul(frame, 1, window, 1, &frame, 1, vDSP_Length(n))

        let zeros = [Float](repeating: 0, count: n)
        var specRe = [Float](repeating: 0, count: n)
        var specIm = [Float](repeating: 0, count: n)
        vDSP_DFT_Execute(forward, frame, zeros, &specRe, &specIm)

        var mags = [Double](repeating: 0, count: half + 1)
        for k in 0...half {
            let re = Double(specRe[k])
            let im = Double(specIm[k])
            mags[k] = (re * re + im * im).squareRoot()
        }

        // MARK: Synthesis — random phases per frame, OLA

        var rng = SplitMix64(seed: seed)
        var windowSum = [Float](repeating: 0, count: outLen + frameLen)
        var timeRe = [Float](repeating: 0, count: n)
        var timeIm = [Float](repeating: 0, count: n)
        let invScale = 1.0 / Double(n)  // vDSP inverse DFT is unnormalised

        var synPos = 0
        while synPos < outLen {
            // Real DC/Nyquist keep the frame exactly real.
            specRe[0] = Float(mags[0])
            specIm[0] = 0
            specRe[half] = Float(mags[half])
            specIm[half] = 0
            for k in 1..<half {
                let theta = rng.nextUnitDouble() * 2.0 * Double.pi
                let re = Float(mags[k] * cos(theta))
                let im = Float(mags[k] * sin(theta))
                specRe[k] = re
                specIm[k] = im
                specRe[n - k] = re     // conjugate symmetry
                specIm[n - k] = -im
            }

            vDSP_DFT_Execute(inverse, specRe, specIm, &timeRe, &timeIm)

            for i in 0..<n {
                let oi = synPos + i
                out[oi] += Float(Double(timeRe[i]) * invScale) * window[i]
                windowSum[oi] += window[i]
            }

            synPos += hop
        }

        let eps: Float = 1e-6
        for i in 0..<outLen where windowSum[i] > eps {
            out[i] /= windowSum[i]
        }
        out.removeLast(out.count - outLen)
        return out
    }

    // MARK: - Window

    /// Periodic Hann (matches WSOLAStretch; constant-sum at hop N/4).
    static func hannWindow(_ n: Int) -> [Float] {
        var w = [Float](repeating: 0, count: n)
        vDSP_hann_window(&w, vDSP_Length(n), Int32(vDSP_HANN_DENORM))
        return w
    }
}
