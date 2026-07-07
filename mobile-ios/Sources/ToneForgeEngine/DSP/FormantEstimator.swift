// FormantEstimator.swift
//
// All-pole (LPC) spectral-envelope estimation for the vocal DSP chain.
// Pure functions, no state — PSOLAHarmonizer uses the envelope for
// formant preservation, and the same envelope is a natural fit for
// future vowel-morph features.
//
// Method: autocorrelation LPC. The frame's autocorrelation r[0…p] is
// computed with vDSP dot products, then Levinson–Durbin recursion
// solves the normal equations for the prediction coefficients a[1…p]
// with x̂[n] = Σ a[k]·x[n−k], i.e. the whitening filter is
//   A(z) = 1 − Σ_{k=1}^{p} a[k]·z^(−k)
// The spectral envelope is |1/A(e^jw)| — smooth across harmonics, with
// peaks at the formants. The recursion runs in Double for numerical
// headroom (order-20 systems are near-singular for very peaky frames);
// a tiny ridge on r[0] keeps it well-conditioned. Degenerate frames
// (silence, order too high for the frame) yield zero coefficients and
// a flat envelope — never NaN/inf.

import Foundation
import Accelerate

public enum FormantEstimator {

    // MARK: - LPC analysis

    /// Linear-prediction coefficients a[1…order] for `frame`, via
    /// Levinson–Durbin on the frame's autocorrelation.
    ///
    /// The returned array has `order` elements; element k−1 is a[k] in
    /// x̂[n] = Σ a[k]·x[n−k] (whitening filter A(z) = 1 − Σ a[k]z^−k).
    /// Silence or a frame shorter than `order`+1 samples returns all
    /// zeros (A(z) = 1, flat envelope).
    public static func lpcCoefficients(_ frame: [Float], order: Int) -> [Float] {
        guard order > 0 else { return [] }
        let zeros = [Float](repeating: 0, count: order)
        guard frame.count > order else { return zeros }

        // Autocorrelation r[0…order] in Double.
        let x = frame.map(Double.init)
        let n = x.count
        var r = [Double](repeating: 0, count: order + 1)
        x.withUnsafeBufferPointer { buf in
            let base = buf.baseAddress!
            for lag in 0...order {
                vDSP_dotprD(base, 1, base + lag, 1, &r[lag], vDSP_Length(n - lag))
            }
        }
        guard r[0] > 1e-12 else { return zeros }
        // Small ridge (white-noise floor) keeps the recursion
        // well-conditioned for near-perfectly-predictable frames.
        r[0] *= 1.0 + 1e-9

        // Levinson–Durbin with the A(z) = 1 + Σ c[k]z^−k convention;
        // prediction coefficients are a[k] = −c[k].
        var c = [Double](repeating: 0, count: order + 1)
        var err = r[0]
        for i in 1...order {
            var acc = r[i]
            for j in 1..<i { acc += c[j] * r[i - j] }
            let k = -acc / err
            var updated = c
            updated[i] = k
            for j in 1..<i { updated[j] = c[j] + k * c[i - j] }
            c = updated
            err *= 1 - k * k
            if !(err > 0) { break }  // singular — keep coefficients so far
        }
        return (1...order).map { Float(-c[$0]) }
    }

    // MARK: - Spectral envelope

    /// Magnitude of 1/A(e^jw) sampled at `binCount` linear-frequency
    /// points from 0 Hz to Nyquist (`sampleRate`/2) inclusive,
    /// peak-normalized to 1.0.
    ///
    /// Bin b maps to frequency b·(sampleRate/2)/(binCount−1). Silence
    /// (or any degenerate frame) yields a flat all-ones envelope; the
    /// result never contains NaN or infinity.
    public static func lpcEnvelope(
        _ frame: [Float], order: Int, binCount: Int, sampleRate: Double
    ) -> [Float] {
        guard binCount > 0 else { return [] }
        let flat = [Float](repeating: 1, count: binCount)
        guard binCount > 1 else { return flat }

        let a = lpcCoefficients(frame, order: order).map(Double.init)
        guard a.contains(where: { $0 != 0 }) else { return flat }

        // |1/A| at w = π·b/(binCount−1): A = 1 − Σ a[k]e^(−jwk).
        var env = [Double](repeating: 0, count: binCount)
        for b in 0..<binCount {
            let w = Double.pi * Double(b) / Double(binCount - 1)
            var re = 1.0
            var im = 0.0
            for k in a.indices {
                let wk = w * Double(k + 1)
                re -= a[k] * cos(wk)
                im += a[k] * sin(wk)
            }
            env[b] = 1.0 / max(sqrt(re * re + im * im), 1e-9)
        }
        let peak = env.max() ?? 1
        guard peak > 0, peak.isFinite else { return flat }
        return env.map { Float($0 / peak) }
    }
}
