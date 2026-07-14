// ChopReference.swift
//
// Unified reference type for sample sources in the sequencer (D-023).
// A step or timeline clip can point at:
//   - A chop from a bundle preset (harmonic/sections/etc.)
//   - A pad from a sample pack
//   - A local sample recorded via mic
//   - A custom URL (external sample file)
//
// The sequencer player resolves these to audio buffers at playback
// time, reusing the existing SampleScheduler infrastructure.
//
// Wire shape is frozen (Codable) for pattern/arrangement persistence.

import Foundation

/// What a sequencer step or timeline clip triggers.
public enum ChopReference: Codable, Hashable, Sendable {

    // MARK: - Bundle Chops

    /// A chop from a bundle preset.
    /// - Parameters:
    ///   - presetKey: The preset name (e.g., "harmonic", "sections").
    ///   - chopIndex: Index into the preset's chops array.
    ///   - resolvedId: Optional resolved chop ID after edits (for splits).
    case bundleChop(presetKey: String, chopIndex: Int, resolvedId: Int?)

    // MARK: - Sample Pack

    /// A pad from a sample pack.
    /// - Parameters:
    ///   - packId: The pack identifier.
    ///   - padIdx: The pad index within the pack.
    case packPad(packId: String, padIdx: Int)

    // MARK: - Local Sample

    /// A locally recorded sample.
    /// - Parameters:
    ///   - id: The local sample's UUID (from PadSampleStore).
    case localSample(id: UUID)

    // MARK: - External

    /// A custom audio file URL.
    /// - Parameters:
    ///   - url: File URL to the audio file.
    ///   - startSec: Optional start offset within the file.
    ///   - endSec: Optional end offset within the file.
    case customURL(url: URL, startSec: Double?, endSec: Double?)

    // MARK: - Sequence

    /// A saved sequencer pattern (SequencerPatternStore). Used when
    /// assigning a whole sequence to a pad via the browser.
    /// - Parameters:
    ///   - patternId: The saved pattern's UUID.
    case sequence(patternId: UUID)

    // MARK: - Synth Chord

    /// A diatonic/key chord voiced on the pad synth (no sample source).
    /// Lets the sequencer build chord loops without a loaded song.
    /// - Parameters:
    ///   - symbol: Chord symbol (e.g., "Dm", "C", "Gmaj7").
    ///   - octaveShift: Whole-octave transpose applied at voicing time,
    ///     −3…+3 (matches Jam). 0 keeps the default register and the
    ///     frozen wire shape.
    case synthChord(symbol: String, octaveShift: Int)

    // MARK: - Convenience

    /// Create a reference to a bundle chop.
    public static func chop(
        preset: String,
        index: Int,
        resolvedId: Int? = nil
    ) -> ChopReference {
        .bundleChop(presetKey: preset, chopIndex: index, resolvedId: resolvedId)
    }

    /// Create a reference to a pack pad (mirrors PadSampleReference.packPad).
    public static func pad(packId: String, padIdx: Int) -> ChopReference {
        .packPad(packId: packId, padIdx: padIdx)
    }

    /// Create a reference to a local sample (mirrors PadSampleReference.localSample).
    public static func local(id: UUID) -> ChopReference {
        .localSample(id: id)
    }

    /// Create a reference to a custom audio file.
    public static func file(
        url: URL,
        start: Double? = nil,
        end: Double? = nil
    ) -> ChopReference {
        .customURL(url: url, startSec: start, endSec: end)
    }

    // MARK: - Properties

    /// A display label for the reference (for UI).
    public var displayLabel: String {
        switch self {
        case .bundleChop(let preset, let index, _):
            return "\(preset.capitalized) #\(index + 1)"
        case .packPad(let packId, let padIdx):
            let row = padIdx / 10
            let col = padIdx % 10
            return "\(packId) R\(row)C\(col)"
        case .localSample:
            return "Local"
        case .customURL(let url, _, _):
            return url.lastPathComponent
        case .sequence:
            return "Sequence"
        case .synthChord(let symbol, _):
            return symbol
        }
    }

    /// Whether this reference points to a bundle chop.
    public var isBundleChop: Bool {
        if case .bundleChop = self { return true }
        return false
    }

    /// Whether this reference points to a pack pad.
    public var isPackPad: Bool {
        if case .packPad = self { return true }
        return false
    }

    /// Whether this reference points to a local sample.
    public var isLocalSample: Bool {
        if case .localSample = self { return true }
        return false
    }
}

// MARK: - Codable (frozen wire shape)

extension ChopReference {
    private enum CodingKeys: String, CodingKey {
        case type
        case presetKey, chopIndex, resolvedId
        case packId, padIdx
        case id
        case url, startSec, endSec
        case patternId
        case chordSymbol, chordOctave
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let type = try c.decode(String.self, forKey: .type)

        switch type {
        case "bundleChop":
            self = .bundleChop(
                presetKey: try c.decode(String.self, forKey: .presetKey),
                chopIndex: try c.decode(Int.self, forKey: .chopIndex),
                resolvedId: try c.decodeIfPresent(Int.self, forKey: .resolvedId)
            )
        case "packPad":
            self = .packPad(
                packId: try c.decode(String.self, forKey: .packId),
                padIdx: try c.decode(Int.self, forKey: .padIdx)
            )
        case "localSample":
            self = .localSample(
                id: try c.decode(UUID.self, forKey: .id)
            )
        case "customURL":
            self = .customURL(
                url: try c.decode(URL.self, forKey: .url),
                startSec: try c.decodeIfPresent(Double.self, forKey: .startSec),
                endSec: try c.decodeIfPresent(Double.self, forKey: .endSec)
            )
        case "sequence":
            self = .sequence(patternId: try c.decode(UUID.self, forKey: .patternId))
        case "synthChord":
            self = .synthChord(
                symbol: try c.decode(String.self, forKey: .chordSymbol),
                octaveShift: try c.decodeIfPresent(Int.self, forKey: .chordOctave) ?? 0
            )
        default:
            throw DecodingError.dataCorruptedError(
                forKey: .type, in: c,
                debugDescription: "Unknown ChopReference type '\(type)'"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)

        switch self {
        case .bundleChop(let presetKey, let chopIndex, let resolvedId):
            try c.encode("bundleChop", forKey: .type)
            try c.encode(presetKey, forKey: .presetKey)
            try c.encode(chopIndex, forKey: .chopIndex)
            try c.encodeIfPresent(resolvedId, forKey: .resolvedId)

        case .packPad(let packId, let padIdx):
            try c.encode("packPad", forKey: .type)
            try c.encode(packId, forKey: .packId)
            try c.encode(padIdx, forKey: .padIdx)

        case .localSample(let id):
            try c.encode("localSample", forKey: .type)
            try c.encode(id, forKey: .id)

        case .customURL(let url, let startSec, let endSec):
            try c.encode("customURL", forKey: .type)
            try c.encode(url, forKey: .url)
            try c.encodeIfPresent(startSec, forKey: .startSec)
            try c.encodeIfPresent(endSec, forKey: .endSec)

        case .sequence(let patternId):
            try c.encode("sequence", forKey: .type)
            try c.encode(patternId, forKey: .patternId)

        case .synthChord(let symbol, let octaveShift):
            try c.encode("synthChord", forKey: .type)
            try c.encode(symbol, forKey: .chordSymbol)
            // Emit octave only when shifted so octave-0 keeps the frozen
            // {"chordSymbol":…,"type":"synthChord"} wire shape.
            if octaveShift != 0 {
                try c.encode(octaveShift, forKey: .chordOctave)
            }
        }
    }
}

// MARK: - Equatable Details

extension ChopReference {
    /// Compare ignoring resolvedId (useful for matching before/after edits).
    public func matches(ignoreResolved other: ChopReference) -> Bool {
        switch (self, other) {
        case let (.bundleChop(p1, c1, _), .bundleChop(p2, c2, _)):
            return p1 == p2 && c1 == c2
        default:
            return self == other
        }
    }
}
