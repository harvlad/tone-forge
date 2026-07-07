// NoteNames.swift
//
// Key-aware note spelling (redesign Phase 4). The Jam/Learn/Chord
// surfaces show note names on pads, and "D#m" in the key of Bb minor
// reads wrong to a musician — flat keys spell flats. The chooser is
// deliberately simple: a key prefers flats when its relative Ionian
// tonic sits on the flat side of the circle of fifths (F, Bb, Eb,
// Ab, Db). Gb/F# major is ambiguous by convention; we side with
// sharps there.

import Foundation

public enum NoteNames {

    static let sharpNames = [
        "C", "C#", "D", "D#", "E", "F",
        "F#", "G", "G#", "A", "A#", "B",
    ]

    static let flatNames = [
        "C", "Db", "D", "Eb", "E", "F",
        "Gb", "G", "Ab", "A", "Bb", "B",
    ]

    /// Spell a pitch class in the context of a key. No key → sharp
    /// spelling (matches the chromatic default everywhere else in
    /// the app).
    public static func name(pitchClass: Int, key: MusicalKey?) -> String {
        let pc = ((pitchClass % 12) + 12) % 12
        let flats = key?.prefersFlats ?? false
        return (flats ? flatNames : sharpNames)[pc]
    }
}

extension MusicalKey {

    /// Whether note names in this key should be spelled with flats.
    /// Computed by mapping the mode's tonic back to its relative
    /// Ionian tonic and checking the flat side of the circle of
    /// fifths. Examples: D minor → F major → flats; B major →
    /// sharps; C mixolydian → F major → flats.
    public var prefersFlats: Bool {
        // Semitone offset from the relative Ionian tonic up to this
        // mode's tonic (major = degree 1 → 0, dorian → 2, …).
        let modeOffset: Int
        switch scale {
        case .major:         modeOffset = 0
        case .dorian:        modeOffset = 2
        case .phrygian:      modeOffset = 4
        case .lydian:        modeOffset = 5
        case .mixolydian:    modeOffset = 7
        case .minor,
             .harmonicMinor,
             .melodicMinor:  modeOffset = 9
        case .locrian:       modeOffset = 11
        }
        let ionianTonic = ((root.rawValue - modeOffset) % 12 + 12) % 12
        // F, Bb, Eb, Ab, Db majors — one through five flats. C (0)
        // and the sharp keys spell sharps; pc 6 (F#/Gb) sides with
        // F# by convention.
        return [5, 10, 3, 8, 1].contains(ionianTonic)
    }
}
