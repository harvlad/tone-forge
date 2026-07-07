// PadTransform.swift
//
// Engine-side DSP transforms for pad samples: reverse, stutter, stretch,
// granular, PSOLA-based pitch/harmony, rhythmic gate, spectral freeze.
// Codable with a frozen wire shape (explicit type discriminator) so they
// can be embedded in P6 session JSON. Applied in sequence by
// TransformEngine, which chains them and peak-normalises the final result.

import Foundation

// MARK: - StutterRate

/// Tempo-synced stutter retrigger fractions (quarter-note down to
/// thirty-second).
public enum StutterRate: String, Codable, CaseIterable, Sendable {
    case r1_4, r1_8, r1_16, r1_32

    /// Beat duration: r1_4 = 1.0 beat, r1_8 = 0.5, r1_16 = 0.25,
    /// r1_32 = 0.125.
    public var beats: Double {
        switch self {
        case .r1_4:  return 1.0
        case .r1_8:  return 0.5
        case .r1_16: return 0.25
        case .r1_32: return 0.125
        }
    }
}

// MARK: - PadTransform

/// One DSP transform that can be applied to a pad sample. Codable via
/// explicit type-discriminator encoding (frozen wire shape for session
/// JSON). Unknown types on decode throw.
public enum PadTransform: Equatable, Sendable {
    case reverse
    case stutter(StutterRate)
    case granular(GranularParams)
    case stretch(Double)
    case octave(Int)
    case harmony
    case choir
    case gate(steps: [Bool])
    case loop
    case spectralFreeze(atSec: Double, seed: UInt64)
}

// MARK: - Codable (frozen wire shape)

extension PadTransform: Codable {
    private enum CodingKeys: String, CodingKey {
        case type, rate, params, factor, octaves, steps, atSec, seed
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let type = try c.decode(String.self, forKey: .type)
        switch type {
        case "reverse":
            self = .reverse
        case "stutter":
            let rate = try c.decode(StutterRate.self, forKey: .rate)
            self = .stutter(rate)
        case "granular":
            let params = try c.decode(GranularParams.self, forKey: .params)
            self = .granular(params)
        case "stretch":
            let factor = try c.decode(Double.self, forKey: .factor)
            self = .stretch(factor)
        case "octave":
            let octaves = try c.decode(Int.self, forKey: .octaves)
            self = .octave(octaves)
        case "harmony":
            self = .harmony
        case "choir":
            self = .choir
        case "gate":
            let steps = try c.decode([Bool].self, forKey: .steps)
            self = .gate(steps: steps)
        case "loop":
            self = .loop
        case "spectralFreeze":
            let atSec = try c.decode(Double.self, forKey: .atSec)
            let seed = try c.decode(UInt64.self, forKey: .seed)
            self = .spectralFreeze(atSec: atSec, seed: seed)
        default:
            throw DecodingError.dataCorruptedError(
                forKey: .type, in: c,
                debugDescription: "Unknown PadTransform type '\(type)'"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .reverse:
            try c.encode("reverse", forKey: .type)
        case .stutter(let rate):
            try c.encode("stutter", forKey: .type)
            try c.encode(rate, forKey: .rate)
        case .granular(let params):
            try c.encode("granular", forKey: .type)
            try c.encode(params, forKey: .params)
        case .stretch(let factor):
            try c.encode("stretch", forKey: .type)
            try c.encode(factor, forKey: .factor)
        case .octave(let octaves):
            try c.encode("octave", forKey: .type)
            try c.encode(octaves, forKey: .octaves)
        case .harmony:
            try c.encode("harmony", forKey: .type)
        case .choir:
            try c.encode("choir", forKey: .type)
        case .gate(let steps):
            try c.encode("gate", forKey: .type)
            try c.encode(steps, forKey: .steps)
        case .loop:
            try c.encode("loop", forKey: .type)
        case .spectralFreeze(let atSec, let seed):
            try c.encode("spectralFreeze", forKey: .type)
            try c.encode(atSec, forKey: .atSec)
            try c.encode(seed, forKey: .seed)
        }
    }
}

// MARK: - TransformTiming

/// Timing context for tempo-synced transforms (stutter, gate). When
/// fixedBpm is nil the transform follows the current grid context; when
/// set the transform ignores the playback tempo and uses the fixed value.
public struct TransformTiming: Codable, Equatable, Sendable {
    /// Fixed tempo for stutter/gate (nil = follow grid context).
    public var fixedBpm: Double?

    public init(fixedBpm: Double? = nil) {
        self.fixedBpm = fixedBpm
    }
}
