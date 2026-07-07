// ChordParser.swift
//
// Port of the pure-logic chord-symbol parser from
// backend/static/launchpad.js (lines 46–162). Keeps the same regex,
// the same pitch-class table, and the same quality-classification
// buckets so the mobile app and the web app produce byte-identical
// pad meanings for a given bundle.
//
// This is the boundary that the Swift port promises to keep in lockstep
// with the JS (see DECISIONS.md → D-006). If launchpad.js changes any
// of the maps or regex here, this file has to change with it in the
// same commit.

import Foundation

/// Pitch class 0..11 (C=0, C#/Db=1, …, B=11). Wrapped for type safety
/// so we don't accidentally mix it with MIDI note numbers or scale
/// degrees.
public struct PitchClass: Hashable, Sendable {
    public let rawValue: Int
    public init(_ raw: Int) {
        // Normalize so `PitchClass(-1)` == `PitchClass(11)`. Keeps
        // callers from having to think about the modulo boundary.
        self.rawValue = ((raw % 12) + 12) % 12
    }
}

/// Coarse quality bucket the launchpad.js `_classifyQuality` walker
/// produces. Distinct from ``ChordFamily`` — this is a finer split
/// (min vs min7, maj vs maj7, …) that some layout strategies use
/// before collapsing to a family.
public enum ChordQuality: String, Sendable, Equatable, CaseIterable {
    case maj
    case min
    case dom7
    case maj7
    case min7
    case sus
    case dim
    case aug
    case other
}

/// Parsed chord: (root pitch class, quality). Slash chords (e.g.
/// "G/B") come out as ``.other`` — that mirrors the JS behaviour,
/// which lets them through as unrecognised so they don't crash the
/// grid assignment.
public struct ParsedChord: Hashable, Sendable {
    public let root: PitchClass
    public let quality: ChordQuality

    public init(root: PitchClass, quality: ChordQuality) {
        self.root = root
        self.quality = quality
    }

    /// Canonicalised key for dictionary/set lookup: enharmonic roots
    /// collapse (C# and Db both hash to `"1:min"`). Matches the JS
    /// `_canonicalChordKey` result exactly.
    public var canonicalKey: String {
        "\(root.rawValue):\(quality.rawValue)"
    }

    /// Coarse family for palette lookup (see ``Palette``).
    public var family: ChordFamily {
        switch quality {
        case .maj, .maj7, .sus:
            return .major
        case .min, .min7:
            return .minor
        case .dom7:
            return .dom7
        case .dim:
            return .dim
        case .aug:
            return .aug
        case .other:
            return .other
        }
    }
}

/// Static parser. Reads chord-symbol strings ("Cm7", "F#dim", "Bb7sus4",
/// "G/B") and returns a ``ParsedChord`` or ``nil`` if the symbol is
/// empty / null / doesn't start with a note letter.
public enum ChordParser {

    /// Root name → pitch class. Direct port of the `PC` table in
    /// launchpad.js:46-50. Includes all enharmonics so both spellings
    /// of a note collapse to the same PC.
    private static let rootPitchClass: [String: Int] = [
        "C":  0, "B#": 0,
        "C#": 1, "Db": 1,
        "D":  2,
        "D#": 3, "Eb": 3,
        "E":  4, "Fb": 4,
        "F":  5, "E#": 5,
        "F#": 6, "Gb": 6,
        "G":  7,
        "G#": 8, "Ab": 8,
        "A":  9,
        "A#": 10, "Bb": 10,
        "B":  11, "Cb": 11,
    ]

    /// Regex mirrors the JS `/^([A-Ga-g])([#b]?)(.*)$/`. Uses
    /// NSRegularExpression for portability rather than
    /// `Regex<..>` so this file compiles pre-Swift 5.7 too.
    private static let symbolRegex: NSRegularExpression = {
        // swiftlint:disable:next force_try
        try! NSRegularExpression(pattern: "^([A-Ga-g])([#b]?)(.*)$")
    }()

    /// Parse a chord symbol. Returns ``nil`` for empty/whitespace-only
    /// input or symbols that don't start with a note letter.
    public static func parse(_ symbol: String) -> ParsedChord? {
        let trimmed = symbol.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return nil }
        let ns = trimmed as NSString
        let range = NSRange(location: 0, length: ns.length)
        guard let match = symbolRegex.firstMatch(in: trimmed, range: range) else {
            return nil
        }
        // Groups: 1 = letter, 2 = optional accidental, 3 = rest (quality).
        let letter = ns.substring(with: match.range(at: 1)).uppercased()
        let accidental = match.range(at: 2).length > 0
            ? ns.substring(with: match.range(at: 2))
            : ""
        let qualityStr = match.range(at: 3).length > 0
            ? ns.substring(with: match.range(at: 3))
            : ""

        let rootName = letter + accidental
        guard let rootPc = rootPitchClass[rootName] else { return nil }
        return ParsedChord(
            root: PitchClass(rootPc),
            quality: classifyQuality(qualityStr)
        )
    }

    /// Map the raw quality suffix ("m7", "sus4", "maj9", …) to a
    /// coarse bucket. Direct port of `_classifyQuality` in
    /// launchpad.js:121-132. The order of checks matters — the JS
    /// walker uses if/else-if, which biases toward the more specific
    /// match (maj7 before maj, min7 before min).
    public static func classifyQuality(_ raw: String) -> ChordQuality {
        let q = raw.lowercased()

        // Empty suffix / plain major variants.
        if q.isEmpty || q == "maj" || q == "major"
            || q == "add9" || q == "6" || q == "maj6" {
            return .maj
        }
        // Major-family sevenths (maj7 must beat plain maj).
        if q == "maj7" || q == "maj9" || q == "maj13" {
            return .maj7
        }
        // Minor-family sevenths (must beat plain min).
        if q == "m7" || q == "min7" || q == "m9" || q == "min9" || q == "m11" {
            return .min7
        }
        // Plain minor variants.
        if q == "m" || q == "min" || q == "minor" || q == "m6" {
            return .min
        }
        // Suspended.
        if q == "sus2" || q == "sus4" || q == "sus" {
            return .sus
        }
        // Diminished + half-diminished. The "ø" is the unicode
        // half-diminished symbol used by some analyses.
        if q == "dim" || q == "dim7" || q == "m7b5" || q == "ø" {
            return .dim
        }
        // Augmented.
        if q == "aug" || q == "+" || q == "aug7" {
            return .aug
        }
        // Bare number quality → dominant seventh family. Matches the JS
        // `/^(7|9|11|13)$/` test literally.
        if q == "7" || q == "9" || q == "11" || q == "13" || q == "dom7" {
            return .dom7
        }
        return .other
    }
}
