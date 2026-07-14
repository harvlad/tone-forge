// ChordRibbonModelTests.swift
//
// Binary-search playhead lookups: exact boundaries (start inclusive,
// end exclusive), gaps between chords, out-of-range, upcoming window
// across a gap, and section resolution.

import XCTest
@testable import JamDesktopCore
import ToneForgeEngine

final class ChordRibbonModelTests: XCTestCase {

    // C [0,2) G [2,4) — gap — Am [5,7)
    private let model = ChordRibbonModel(
        chords: [
            ChordEvent(start: 0, end: 2, symbol: "C"),
            ChordEvent(start: 2, end: 4, symbol: "G"),
            ChordEvent(start: 5, end: 7, symbol: "Am"),
        ],
        sections: [
            SectionEvent(start: 0, end: 4, label: "Verse"),
            SectionEvent(start: 4, end: 7, label: "Chorus"),
        ]
    )

    func testBoundariesStartInclusiveEndExclusive() {
        XCTAssertEqual(model.currentChord(at: 0)?.symbol, "C")
        XCTAssertEqual(model.currentChord(at: 1.999)?.symbol, "C")
        XCTAssertEqual(model.currentChord(at: 2.0)?.symbol, "G")
        XCTAssertEqual(model.currentChord(at: 3.999)?.symbol, "G")
        XCTAssertNil(model.currentChord(at: 4.0))  // gap starts
    }

    func testGapAndOutOfRangeReturnNil() {
        XCTAssertNil(model.currentChord(at: 4.5))   // gap
        XCTAssertNil(model.currentChord(at: -1))    // before song
        XCTAssertNil(model.currentChord(at: 7.0))   // after last end
        XCTAssertNil(model.currentChord(at: 100))
    }

    func testWindowFromInsideChord() {
        XCTAssertEqual(
            model.window(at: 2.5, count: 3).map(\.symbol),
            ["G", "Am"]
        )
    }

    func testWindowFromGapStartsAtNextChord() {
        XCTAssertEqual(
            model.window(at: 4.5, count: 2).map(\.symbol),
            ["Am"]
        )
    }

    func testWindowPastEndIsEmpty() {
        XCTAssertTrue(model.window(at: 8, count: 3).isEmpty)
        XCTAssertTrue(model.window(at: 1, count: 0).isEmpty)
    }

    func testUnsortedInputIsSorted() {
        let shuffled = ChordRibbonModel(
            chords: [
                ChordEvent(start: 5, end: 7, symbol: "Am"),
                ChordEvent(start: 0, end: 2, symbol: "C"),
                ChordEvent(start: 2, end: 4, symbol: "G"),
            ],
            sections: []
        )
        XCTAssertEqual(shuffled.currentChord(at: 3)?.symbol, "G")
    }

    func testCurrentSection() {
        XCTAssertEqual(model.currentSection(at: 1)?.label, "Verse")
        XCTAssertEqual(model.currentSection(at: 5)?.label, "Chorus")
    }

    func testEmptyTimeline() {
        let empty = ChordRibbonModel(chords: [], sections: [])
        XCTAssertNil(empty.currentChord(at: 0))
        XCTAssertTrue(empty.window(at: 0, count: 4).isEmpty)
    }
}
