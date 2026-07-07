// GranularEngine.swift
//
// Offline granular resynthesis: scatters Hann-windowed grains of the
// source across the requested output duration. The read head walks
// linearly through the source (so an N-second render "plays through"
// the material once) while per-grain position jitter and pitch spread
// blur it into a cloud.
//
// Determinism: every random draw comes from one SplitMix64(seed:)
// stream, with a FIXED number of draws per grain (jitter, then pitch)
// regardless of parameter values — so the mapping seed → output is
// bit-identical across runs, and toggling one parameter doesn't
// re-scramble the others.
//
// Pitch is realised per grain by resampled reads: the grain reads the
// source at rate 2^(semis/12) with linear interpolation, while its
// Hann envelope stays in output time (constant grain duration, varied
// content pitch).
//
// Level safety: dense clouds pile grains up; the output is
// peak-normalised only when it exceeds ±1, so sparse settings keep
// their natural level.

import Foundation
import Accelerate

// MARK: - Parameters

/// Control parameters for one granular render. Codable so presets
/// can be persisted; Equatable so hosts can diff for re-renders.
public struct GranularParams: Codable, Equatable, Sendable {
    /// Grain duration in milliseconds (clamped to ≥ 1 ms).
    public var grainMs: Double
    /// Grain spawn rate in grains per second (clamped to > 0).
    public var densityHz: Double
    /// Position randomisation, 0…1 as a fraction of the source
    /// length, applied symmetrically around the linear read head.
    public var positionJitter: Double
    /// Per-grain pitch offset drawn uniformly in ±pitchSpreadSemis.
    public var pitchSpreadSemis: Double
    /// PRNG seed; same seed + params → bit-identical output.
    public var seed: UInt64

    public init(
        grainMs: Double = 80,
        densityHz: Double = 25,
        positionJitter: Double = 0.05,
        pitchSpreadSemis: Double = 0,
        seed: UInt64 = 0
    ) {
        self.grainMs = grainMs
        self.densityHz = densityHz
        self.positionJitter = positionJitter
        self.pitchSpreadSemis = pitchSpreadSemis
        self.seed = seed
    }
}

// MARK: - Engine

public enum GranularEngine {

    /// Render `durationSec` of granular texture from `input`. Output
    /// length is exactly `round(durationSec × sampleRate)` samples.
    /// Deterministic for a given (input, params, duration, rate).
    public static func render(
        _ input: [Float],
        params: GranularParams,
        durationSec: Double,
        sampleRate: Double
    ) -> [Float] {
        let outLen = Int((durationSec * sampleRate).rounded())
        guard outLen > 0 else { return [] }
        var out = [Float](repeating: 0, count: outLen)
        guard sampleRate > 0, input.count > 1 else { return out }

        let grainLen = max(2, Int((max(params.grainMs, 1) / 1_000 * sampleRate).rounded()))
        let spawnInterval = sampleRate / max(params.densityHz, 0.001)
        let envelope = grainEnvelope(grainLen)
        let sourceLen = Double(input.count)
        let maxReadPos = sourceLen - 1

        var rng = SplitMix64(seed: params.seed)

        input.withUnsafeBufferPointer { src in
            let x = src.baseAddress!
            var spawnTime = 0.0
            while spawnTime < Double(outLen) {
                // Fixed two draws per grain (see file header).
                let jitter = rng.nextSymmetricDouble() * params.positionJitter * sourceLen
                let semis = rng.nextSymmetricDouble() * params.pitchSpreadSemis
                let rate = pow(2.0, semis / 12.0)

                // Linear walk through the source over the render span.
                let progress = spawnTime / Double(outLen)
                let basePos = min(max(progress * maxReadPos + jitter, 0), maxReadPos)

                let outStart = Int(spawnTime.rounded())
                var readPos = basePos
                for k in 0..<grainLen {
                    let oi = outStart + k
                    if oi >= outLen || readPos >= maxReadPos { break }
                    let i0 = Int(readPos)
                    let frac = Float(readPos - Double(i0))
                    let sample = x[i0] + (x[i0 + 1] - x[i0]) * frac
                    out[oi] += sample * envelope[k]
                    readPos += rate
                }

                spawnTime += spawnInterval
            }
        }

        // Grain pileup protection: normalise only when clipping.
        // Division (not multiply-by-reciprocal) so the new peak is
        // exactly 1.0 with no upward rounding.
        var peak: Float = 0
        vDSP_maxmgv(out, 1, &peak, vDSP_Length(outLen))
        if peak > 1 {
            vDSP_vsdiv(out, 1, &peak, &out, 1, vDSP_Length(outLen))
        }
        return out
    }

    // MARK: - Envelope

    /// Symmetric Hann grain envelope, zero at both endpoints so
    /// grains never click on entry or exit.
    static func grainEnvelope(_ n: Int) -> [Float] {
        var w = [Float](repeating: 0, count: n)
        for i in 0..<n {
            w[i] = Float(0.5 - 0.5 * cos(2.0 * Double.pi * Double(i) / Double(n - 1)))
        }
        return w
    }
}
