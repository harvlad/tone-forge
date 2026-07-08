// ChordPadGridTests.swift
//
// The 4×4 Chord Pads grid content (Phase 12): rows 1–3 = diatonic
// triads (degrees 1–7 then 1–5 an octave up), row 4 = sevenths on
// degrees 1, 4, 6, 7 with scale-derived qualities. Every symbol must
// be voicable (ChordVoicing.midiNotes non-empty) — a chord pad that
// makes no sound is a bug.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

final class ChordPadGridTests: XCTestCase {

    private let dMinor = MusicalKey(root: PitchClass(2), scale: .minor)
    private let cMajor = MusicalKey(root: PitchClass(0), scale: .major)

    func testSixteenCellsInDisplayOrder() {
        let cells = ChordPadGrid.cells(key: dMinor)
        XCTAssertEqual(cells.count, 16)
        XCTAssertEqual(cells.map(\.index), Array(0..<16))
    }

    func testDMinorTriadRows() {
        let cells = ChordPadGrid.cells(key: dMinor)
        // Degrees 1–7 (mockup row: Dm i / Edim ii° / F III / Gm iv /
        // Am v / Bb VI / C VII), octave 0.
        XCTAssertEqual(
            cells[0..<7].map(\.symbol),
            ["Dm", "Edim", "F", "Gm", "Am", "Bb", "C"]
        )
        XCTAssertEqual(
            cells[0..<7].map(\.detail),
            ["i", "ii°", "III", "iv", "v", "VI", "VII"]
        )
        XCTAssertTrue(cells[0..<7].allSatisfy { $0.octaveShift == 0 })
        // Degrees 1–5 again, an octave up.
        XCTAssertEqual(
            cells[7..<12].map(\.symbol),
            ["Dm", "Edim", "F", "Gm", "Am"]
        )
        XCTAssertTrue(cells[7..<12].allSatisfy { $0.octaveShift == 1 })
    }

    func testDMinorSeventhRow() {
        let cells = ChordPadGrid.cells(key: dMinor)
        // Natural minor: i7 min7, iv7 min7, VI maj7, VII dominant.
        XCTAssertEqual(
            cells[12..<16].map(\.symbol),
            ["Dm7", "Gm7", "Bbmaj7", "C7"]
        )
        XCTAssertEqual(
            cells[12..<16].map(\.detail),
            ["i7", "iv7", "VI7", "VII7"]
        )
    }

    func testCMajorSeventhRowFallsBackForDiminished() {
        let cells = ChordPadGrid.cells(key: cMajor)
        // I maj7, IV maj7, vi min7 — and vii° has no parseable
        // seventh spelling, so the pad falls back to the triad.
        XCTAssertEqual(
            cells[12..<16].map(\.symbol),
            ["Cmaj7", "Fmaj7", "Am7", "Bdim"]
        )
        XCTAssertEqual(cells[15].detail, "vii°")
    }

    func testEveryCellIsVoicable() {
        for key in [dMinor, cMajor,
                    MusicalKey(root: PitchClass(2), scale: .harmonicMinor)] {
            for cell in ChordPadGrid.cells(key: key) {
                XCTAssertFalse(
                    ChordVoicing.midiNotes(symbol: cell.symbol).isEmpty,
                    "cell \(cell.index) (\(cell.symbol)) in \(key) is unvoicable"
                )
            }
        }
    }

    func testCellIndexMapsOverlayToDisplayOrder() {
        // Overlay row 1 = bottom → display row-major from top-left.
        XCTAssertEqual(ChordPadsView.cellIndex(row: 4, col: 1), 0)
        XCTAssertEqual(ChordPadsView.cellIndex(row: 4, col: 4), 3)
        XCTAssertEqual(ChordPadsView.cellIndex(row: 1, col: 1), 12)
        XCTAssertEqual(ChordPadsView.cellIndex(row: 1, col: 4), 15)
        // Full coverage of 0…15, each exactly once.
        var seen = Set<Int>()
        for row in 1...4 {
            for col in 1...4 {
                seen.insert(ChordPadsView.cellIndex(row: row, col: col))
            }
        }
        XCTAssertEqual(seen, Set(0..<16))
    }
}
