// SongGridTests.swift
//
// Encounter-order assignment must land the first parsed chord on pad
// 11 (bottom-left), the eighth on 18, the ninth on 21, and so on.
// Duplicate canonical chords collapse; unparseable chords are dropped;
// overflow starts at chord 65.

import XCTest
@testable import ToneForgeEngine

final class SongGridTests: XCTestCase {

    func testFirstChordLandsOnPad11() {
        let grid = SongGridBuilder.build(symbols: ["C"])
        let a = grid.assignments[PadIndex.at(row: 1, col: 1)]
        XCTAssertEqual(a?.symbol, "C")
        XCTAssertEqual(a?.parsed.family, .major)
    }

    func testEighthChordLandsOnPad18() {
        let syms = ["C", "D", "E", "F", "G", "A", "B", "Cm"]
        let grid = SongGridBuilder.build(symbols: syms)
        XCTAssertEqual(grid.assignments[PadIndex.at(row: 1, col: 8)]?.symbol, "Cm")
    }

    func testNinthChordWrapsToPad21() {
        var syms = ["C", "D", "E", "F", "G", "A", "B", "Cm"]
        syms.append("Dm")
        let grid = SongGridBuilder.build(symbols: syms)
        XCTAssertEqual(grid.assignments[PadIndex.at(row: 2, col: 1)]?.symbol, "Dm")
    }

    func testDuplicateCanonicalCollapsesToFirstPad() {
        // Second "C" is a duplicate canonical key → dropped.
        let grid = SongGridBuilder.build(symbols: ["C", "C", "Am"])
        XCTAssertEqual(grid.assignments[PadIndex.at(row: 1, col: 1)]?.symbol, "C")
        XCTAssertEqual(grid.assignments[PadIndex.at(row: 1, col: 2)]?.symbol, "Am")
        // Only two assignments total.
        XCTAssertEqual(grid.assignments.count, 2)
    }

    func testEnharmonicsDuplicateCollapse() {
        // C# and Db share a canonical key → only the first fills a pad.
        let grid = SongGridBuilder.build(symbols: ["C#m", "Dbm"])
        XCTAssertEqual(grid.assignments.count, 1)
        XCTAssertEqual(grid.assignments[PadIndex.at(row: 1, col: 1)]?.symbol, "C#m")
    }

    func testUnparseableDropped() {
        let grid = SongGridBuilder.build(symbols: ["Xm", "C"])
        // Xm dropped; C lands on pad 11.
        XCTAssertEqual(grid.assignments.count, 1)
        XCTAssertEqual(grid.assignments[PadIndex.at(row: 1, col: 1)]?.symbol, "C")
    }

    func testOverflowStartsAtChord65() {
        // Generate 66 distinct chord symbols. Use roots × qualities to
        // guarantee uniqueness of canonical key.
        let roots = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        let qualities = ["", "m", "7", "dim", "aug", "maj7"]
        var syms: [String] = []
        for q in qualities {
            for r in roots {
                syms.append("\(r)\(q)")
                if syms.count >= 66 { break }
            }
            if syms.count >= 66 { break }
        }
        XCTAssertEqual(syms.count, 66)

        let grid = SongGridBuilder.build(symbols: syms)
        XCTAssertEqual(grid.assignments.count, 64)
        XCTAssertEqual(grid.overflow.count, 2)
        XCTAssertEqual(grid.overflow.first, syms[64])
        XCTAssertEqual(grid.overflow.last, syms[65])
    }

    // MARK: - byCanonicalKey lookup

    func testByCanonicalKeyLookup() {
        let grid = SongGridBuilder.build(symbols: ["C", "Am7"])
        XCTAssertNotNil(grid.byCanonicalKey["0:maj"])
        XCTAssertNotNil(grid.byCanonicalKey["9:min7"])
    }

    // MARK: - meaning()

    func testMeaningForAssignedPad() {
        let grid = SongGridBuilder.build(symbols: ["Cm7"])
        let meaning = grid.meaning(for: PadIndex.at(row: 1, col: 1))
        if case .chord(let symbol, let family, _) = meaning {
            XCTAssertEqual(symbol, "Cm7")
            XCTAssertEqual(family, .minor)
        } else {
            XCTFail("Expected .chord meaning, got \(meaning)")
        }
    }

    func testMeaningForUnassignedPadIsNone() {
        let grid = SongGridBuilder.build(symbols: ["C"])
        XCTAssertEqual(grid.meaning(for: PadIndex.at(row: 8, col: 8)), .none)
    }

    // MARK: - color

    func testAssignmentColorMatchesFamily() {
        let grid = SongGridBuilder.build(symbols: ["Cm"])
        let a = grid.assignments[PadIndex.at(row: 1, col: 1)]!
        XCTAssertEqual(a.color, Palette.songFamily(.minor))
    }
}
