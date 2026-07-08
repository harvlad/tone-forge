// JamInKeyLayoutTests.swift
//
// Pins the Jam in Key grid layout (redesign Phase 7): the full 8×8
// is the OpenJamGrid note surface, octave shift moves MIDI in whole
// octaves (clamped ±3), chord pitch classes render bright, and
// degree labels come through for in-key pads.

import XCTest
@testable import ToneForgeEngine

final class JamInKeyLayoutTests: XCTestCase {

    private let dMinor = MusicalKey.parse("D minor")

    // MARK: - Meaning

    func testBottomLeftIsE2Note() {
        let layout = JamInKeyLayout(key: dMinor)
        guard case .note(let midi, let pc, _) = layout.meaning(
            at: PadIndex.at(row: 1, col: 1)
        ) else {
            return XCTFail("expected note meaning")
        }
        XCTAssertEqual(midi, 40)
        XCTAssertEqual(pc, 4)
    }

    func testWholeGridIsNotes() {
        // No sample rows in jam mode — every valid pad is a note.
        let layout = JamInKeyLayout(key: dMinor)
        for row in 1...8 {
            for col in 1...8 {
                guard case .note = layout.meaning(
                    at: PadIndex.at(row: row, col: col)
                ) else {
                    return XCTFail("pad \(row)\(col) is not a note")
                }
            }
        }
    }

    func testOctaveShiftMovesMidiNotPitchClass() {
        let layout = JamInKeyLayout(key: dMinor, octaveShift: 2)
        guard case .note(let midi, let pc, _) = layout.meaning(
            at: PadIndex.at(row: 1, col: 1)
        ) else {
            return XCTFail("expected note meaning")
        }
        XCTAssertEqual(midi, 40 + 24)
        XCTAssertEqual(pc, 4, "pitch class ignores octave shift")
    }

    func testOctaveShiftClampsToPlusMinusThree() {
        XCTAssertEqual(JamInKeyLayout(key: dMinor, octaveShift: 9).octaveShift, 3)
        XCTAssertEqual(JamInKeyLayout(key: dMinor, octaveShift: -9).octaveShift, -3)
    }

    func testInvalidPadIsNone() {
        let layout = JamInKeyLayout(key: dMinor)
        guard case .none = layout.meaning(at: PadIndex(0)) else {
            return XCTFail("invalid pad should be .none")
        }
    }

    // MARK: - Visuals

    func testChordToneRendersBright() {
        // D = MIDI 50 = (row 3, col 1): 40 + 2*5. Pitch class 2.
        let layout = JamInKeyLayout(key: dMinor, chordPitchClasses: [2])
        let visual = layout.visual(at: PadIndex.at(row: 3, col: 1))
        XCTAssertTrue(visual.isBright)
        let other = layout.visual(at: PadIndex.at(row: 1, col: 1))
        XCTAssertFalse(other.isBright)
    }

    func testRootPadCarriesTonicNumeral() {
        // D = MIDI 50 = (row 3, col 1); the tonic renders as scale
        // degree 1 ("i" in minor).
        let layout = JamInKeyLayout(key: dMinor)
        XCTAssertEqual(layout.visual(at: PadIndex.at(row: 3, col: 1)).label, "i")
    }

    func testOutOfKeyPadHasNoLabel() {
        // Eb (pc 3) is chromatic in D natural minor. Eb = MIDI 51 =
        // (row 3, col 2).
        let layout = JamInKeyLayout(key: dMinor)
        XCTAssertNil(layout.visual(at: PadIndex.at(row: 3, col: 2)).label)
    }

    func testNoKeyStillPlaysChromatically() {
        let layout = JamInKeyLayout(key: nil)
        guard case .note(let midi, _, let label) = layout.meaning(
            at: PadIndex.at(row: 1, col: 1)
        ) else {
            return XCTFail("expected note meaning")
        }
        XCTAssertEqual(midi, 40)
        XCTAssertNil(label)
    }
}
