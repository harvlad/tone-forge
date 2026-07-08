// SamplePadGrid4x4Tests.swift
//
// Pins the 4×4 → 8×8 quadrant mapping the named sample grid relies
// on. ModeCoordinator.sampleQuadrantContent binds pack padIdx p to
// grid (row: 8 - p/4, col: p%4 + 1) — the top-left quadrant, rows
// 5–8, cols 1–4. SamplePadGrid4x4 must hit exactly those cells so
// audio, LEDs, recording, and the Launchpad mirror stay coherent.

import XCTest
@testable import ToneForgeMobile

final class SamplePadGrid4x4Tests: XCTestCase {

    /// Local 4×4 coordinates (row 1 = bottom) land in grid rows 5–8,
    /// cols 1–4.
    func testGridIndexCoversSampleQuadrant() {
        for row in 1...4 {
            for col in 1...4 {
                let (gridRow, gridCol) = SamplePadGrid4x4.gridIndex(
                    row: row, col: col)
                XCTAssertEqual(gridRow, row + 4)
                XCTAssertEqual(gridCol, col)
                XCTAssertTrue((5...8).contains(gridRow))
                XCTAssertTrue((1...4).contains(gridCol))
            }
        }
    }

    /// The screen layout (top row = grid row 8) matches the pack
    /// binding padIdx p → (row: 8 - p/4, col: p%4 + 1): padIdx 0 is
    /// top-left, padIdx 15 bottom-right.
    func testGridIndexMatchesPackPadOrder() {
        for padIdx in 0..<16 {
            let expectedGridRow = 8 - padIdx / 4
            let expectedGridCol = padIdx % 4 + 1
            // Screen row r (0 = top) hosts grid row 8 - r; local
            // overlay row (1 = bottom) for screen row r is 4 - r.
            let localRow = 4 - padIdx / 4
            let localCol = padIdx % 4 + 1
            let (gridRow, gridCol) = SamplePadGrid4x4.gridIndex(
                row: localRow, col: localCol)
            XCTAssertEqual(gridRow, expectedGridRow, "padIdx \(padIdx)")
            XCTAssertEqual(gridCol, expectedGridCol, "padIdx \(padIdx)")
        }
    }
}
