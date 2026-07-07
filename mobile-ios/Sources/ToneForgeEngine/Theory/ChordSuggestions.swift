// ChordSuggestions.swift
//
// "Suggested next chord" chips for Jam in Key (redesign Phase 4).
// A small diatonic-function table — not a Markov model — because the
// mockup shows exactly two suggestions and beginners want *safe*
// moves, not novelty. Degrees are looked up in the key's own triads
// so the returned chords carry ready-to-play symbols and numerals.

import Foundation

public enum ChordSuggestions {

    /// Exactly two diatonic follow-up chords for the chord currently
    /// sounding. The current chord is located by root pitch class
    /// among the key's diatonic triads; non-diatonic roots fall back
    /// to tonic + dominant-degree suggestions so the chips never go
    /// blank mid-song.
    public static func suggestions(
        after symbol: String,
        in key: MusicalKey
    ) -> [DiatonicChord] {
        let triads = DiatonicChords.triads(key: key)
        guard triads.count == 7 else { return [] }

        let degree = ChordParser.parse(symbol).flatMap { parsed in
            triads.first { $0.root == parsed.root }?.degree
        }

        let targets = follow(degree: degree, minorish: isMinorish(triads))
        return targets.map { triads[$0 - 1] }
    }

    // MARK: - Function table

    /// Minor-flavoured keys resolve differently (i → VII, VI — the
    /// mockup's Dm → C, Bb) than major ones (I → IV, V). Flavour is
    /// read off the tonic triad so modes sort themselves: dorian/
    /// phrygian/locrian take the minor table, lydian/mixolydian the
    /// major one.
    private static func isMinorish(_ triads: [DiatonicChord]) -> Bool {
        triads[0].quality == .min || triads[0].quality == .dim
    }

    /// Degree → two follow-up degrees. `nil` (chord outside the key)
    /// → tonic + the table's dominant-function degree.
    private static func follow(degree: Int?, minorish: Bool) -> [Int] {
        if minorish {
            switch degree {
            case 1:  return [7, 6]
            case 2:  return [5, 1]
            case 3:  return [6, 7]
            case 4:  return [1, 5]
            case 5:  return [1, 6]
            case 6:  return [3, 7]
            case 7:  return [1, 3]
            default: return [1, 7]
            }
        }
        switch degree {
        case 1:  return [4, 5]
        case 2:  return [5, 1]
        case 3:  return [6, 4]
        case 4:  return [5, 1]
        case 5:  return [1, 6]
        case 6:  return [2, 4]
        case 7:  return [1, 3]
        default: return [1, 5]
        }
    }
}
