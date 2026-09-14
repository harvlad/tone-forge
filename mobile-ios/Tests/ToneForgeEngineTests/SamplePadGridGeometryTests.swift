// SamplePadGridGeometryTests.swift
//
// Pins the pure grid-index + hit-geometry math extracted from
// SamplePadGrid4x4. These guard the 64-grid routing regressions: a wrong
// quadrant shift routes a tap/waveform to the wrong pad, and a wrong cell
// center drops the hold-radial menu onto the wrong tile.

import XCTest
import CoreGraphics
import ToneForgeEngine

final class SamplePadGridGeometryTests: XCTestCase {

    // MARK: - gridIndex quadrant mapping

    // A 4×4 kit is the TOP-LEFT quadrant of the 8×8 grid: local rows shift up
    // by 8 − 4 = 4, columns unchanged. Local (1,1) = bottom-left of the kit =
    // grid (5,1); local (4,4) = grid (8,4).
    func test4x4MapsIntoTopLeftQuadrant() {
        XCTAssertEqual(g(row: 1, col: 1, rows: 4).row, 5)
        XCTAssertEqual(g(row: 1, col: 1, rows: 4).col, 1)
        XCTAssertEqual(g(row: 4, col: 4, rows: 4).row, 8)
        XCTAssertEqual(g(row: 4, col: 4, rows: 4).col, 4)
        // Whole quadrant lands in grid rows 5…8.
        for r in 1...4 {
            XCTAssertEqual(g(row: r, col: 1, rows: 4).row, r + 4)
        }
    }

    // An 8×8 grid (rows == 8) is the identity — no shift, top-origin mapping.
    func test8x8IsIdentity() {
        for r in 1...8 {
            for c in 1...8 {
                XCTAssertEqual(g(row: r, col: c, rows: 8).row, r)
                XCTAssertEqual(g(row: r, col: c, rows: 8).col, c)
            }
        }
    }

    private func g(row: Int, col: Int, rows: Int) -> (row: Int, col: Int) {
        SamplePadGridGeometry.gridIndex(row: row, col: col, rows: rows)
    }

    // MARK: - padCenter geometry (radial-menu anchor)

    func testPadCenterFlipsRowAndCentersCell() {
        // 4×4, no spacing, 400×400 → 100pt cells; centers at 50,150,250,350.
        let size = CGSize(width: 400, height: 400)
        // Bottom-left pad (local row 1, col 1) sits at screen bottom-left cell.
        let bl = SamplePadGridGeometry.padCenter(
            localRow: 1, localCol: 1, rows: 4, cols: 4,
            tileSpacing: 0, size: size)
        XCTAssertEqual(bl.x, 50, accuracy: 0.001)
        XCTAssertEqual(bl.y, 350, accuracy: 0.001)   // row 1 = bottom → y large

        // Top-right pad (local row 4, col 4) sits at screen top-right cell.
        let tr = SamplePadGridGeometry.padCenter(
            localRow: 4, localCol: 4, rows: 4, cols: 4,
            tileSpacing: 0, size: size)
        XCTAssertEqual(tr.x, 350, accuracy: 0.001)
        XCTAssertEqual(tr.y, 50, accuracy: 0.001)    // row 4 = top → y small
    }

    func testPadCenterAccountsForSpacing() {
        // 2 cols, 10pt gap, 210 wide → cell = (210-10)/2 = 100; col1 center 50,
        // col2 center = 100+10+50 = 160.
        let size = CGSize(width: 210, height: 210)
        let c1 = SamplePadGridGeometry.padCenter(
            localRow: 1, localCol: 1, rows: 2, cols: 2,
            tileSpacing: 10, size: size)
        let c2 = SamplePadGridGeometry.padCenter(
            localRow: 1, localCol: 2, rows: 2, cols: 2,
            tileSpacing: 10, size: size)
        XCTAssertEqual(c1.x, 50, accuracy: 0.001)
        XCTAssertEqual(c2.x, 160, accuracy: 0.001)
    }
}
