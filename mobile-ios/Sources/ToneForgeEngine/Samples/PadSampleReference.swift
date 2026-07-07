// PadSampleReference.swift
//
// What a grid pad points at: either a pad inside a sample pack (the
// v1 world) or a locally-created sample in PadSampleStore (mic /
// vocoded / baked). Engine-side because SessionCapture (P6) embeds
// these references in session JSON — the wire shape is FROZEN.
//
// PadSlot is the per-pad assignment record. P3 ships it as just the
// reference; P4 extends it with a transform chain + timing via
// decodeIfPresent, so P3-written blobs stay readable.

import Foundation

public enum PadSampleReference: Codable, Hashable, Sendable {
    /// A pad in a sample pack (bundled, song-derived, curated).
    case packPad(packId: String, padIdx: Int)
    /// A locally-created sample (PadSampleStore payload).
    case localSample(id: UUID)

    // MARK: - Codable (frozen wire shape)

    private enum CodingKeys: String, CodingKey {
        case type, packId, padIdx, id
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let type = try c.decode(String.self, forKey: .type)
        switch type {
        case "packPad":
            self = .packPad(
                packId: try c.decode(String.self, forKey: .packId),
                padIdx: try c.decode(Int.self, forKey: .padIdx)
            )
        case "localSample":
            self = .localSample(id: try c.decode(UUID.self, forKey: .id))
        default:
            throw DecodingError.dataCorruptedError(
                forKey: .type, in: c,
                debugDescription: "Unknown PadSampleReference type '\(type)'"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .packPad(let packId, let padIdx):
            try c.encode("packPad", forKey: .type)
            try c.encode(packId, forKey: .packId)
            try c.encode(padIdx, forKey: .padIdx)
        case .localSample(let id):
            try c.encode("localSample", forKey: .type)
            try c.encode(id, forKey: .id)
        }
    }
}

/// One pad's assignment. Holds the reference, transform chain, and
/// timing context. P3-era blobs (only `ref`) decode with empty
/// transforms and default timing.
public struct PadSlot: Codable, Equatable, Sendable {
    public var ref: PadSampleReference
    public var transforms: [PadTransform]
    public var timing: TransformTiming

    public init(
        ref: PadSampleReference,
        transforms: [PadTransform] = [],
        timing: TransformTiming = TransformTiming()
    ) {
        self.ref = ref
        self.transforms = transforms
        self.timing = timing
    }

    // MARK: - Codable (P3 back-compat via decodeIfPresent)

    private enum CodingKeys: String, CodingKey {
        case ref, transforms, timing
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.ref = try c.decode(PadSampleReference.self, forKey: .ref)
        self.transforms = try c.decodeIfPresent(
            [PadTransform].self, forKey: .transforms
        ) ?? []
        self.timing = try c.decodeIfPresent(
            TransformTiming.self, forKey: .timing
        ) ?? TransformTiming()
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(ref, forKey: .ref)
        try c.encode(transforms, forKey: .transforms)
        try c.encode(timing, forKey: .timing)
    }
}
