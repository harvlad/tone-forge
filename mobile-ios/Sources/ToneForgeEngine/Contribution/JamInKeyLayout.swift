// JamInKeyLayout.swift
//
// Grid layout for Jam in Key mode (redesign Phase 7): the whole 8×8
// grid is the OpenJamGrid chromatic note surface — no sample rows.
// The key can differ from the song's detected key (user override /
// harmonic-minor variant), the currently sounding chord's pitch
// classes render bright (teal), and the entire surface can be shifted
// by whole octaves.
//
// Like every layout this is an immutable snapshot: ModeCoordinator
// rebuilds it when the key override, scale variant, chord, octave
// shift, or highlight toggle changes.

import Foundation

public struct JamInKeyLayout: GridLayoutProviding {
    public let key: MusicalKey?
    /// Pitch classes of the currently sounding chord (bright pads).
    /// Empty when highlighting is disabled or no chord is active.
    public let chordPitchClasses: Set<Int>
    /// Whole-octave shift applied to every pad's MIDI note. Clamped
    /// to −3…+3 so the surface (base MIDI 40–82) stays in 4…118.
    public let octaveShift: Int

    private let jamGrid: OpenJamGrid

    public init(
        key: MusicalKey?,
        chordPitchClasses: Set<Int> = [],
        octaveShift: Int = 0
    ) {
        self.key = key
        self.chordPitchClasses = chordPitchClasses
        self.octaveShift = max(-3, min(3, octaveShift))
        self.jamGrid = OpenJamGrid(
            key: key,
            chordPitchClasses: chordPitchClasses,
            outOfKeyMode: .dim
        )
    }

    public func meaning(at pad: PadIndex) -> PadMeaning {
        guard pad.isValid else { return .none }
        guard case .note(let midi, let pc, let degreeLabel) = jamGrid.meaning(for: pad) else {
            return .none
        }
        return .note(midi: midi + octaveShift * 12, pitchClass: pc, degreeLabel: degreeLabel)
    }

    public func visual(at pad: PadIndex) -> PadVisual {
        guard pad.isValid else { return .off }
        let color = jamGrid.color(for: pad)
        let pc = OpenJamGrid.pitchClass(for: pad)
        let isChordTone = pc.map { chordPitchClasses.contains($0) } ?? false
        var label: String? = nil
        if case .note(_, _, let degreeLabel) = jamGrid.meaning(for: pad) {
            label = degreeLabel
        }
        return PadVisual(
            colorHint: HybridModeLayout.hint(color),
            label: label,
            isBright: isChordTone
        )
    }
}
