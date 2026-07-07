// DiatonicChords.swift
//
// Stacked-thirds diatonic triads for a key (redesign Phase 4). This
// feeds the Jam in Key degree pads (7 pads = 7 diatonic triads with
// roman numerals) and the Chord Pads grid. Qualities are *derived*
// from the scale intervals rather than looked up per mode, so
// harmonic minor gets its major V and diminished vii° for free.

import Foundation

/// One diatonic triad: degree 1..7, spelled symbol ("Dm", "Bb",
/// "Edim" — always re-parseable by ChordParser), and a
/// quality-derived roman numeral ("i", "VI", "ii°", "III+").
public struct DiatonicChord: Sendable, Equatable {
    public let degree: Int
    public let root: PitchClass
    public let quality: ChordQuality
    public let romanNumeral: String
    public let symbol: String

    public init(
        degree: Int,
        root: PitchClass,
        quality: ChordQuality,
        romanNumeral: String,
        symbol: String
    ) {
        self.degree = degree
        self.root = root
        self.quality = quality
        self.romanNumeral = romanNumeral
        self.symbol = symbol
    }
}

public enum DiatonicChords {

    /// The seven diatonic triads of a key, degree order. Triads are
    /// built by stacking scale thirds (degrees i, i+2, i+4), so the
    /// quality at each degree follows the chosen scale variant.
    public static func triads(key: MusicalKey) -> [DiatonicChord] {
        let intervals = ScaleIntervals.intervals(for: key.scale)
        let n = intervals.count
        return (0..<n).map { i in
            let rootOffset = intervals[i]
            // Wrapped scale steps get +12 so interval math stays
            // ascending.
            let third = intervals[(i + 2) % n] + ((i + 2) >= n ? 12 : 0)
            let fifth = intervals[(i + 4) % n] + ((i + 4) >= n ? 12 : 0)
            let quality = triadQuality(
                third: third - rootOffset,
                fifth: fifth - rootOffset
            )
            let rootPC = PitchClass(key.root.rawValue + rootOffset)
            return DiatonicChord(
                degree: i + 1,
                root: rootPC,
                quality: quality,
                romanNumeral: romanNumeral(degree: i + 1, quality: quality),
                symbol: symbol(root: rootPC, quality: quality, key: key)
            )
        }
    }

    // MARK: - Derivation

    private static func triadQuality(third: Int, fifth: Int) -> ChordQuality {
        switch (third, fifth) {
        case (4, 7): return .maj
        case (3, 7): return .min
        case (3, 6): return .dim
        case (4, 8): return .aug
        default:     return .other
        }
    }

    /// Upper-case numerals for major/augmented, lower-case for
    /// minor/diminished, with ° / + markers. No degree accidentals —
    /// the degrees are the scale's own, so "VI" in D minor *is* Bb.
    static func romanNumeral(degree: Int, quality: ChordQuality) -> String {
        let numerals = ["I", "II", "III", "IV", "V", "VI", "VII"]
        guard degree >= 1, degree <= numerals.count else { return "?" }
        let base = numerals[degree - 1]
        switch quality {
        case .min:  return base.lowercased()
        case .dim:  return base.lowercased() + "°"
        case .aug:  return base + "+"
        default:    return base
        }
    }

    /// Chord symbol spelled for the key (flat keys spell flats) and
    /// using suffixes ChordParser round-trips ("m", "dim", "aug").
    static func symbol(
        root: PitchClass,
        quality: ChordQuality,
        key: MusicalKey
    ) -> String {
        let name = NoteNames.name(pitchClass: root.rawValue, key: key)
        switch quality {
        case .min:  return name + "m"
        case .dim:  return name + "dim"
        case .aug:  return name + "aug"
        default:    return name
        }
    }
}
