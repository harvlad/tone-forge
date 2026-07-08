// RomanNumeral.swift
//
// Chord symbol → roman-numeral label within a key (D-022 Learn
// redesign). Complements DiatonicChords (which goes key → triads):
// this direction answers "the analysis says Am and the key is
// C major — what function is that?" for the Learn chord cards.
//
// Only chords whose ROOT sits on a scale degree get a label;
// chromatic roots return nil (the card just shows the symbol).
// Seventh qualities keep their suffix ("V7", "ii7", "IVmaj7") so
// the label round-trips the sound, not just the triad.

import Foundation

public enum RomanNumeral {

    /// Roman-numeral label for `symbol` in `key`, or nil when the
    /// key is unknown, the symbol doesn't parse, or the root is
    /// chromatic (not a scale degree).
    public static func label(symbol: String, key: MusicalKey?) -> String? {
        guard
            let key,
            let parsed = ChordParser.parse(symbol)
        else { return nil }

        let offset = ((parsed.root.rawValue - key.root.rawValue) % 12 + 12) % 12
        let intervals = ScaleIntervals.intervals(for: key.scale)
        guard let index = intervals.firstIndex(of: offset) else { return nil }
        let degree = index + 1

        let numerals = ["I", "II", "III", "IV", "V", "VI", "VII"]
        guard degree <= numerals.count else { return nil }
        let base = numerals[degree - 1]

        switch parsed.quality {
        case .maj:   return base
        case .min:   return base.lowercased()
        case .dim:   return base.lowercased() + "°"
        case .aug:   return base + "+"
        case .dom7:  return base + "7"
        case .min7:  return base.lowercased() + "7"
        case .maj7:  return base + "maj7"
        case .sus:   return base + "sus"
        case .other: return nil
        }
    }
}
