// SeededRandom.swift
//
// SplitMix64 (Steele, Lea & Flood, "Fast Splittable Pseudorandom
// Number Generators") — the reference seeding/stream generator used
// by the granular and spectral-freeze DSP so that renders are
// bit-identical across runs and platforms for a given seed.
//
// Chosen over SystemRandomNumberGenerator (non-deterministic) and
// over larger-state PRNGs because the DSP needs only modest stream
// lengths (thousands of draws per render), a single UInt64 of state,
// and exact cross-platform reproducibility. All arithmetic is
// wrapping 64-bit integer math, so results do not depend on the
// host's floating-point behaviour.

import Foundation

/// Deterministic 64-bit PRNG (SplitMix64). Same seed → same stream,
/// on every run and every platform.
public struct SplitMix64: RandomNumberGenerator, Sendable {

    private var state: UInt64

    /// Create a generator whose entire output stream is determined
    /// by `seed`.
    public init(seed: UInt64) {
        self.state = seed
    }

    /// Next 64 uniformly distributed bits.
    public mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }

    /// Uniform Double in [0, 1) using the top 53 bits (full mantissa
    /// precision, bias-free).
    public mutating func nextUnitDouble() -> Double {
        Double(next() >> 11) * 0x1p-53
    }

    /// Uniform Double in [-1, 1).
    public mutating func nextSymmetricDouble() -> Double {
        nextUnitDouble() * 2 - 1
    }
}
