// SamplePadGrid4x4Tests.swift
//
// Pins the 4×4 → 8×8 quadrant mapping the named sample grid relies
// on. ModeCoordinator.sampleQuadrantContent binds pack padIdx p to
// grid (row: 8 - p/4, col: p%4 + 1) — the top-left quadrant, rows
// 5–8, cols 1–4. SamplePadGrid4x4 must hit exactly those cells so
// audio, LEDs, recording, and the Launchpad mirror stay coherent.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

// The 4×4 → 8×8 quadrant math now lives in the pure, engine-side
// SamplePadGridGeometry (SamplePadGrid4x4.gridIndex is a thin wrapper). This
// file previously called a private instance method as if it were static, so
// it never compiled — reviving it against the extracted function both fixes
// that and keeps the pack-pad-order relationship pinned on the Mobile side.
final class SamplePadGrid4x4Tests: XCTestCase {

    /// Local 4×4 coordinates (row 1 = bottom) land in grid rows 5–8,
    /// cols 1–4.
    func testGridIndexCoversSampleQuadrant() {
        for row in 1...4 {
            for col in 1...4 {
                let (gridRow, gridCol) = SamplePadGridGeometry.gridIndex(
                    row: row, col: col, rows: 4)
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
            let (gridRow, gridCol) = SamplePadGridGeometry.gridIndex(
                row: localRow, col: localCol, rows: 4)
            XCTAssertEqual(gridRow, expectedGridRow, "padIdx \(padIdx)")
            XCTAssertEqual(gridCol, expectedGridCol, "padIdx \(padIdx)")
        }
    }

    /// launchpad-edit-mode: the hold-radial recognition exists ONLY on
    /// an editing bench, never in performance. This single predicate
    /// decides whether PadTouchOverlay gets a long-press handler at
    /// all — false means Jam's Edit-off grid AND the Perform stage arm
    /// no hold timer on touch-down (zero gesture-recognition tax).
    func testHoldRadialGate() {
        XCTAssertTrue(
            SamplePadGrid4x4.holdRadialEnabled(editing: true, stage: false),
            "Jam with Edit on is the bench — radial available")
        XCTAssertFalse(
            SamplePadGrid4x4.holdRadialEnabled(editing: false, stage: false),
            "Edit off = performance: no hold recognition")
        XCTAssertFalse(
            SamplePadGrid4x4.holdRadialEnabled(editing: true, stage: true),
            "stage is play-only even if a host passes editing")
        XCTAssertFalse(
            SamplePadGrid4x4.holdRadialEnabled(editing: false, stage: true))
    }
}
