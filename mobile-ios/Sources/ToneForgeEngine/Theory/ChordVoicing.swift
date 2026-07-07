// ChordVoicing.swift
//
// Chord symbol → playable notes (redesign Phase 4). Three layers:
//
//   - chordTones(for:): semitone offsets from the root, ascending,
//     per ChordQuality bucket. This is the one voicing table the
//     Jam degree pads, Chord Pads and Learn practice pads all share.
//   - pitchClassSet(symbol:): the offsets folded onto pitch classes —
//     the grid-highlight shape. Richer than the old
//     ModeCoordinator.pitchClasses table for maj7/min7 (adds the 7th)
//     and sus (4th instead of 3rd); identical for maj/min/dom7/dim/
//     aug/other, which is what the launchpad LED tests pin.
//   - midiNotes(symbol:octaveShift:baseMidi:): concrete MIDI notes
//     for PadSynth, root anchored at/above baseMidi (C3 = 48 by
//     default, comfortably mid-keyboard for the pad voice).

import Foundation

public enum ChordVoicing {

    /// Semitone offsets from the root, root first, ascending.
    public static func chordTones(for chord: ParsedChord) -> [Int] {
        switch chord.quality {
        case .maj:   return [0, 4, 7]
        case .min:   return [0, 3, 7]
        case .maj7:  return [0, 4, 7, 11]
        case .min7:  return [0, 3, 7, 10]
        case .dom7:  return [0, 4, 7, 10]
        case .sus:   return [0, 5, 7]
        case .dim:   return [0, 3, 6]
        case .aug:   return [0, 4, 8]
        case .other: return [0]
        }
    }

    /// Pitch classes 0..11 for a chord symbol. Empty for unparseable
    /// symbols (mirrors the old ModeCoordinator.pitchClasses
    /// contract).
    public static func pitchClassSet(symbol: String) -> Set<Int> {
        guard let parsed = ChordParser.parse(symbol) else { return [] }
        return Set(chordTones(for: parsed).map {
            (parsed.root.rawValue + $0) % 12
        })
    }

    /// MIDI notes for a chord symbol, low to high. The root lands on
    /// the first occurrence of its pitch class at or above
    /// `baseMidi`, then `octaveShift` moves the whole voicing in
    /// whole octaves. Empty for unparseable symbols.
    public static func midiNotes(
        symbol: String,
        octaveShift: Int = 0,
        baseMidi: Int = 48
    ) -> [Int] {
        guard let parsed = ChordParser.parse(symbol) else { return [] }
        let basePC = ((baseMidi % 12) + 12) % 12
        let rootMidi = baseMidi
            + ((parsed.root.rawValue - basePC + 12) % 12)
            + octaveShift * 12
        return chordTones(for: parsed).map { rootMidi + $0 }
    }
}
