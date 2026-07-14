// SyntheticCorpus.swift
//
// Deterministic synthetic drum-onset generator. Each DrumRole is
// synthesised as a short 48 kHz mono slice with parametric augmentation
// (gain, pitch/centroid jitter, decay, noise mix, mic roll-off), then
// run through the *same* OnsetFeatures.extract the runtime uses — so the
// model trains on exactly the feature distribution it will see live.

import Foundation
import ToneForgeEngine

/// One labeled training example: canonical feature vector + role label.
struct LabeledExample {
    let features: [Double]   // OnsetFeatures.featureNames order
    let role: String         // DrumRole.rawValue
}

/// Small seeded PRNG so corpus generation is reproducible build-to-build.
struct SplitMix64: RandomNumberGenerator {
    private var state: UInt64
    init(seed: UInt64) { state = seed }
    mutating func next() -> UInt64 {
        state &+= 0x9E3779B97F4A7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58476D1CE4E5B9
        z = (z ^ (z >> 27)) &* 0x94D049BB133111EB
        return z ^ (z >> 31)
    }
}

enum SyntheticCorpus {
    static let sampleRate = 48_000.0

    /// Generate `perRole` augmented examples for every DrumRole.
    static func generate(perRole: Int, seed: UInt64 = 0xB347) -> [LabeledExample] {
        var rng = SplitMix64(seed: seed)
        var out: [LabeledExample] = []
        out.reserveCapacity(perRole * DrumRole.allCases.count)
        for role in DrumRole.allCases {
            for _ in 0..<perRole {
                let slice = synthesize(role: role, rng: &rng)
                let feat = OnsetFeatures.extract(slice, sampleRate: sampleRate)
                out.append(LabeledExample(features: feat.featureVector, role: role.rawValue))
            }
        }
        return out
    }

    // MARK: - Per-role synthesis

    private static func synthesize(role: DrumRole, rng: inout SplitMix64) -> [Float] {
        switch role {
        case .kick:      return kick(&rng)
        case .snare:     return snare(&rng)
        case .closedHat: return hat(&rng, open: false)
        case .openHat:   return hat(&rng, open: true)
        case .clap:      return clap(&rng)
        case .rim:       return rim(&rng)
        case .perc:      return perc(&rng)
        }
    }

    /// Low sine body (50–110 Hz) + sharp click transient, exp decay.
    private static func kick(_ rng: inout SplitMix64) -> [Float] {
        let f0 = uniform(50, 110, &rng)
        let dur = uniform(0.09, 0.16, &rng)
        let decay = uniform(18, 34, &rng)
        let clickAmt = uniform(0.05, 0.2, &rng)
        let n = Int(dur * sampleRate)
        var x = [Float](repeating: 0, count: n)
        // Pitch drop on the body (classic kick "pitch envelope").
        let drop = uniform(1.4, 2.2, &rng)
        for i in 0..<n {
            let t = Double(i) / sampleRate
            let env = exp(-decay * t)
            let f = f0 * (1 + (drop - 1) * exp(-40 * t))
            let body = sin(2 * .pi * f * t)
            let click = clickAmt * exp(-400 * t) * whiteNoise(&rng)
            x[i] = Float((body * env) + click)
        }
        return finish(x, gain: uniform(0.5, 1.0, &rng), micRolloff: true, &rng)
    }

    /// Noise burst + weak tonal body (~180 Hz), medium decay.
    private static func snare(_ rng: inout SplitMix64) -> [Float] {
        let dur = uniform(0.07, 0.13, &rng)
        let decay = uniform(28, 48, &rng)
        let tone = uniform(150, 230, &rng)
        let toneMix = uniform(0.15, 0.35, &rng)
        let n = Int(dur * sampleRate)
        var x = [Float](repeating: 0, count: n)
        for i in 0..<n {
            let t = Double(i) / sampleRate
            let env = exp(-decay * t)
            let noise = whiteNoise(&rng)
            let body = toneMix * sin(2 * .pi * tone * t)
            x[i] = Float(((1 - toneMix) * noise + body) * env)
        }
        return finish(x, gain: uniform(0.4, 0.9, &rng), micRolloff: false, &rng)
    }

    /// High-frequency filtered noise. Open = longer decay.
    private static func hat(_ rng: inout SplitMix64, open: Bool) -> [Float] {
        let dur = open ? uniform(0.16, 0.30, &rng) : uniform(0.03, 0.07, &rng)
        let decay = open ? uniform(10, 22, &rng) : uniform(60, 110, &rng)
        let n = Int(dur * sampleRate)
        var x = [Float](repeating: 0, count: n)
        var prev: Float = 0
        for i in 0..<n {
            let t = Double(i) / sampleRate
            let env = exp(-decay * t)
            // High-pass the noise (difference emphasises highs).
            let w = whiteNoise(&rng)
            let hp = Float(w) - prev
            prev = Float(w)
            x[i] = hp * Float(env)
        }
        return finish(x, gain: uniform(0.3, 0.8, &rng), micRolloff: false, &rng)
    }

    /// Multi-lobe noise bursts (3–4 claps), mid-band, longer envelope.
    private static func clap(_ rng: inout SplitMix64) -> [Float] {
        let dur = uniform(0.13, 0.22, &rng)
        let n = Int(dur * sampleRate)
        var x = [Float](repeating: 0, count: n)
        let lobes = Int(uniform(3, 5, &rng))
        let gap = uniform(0.006, 0.012, &rng)
        for i in 0..<n {
            let t = Double(i) / sampleRate
            var env = 0.0
            for k in 0..<lobes {
                let lt = t - Double(k) * gap
                if lt >= 0 { env += exp(-90 * lt) }
            }
            env += 0.4 * exp(-18 * t) // tail
            x[i] = Float(whiteNoise(&rng) * env)
        }
        return finish(x, gain: uniform(0.4, 0.9, &rng), micRolloff: false, &rng)
    }

    /// Bright, very short click with faint tonal ring (~2 kHz).
    private static func rim(_ rng: inout SplitMix64) -> [Float] {
        let dur = uniform(0.02, 0.045, &rng)
        let ring = uniform(1600, 2600, &rng)
        let n = Int(dur * sampleRate)
        var x = [Float](repeating: 0, count: n)
        for i in 0..<n {
            let t = Double(i) / sampleRate
            let env = exp(-160 * t)
            let click = whiteNoise(&rng)
            let tone = sin(2 * .pi * ring * t)
            x[i] = Float((0.5 * click + 0.5 * tone) * env)
        }
        return finish(x, gain: uniform(0.3, 0.8, &rng), micRolloff: false, &rng)
    }

    /// Mid tonal blip (tom/conga-ish, 200–500 Hz), semi-tonal.
    private static func perc(_ rng: inout SplitMix64) -> [Float] {
        let f0 = uniform(200, 520, &rng)
        let dur = uniform(0.08, 0.15, &rng)
        let decay = uniform(20, 40, &rng)
        let noiseMix = uniform(0.1, 0.3, &rng)
        let n = Int(dur * sampleRate)
        var x = [Float](repeating: 0, count: n)
        for i in 0..<n {
            let t = Double(i) / sampleRate
            let env = exp(-decay * t)
            let body = sin(2 * .pi * f0 * t)
            let noise = noiseMix * whiteNoise(&rng)
            x[i] = Float(((1 - noiseMix) * body + noise) * env)
        }
        return finish(x, gain: uniform(0.4, 0.9, &rng), micRolloff: true, &rng)
    }

    // MARK: - Shared helpers

    /// Apply gain, optional 1-pole low-shelf mic roll-off, and a tiny
    /// noise floor so silence-tail slices stay realistic.
    private static func finish(
        _ x: [Float], gain: Double, micRolloff: Bool, _ rng: inout SplitMix64
    ) -> [Float] {
        var y = x
        if micRolloff {
            // Attenuate sub-bass a touch (phone/laptop mic) — leaves the
            // kick body but reduces the low-band ratio toward real mics.
            let a: Float = 0.15
            var prev: Float = 0
            for i in 0..<y.count {
                let hp = y[i] - prev + a * prev
                prev = y[i]
                y[i] = hp
            }
        }
        let noiseFloor = Float(uniform(0.0005, 0.003, &rng))
        for i in 0..<y.count {
            y[i] = Float(gain) * y[i] + noiseFloor * Float(whiteNoise(&rng))
        }
        return y
    }

    private static func uniform(_ lo: Double, _ hi: Double, _ rng: inout SplitMix64) -> Double {
        let u = Double(rng.next() >> 11) * (1.0 / 9007199254740992.0) // [0,1)
        return lo + (hi - lo) * u
    }

    private static func whiteNoise(_ rng: inout SplitMix64) -> Double {
        2 * (Double(rng.next() >> 11) * (1.0 / 9007199254740992.0)) - 1
    }
}
