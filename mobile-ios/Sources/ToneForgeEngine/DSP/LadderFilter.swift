// LadderFilter.swift
//
// Zero-delay-feedback (TPT, Zavalishin) 4-pole lowpass ladder with a
// tanh-saturated feedback path. −24 dB/oct rolloff; resonance 0…1
// maps to feedback k = 0…4 (self-oscillation approached at 1).
//
// Per-stage one-pole TPT:
//   y = G·x + s/(1+g)          where g = tan(π·fc/fs), G = g/(1+g)
//   s ← 2y − s
//
// Cascading four stages and closing the loop u = x − k·y₄ gives the
// zero-delay solve:
//   y₄ = (G⁴·x + G³S₁ + G²S₂ + G·S₃ + S₄) / (1 + k·G⁴),  Sᵢ = sᵢ/(1+g)
//
// The nonlinearity saturates ONLY the feedback term (u = x − k·tanh(y₄))
// after the zero-delay solve. Keeping the dry input path linear matters:
// at resonance 0 the filter is exactly linear, so a full-scale saw
// through it produces no distortion harmonics — which would otherwise
// alias and blow the synth's −60 dBFS alias gate. With resonance up,
// the saturated feedback bounds self-oscillation musically.
//
// A struct (not class) so each synth voice embeds its own filter with
// zero allocation on the render thread.

import Foundation

public struct LadderFilter: Sendable {

    // Stage states.
    private var s1: Float = 0
    private var s2: Float = 0
    private var s3: Float = 0
    private var s4: Float = 0

    // Coefficients derived in configure().
    private var G: Float = 0          // g/(1+g)
    private var invOnePlusG: Float = 1
    private var k: Float = 0          // feedback 0…4
    private var G4: Float = 0         // G⁴

    public init() {}

    /// Recompute coefficients. Call at control rate (per render block
    /// or on param change), not per sample. Cutoff is clamped to
    /// [10 Hz, 0.49·fs] to keep tan() sane.
    public mutating func configure(cutoffHz: Double, resonance: Double, sampleRate: Double) {
        let fc = min(max(cutoffHz, 10), sampleRate * 0.49)
        let g = Float(tan(.pi * fc / sampleRate))
        G = g / (1 + g)
        invOnePlusG = 1 / (1 + g)
        k = Float(min(max(resonance, 0), 1)) * 4
        G4 = G * G * G * G
    }

    /// Zero the stage memory (voice re-arm).
    public mutating func reset() {
        s1 = 0; s2 = 0; s3 = 0; s4 = 0
    }

    /// Process `count` samples in place.
    public mutating func process(_ x: UnsafeMutablePointer<Float>, count: Int) {
        // Hoist coefficients into locals for the tight loop.
        let G = self.G
        let G2 = G * G
        let G3 = G2 * G
        let G4 = self.G4
        let inv1g = self.invOnePlusG
        let k = self.k
        let fbDenom = 1 / (1 + k * G4)

        var s1 = self.s1, s2 = self.s2, s3 = self.s3, s4 = self.s4

        for i in 0..<count {
            let input = x[i]
            let S1 = s1 * inv1g
            let S2 = s2 * inv1g
            let S3 = s3 * inv1g
            let S4 = s4 * inv1g

            // Zero-delay estimate of the 4th stage output.
            let y4Lin = (G4 * input + G3 * S1 + G2 * S2 + G * S3 + S4) * fbDenom
            // Saturate the feedback term only — dry path stays linear.
            let u = input - k * Self.fastTanh(y4Lin)

            // Advance the four stages with the solved input.
            let y1 = G * u + S1;  s1 = 2 * y1 - s1
            let y2 = G * y1 + S2; s2 = 2 * y2 - s2
            let y3 = G * y2 + S3; s3 = 2 * y3 - s3
            let y4 = G * y3 + S4; s4 = 2 * y4 - s4

            x[i] = y4
        }

        self.s1 = s1; self.s2 = s2; self.s3 = s3; self.s4 = s4
    }

    /// Rational tanh approximation (Padé 7/6 from the Lambert
    /// continued fraction) — no transcendental call on the render
    /// thread. Error < 1e-3 everywhere (≈ 2e-6 at |x| = 2); exactly
    /// linear slope 1 at the origin (keeps the small-signal response
    /// ideal) and bounded below 1 thanks to the ±4 clamp.
    @inline(__always)
    static func fastTanh(_ x: Float) -> Float {
        let clamped = min(max(x, -4), 4)
        let x2 = clamped * clamped
        let num = clamped * (135_135 + x2 * (17_325 + x2 * (378 + x2)))
        let den = 135_135 + x2 * (62_370 + x2 * (3_150 + x2 * 28))
        return num / den
    }
}
