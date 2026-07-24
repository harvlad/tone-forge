// OpenJamGridTests.swift
//
// Locks the port of `_midiForPad`, `_openJamColor`, and the scale
// degree walker to the JS behaviour. Because the open-jam layout is
// numerical (MIDI 40 at pad 11, +5/row, +1/col), the MIDI arithmetic
// tests double as a check that the row/col convention hasn't been
// flipped by accident.

import XCTest
@testable import ToneForgeEngine

final class OpenJamGridTests: XCTestCase {

    // MARK: - MIDI arithmetic

    func testBottomLeftIsE2() {
        // Pad 11 = row 1 col 1 = E2 = MIDI 40.
        XCTAssertEqual(OpenJamGrid.midi(row: 1, col: 1), 40)
    }

    func testColumnAddsSemitone() {
        // (row 1, col 2) = MIDI 41 (F2).
        XCTAssertEqual(OpenJamGrid.midi(row: 1, col: 2), 41)
    }

    func testRowAddsFifth() {
        // Row + 1 = +5 semitones. (row 2, col 1) = MIDI 45 (A2).
        XCTAssertEqual(OpenJamGrid.midi(row: 2, col: 1), 45)
    }

    func testTopRightMidi() {
        // Row 8, col 8 = 40 + 35 + 7 = 82.
        XCTAssertEqual(OpenJamGrid.midi(row: 8, col: 8), 82)
    }

    func testMidiForPadUsesPadIndex() {
        XCTAssertEqual(OpenJamGrid.midi(for: PadIndex.at(row: 1, col: 1)), 40)
        XCTAssertEqual(OpenJamGrid.midi(for: PadIndex.at(row: 8, col: 8)), 82)
    }

    func testMidiForOutOfRangePadIsNil() {
        XCTAssertNil(OpenJamGrid.midi(for: PadIndex(99)))
        XCTAssertNil(OpenJamGrid.midi(for: PadIndex(0)))
    }

    // MARK: - PitchClass

    func testPitchClassAtBottomLeft() {
        // E2 = pitch class 4.
        XCTAssertEqual(OpenJamGrid.pitchClass(for: PadIndex.at(row: 1, col: 1)), 4)
    }

    // MARK: - MusicalKey parsing

    func testParseMajorKey() {
        let k = MusicalKey.parse("C major")
        XCTAssertEqual(k?.root.rawValue, 0)
        XCTAssertEqual(k?.scale, .major)
    }

    func testParseMinorKey() {
        let k = MusicalKey.parse("F# minor")
        XCTAssertEqual(k?.root.rawValue, 6)
        XCTAssertEqual(k?.scale, .minor)
    }

    func testParseAcceptsShortForm() {
        XCTAssertEqual(MusicalKey.parse("A min")?.scale, .minor)
        XCTAssertEqual(MusicalKey.parse("G maj")?.scale, .major)
    }

    func testParseBareRootIsMajor() {
        XCTAssertEqual(MusicalKey.parse("C")?.root.rawValue, 0)
        XCTAssertEqual(MusicalKey.parse("C")?.scale, .major)
        XCTAssertEqual(MusicalKey.parse("F#")?.scale, .major)
        XCTAssertEqual(MusicalKey.parse("F#")?.root.rawValue, 6)
    }

    func testParseNilOnJunk() {
        XCTAssertNil(MusicalKey.parse(nil))
        XCTAssertNil(MusicalKey.parse(""))
        XCTAssertNil(MusicalKey.parse("bogus"))
        XCTAssertNil(MusicalKey.parse("H major"))
    }

    // MARK: - Color priority walker

    func testChordToneBeatsRoot() {
        // Key of C major (root PC 0). Chord contains PC 0.
        // Pad at PC 0 must render as chord tone (teal), not root (gold).
        let key = MusicalKey(root: PitchClass(0), scale: .major)
        let grid = OpenJamGrid(key: key, chordPitchClasses: [0])
        // Find a pad whose PC is 0. row 1 col 9 = out of range; instead
        // walk the grid.
        for row in 1...8 {
            for col in 1...8 {
                let pad = PadIndex.at(row: row, col: col)
                if OpenJamGrid.pitchClass(for: pad) == 0 {
                    XCTAssertEqual(grid.color(for: pad), Palette.openJamChordTone)
                    return
                }
            }
        }
        XCTFail("No pad with PC 0 found — grid arithmetic broken")
    }

    func testRootBeatsScaleDegree() {
        // Empty chord set, key of C major. PC 0 (the root) must render
        // as openJamRoot, not degree I.
        let key = MusicalKey(root: PitchClass(0), scale: .major)
        let grid = OpenJamGrid(key: key)
        for row in 1...8 {
            for col in 1...8 {
                let pad = PadIndex.at(row: row, col: col)
                if OpenJamGrid.pitchClass(for: pad) == 0 {
                    XCTAssertEqual(grid.color(for: pad), Palette.openJamRoot)
                    return
                }
            }
        }
        XCTFail("No pad with PC 0 found")
    }

    func testInKeyPaintsDegreeDimmed() {
        // C major, PC 2 (D) = degree 2.
        let key = MusicalKey(root: PitchClass(0), scale: .major)
        let grid = OpenJamGrid(key: key)
        for row in 1...8 {
            for col in 1...8 {
                let pad = PadIndex.at(row: row, col: col)
                if OpenJamGrid.pitchClass(for: pad) == 2 {
                    XCTAssertEqual(grid.color(for: pad), Palette.openJamDegreeDimmed(degree: 2))
                    return
                }
            }
        }
        XCTFail("No pad with PC 2 found")
    }

    func testOutOfKeyPaintsChromaticDimWhenDim() {
        // C major, PC 1 (C#) = out of key.
        let key = MusicalKey(root: PitchClass(0), scale: .major)
        let grid = OpenJamGrid(key: key, chordPitchClasses: [], outOfKeyMode: .dim)
        for row in 1...8 {
            for col in 1...8 {
                let pad = PadIndex.at(row: row, col: col)
                if OpenJamGrid.pitchClass(for: pad) == 1 {
                    XCTAssertEqual(grid.color(for: pad), Palette.openJamChromaticDim)
                    return
                }
            }
        }
        XCTFail("No pad with PC 1 found")
    }

    func testOutOfKeyPaintsOffWhenOff() {
        let key = MusicalKey(root: PitchClass(0), scale: .major)
        let grid = OpenJamGrid(key: key, chordPitchClasses: [], outOfKeyMode: .off)
        for row in 1...8 {
            for col in 1...8 {
                let pad = PadIndex.at(row: row, col: col)
                if OpenJamGrid.pitchClass(for: pad) == 1 {
                    XCTAssertEqual(grid.color(for: pad), .off)
                    return
                }
            }
        }
        XCTFail("No pad with PC 1 found")
    }

    // MARK: - Meaning + degree labels

    func testMeaningIncludesDegreeLabel() {
        let key = MusicalKey(root: PitchClass(0), scale: .major)
        let grid = OpenJamGrid(key: key)
        // Find a pad at PC 7 (G, degree V).
        for row in 1...8 {
            for col in 1...8 {
                let pad = PadIndex.at(row: row, col: col)
                if OpenJamGrid.pitchClass(for: pad) == 7 {
                    let m = grid.meaning(for: pad)
                    if case .note(_, _, let label) = m {
                        XCTAssertEqual(label, "V")
                        return
                    } else {
                        XCTFail("Expected .note, got \(m)")
                    }
                }
            }
        }
    }

    func testMeaningRootLabel() {
        let key = MusicalKey(root: PitchClass(0), scale: .major)
        let grid = OpenJamGrid(key: key)
        for row in 1...8 {
            for col in 1...8 {
                let pad = PadIndex.at(row: row, col: col)
                if OpenJamGrid.pitchClass(for: pad) == 0 {
                    let m = grid.meaning(for: pad)
                    if case .note(_, _, let label) = m {
                        // Root has degree label "I" (not "R") because
                        // the scale-degree walker matches PC 0 first.
                        XCTAssertEqual(label, "I")
                        return
                    }
                }
            }
        }
    }

    // MARK: - Scale intervals

    func testMajorScaleIntervals() {
        XCTAssertEqual(ScaleIntervals.intervals(for: .major), [0, 2, 4, 5, 7, 9, 11])
    }

    func testMinorScaleIntervals() {
        XCTAssertEqual(ScaleIntervals.intervals(for: .minor), [0, 2, 3, 5, 7, 8, 10])
    }
}
