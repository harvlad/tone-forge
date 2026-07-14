// ClickGridTests.swift
//
// Pure click-grid math: index rounding with epsilon tolerance,
// floored-modulo accents for negative (count-in) indices, and the
// look-ahead window walk.

import XCTest
@testable import JamDesktopCore

final class ClickGridTests: XCTestCase {

    func testSecondsPerClick() {
        XCTAssertEqual(ClickGrid(bpm: 120).secondsPerClick, 0.5)
        XCTAssertEqual(ClickGrid(bpm: 60).secondsPerClick, 1.0)
    }

    func testClickIndexOnOrAfter() {
        let grid = ClickGrid(bpm: 120)   // clicks every 0.5 s
        XCTAssertEqual(grid.clickIndex(onOrAfter: 0), 0)
        XCTAssertEqual(grid.clickIndex(onOrAfter: 0.5), 1)   // exactly on a click
        XCTAssertEqual(grid.clickIndex(onOrAfter: 0.51), 2)
        XCTAssertEqual(grid.clickIndex(onOrAfter: -0.75), -1)
    }

    func testAccentEveryBarDownbeat() {
        let grid = ClickGrid(bpm: 120, beatsPerBar: 4)
        XCTAssertTrue(grid.isAccent(clickIndex: 0))
        XCTAssertFalse(grid.isAccent(clickIndex: 1))
        XCTAssertFalse(grid.isAccent(clickIndex: 3))
        XCTAssertTrue(grid.isAccent(clickIndex: 4))
    }

    func testNegativeIndicesAccentWithFlooredModulo() {
        let grid = ClickGrid(bpm: 120, beatsPerBar: 4)
        XCTAssertTrue(grid.isAccent(clickIndex: -4))    // count-in downbeat
        XCTAssertFalse(grid.isAccent(clickIndex: -1))   // beat 4 of prior bar
        XCTAssertFalse(grid.isAccent(clickIndex: -3))
    }

    func testWindowWalk() {
        let grid = ClickGrid(bpm: 60, beatsPerBar: 4)   // 1 s per click
        let clicks = grid.clicks(fromClickIndex: 0, before: 3.5)
        XCTAssertEqual(clicks.map(\.index), [0, 1, 2, 3])
        XCTAssertEqual(clicks.map(\.timeSeconds), [0, 1, 2, 3])
        XCTAssertEqual(clicks.map(\.isAccent), [true, false, false, false])
    }

    func testEmptyWindow() {
        let grid = ClickGrid(bpm: 60)
        XCTAssertTrue(grid.clicks(fromClickIndex: 5, before: 5.0).isEmpty)
    }

    func testDegenerateInputsClamped() {
        let grid = ClickGrid(bpm: 0, beatsPerBar: 0)
        XCTAssertEqual(grid.bpm, 1)
        XCTAssertEqual(grid.beatsPerBar, 1)
        XCTAssertTrue(grid.isAccent(clickIndex: 3))   // every beat accents in 1/1
    }
}
