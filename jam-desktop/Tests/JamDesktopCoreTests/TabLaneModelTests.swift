// TabLaneModelTests.swift
//
// Layout math parity with picking-tab-lane.js: pitch->string/fret
// heuristic, geometry constants, placement x, per-frame translation.

import XCTest
@testable import JamDesktopCore

final class TabLaneModelTests: XCTestCase {

    // MARK: pitchToStringFret

    func testBiasPrefersFretClosestToFive() {
        // MIDI 45 = open A, but low-E fret 5 scores 0 vs A fret 0
        // scoring 5 — JS picks low E.
        let sf = TabLaneModel.pitchToStringFret(45)
        XCTAssertEqual(sf?.string, 0)
        XCTAssertEqual(sf?.fret, 5)
    }

    func testLowEOpenOnlyOption() {
        let sf = TabLaneModel.pitchToStringFret(40)
        XCTAssertEqual(sf?.string, 0)
        XCTAssertEqual(sf?.fret, 0)
    }

    func testHighPitchPicksBStringFretFive() {
        // MIDI 64: G fret 9 (score 4) vs B fret 5 (score 0).
        let sf = TabLaneModel.pitchToStringFret(64)
        XCTAssertEqual(sf?.string, 4)
        XCTAssertEqual(sf?.fret, 5)
    }

    func testOutOfRangePitchesReturnNil() {
        XCTAssertNil(TabLaneModel.pitchToStringFret(39))  // below low E
        XCTAssertNil(TabLaneModel.pitchToStringFret(82))  // above fret 17 everywhere
    }

    func testTopOfRange() {
        // MIDI 81 = high E fret 17, only playable string.
        let sf = TabLaneModel.pitchToStringFret(81)
        XCTAssertEqual(sf?.string, 5)
        XCTAssertEqual(sf?.fret, 17)
    }

    // MARK: Geometry (JS defaults)

    func testDefaultGeometryMatchesJS() {
        let model = TabLaneModel()
        XCTAssertEqual(model.usableWidth, 592)               // 640 - 36 - 12
        XCTAssertEqual(model.pxPerSec, 207.2, accuracy: 0.001) // 0.7*592/2
        XCTAssertEqual(model.playheadX, 213.6, accuracy: 0.001) // 36 + 0.3*592
        XCTAssertEqual(model.stringSpacing, 16.4, accuracy: 0.001) // (96-14)/5
    }

    func testHighEDrawnOnTop() {
        let model = TabLaneModel()
        XCTAssertEqual(model.stringY(forString: 5), model.stringTop)
        XCTAssertEqual(model.stringY(forString: 0), model.stringBottom)
        XCTAssertEqual(
            TabLaneModel.stringLabelsTopToBottom, ["E", "B", "G", "D", "A", "E"])
    }

    // MARK: Placement + translation

    func testPlacementAbsoluteXAndTranslationAlignPlayhead() {
        var model = TabLaneModel()
        model.notes = [TabLaneNote(pitch: 45, startS: 3.0)]
        let placement = model.placements()[0]
        XCTAssertEqual(placement.x, 3.0 * model.pxPerSec, accuracy: 0.001)

        // At t = 3.0 the note must land exactly on the playhead.
        let visibleX = placement.x + model.translation(at: 3.0)
        XCTAssertEqual(visibleX, model.playheadX, accuracy: 0.001)
    }

    func testUnmappablePitchesSkipped() {
        var model = TabLaneModel()
        model.notes = [
            TabLaneNote(pitch: 10, startS: 0),   // below low E
            TabLaneNote(pitch: 45, startS: 1),
        ]
        XCTAssertEqual(model.placements().count, 1)
    }

    func testLookaheadRejectsNonPositive() {
        var model = TabLaneModel()
        model.lookaheadS = 4.0
        XCTAssertEqual(model.lookaheadS, 4.0)
        model.lookaheadS = 0
        XCTAssertEqual(model.lookaheadS, 4.0) // unchanged (JS guard)
        // Halving lookahead doubles scroll speed.
        model.lookaheadS = 2.0
        let fast = model.pxPerSec
        model.lookaheadS = 4.0
        XCTAssertEqual(fast, model.pxPerSec * 2, accuracy: 0.001)
    }

    func testNoteNames() {
        XCTAssertEqual(TabLaneModel.noteName(60), "C")
        XCTAssertEqual(TabLaneModel.noteName(61), "C#")
        XCTAssertEqual(TabLaneModel.noteName(69), "A")
    }
}
