// SamplePadGridGeometry.swift
//
// Pure grid-index + hit-geometry math for the on-screen sample pad grid
// (SamplePadGrid4x4). Extracted out of the SwiftUI view so the routing math
// that kept regressing — the 4×4 top-left-quadrant shift and the 8×8
// full-grid identity, plus the touch-point → cell-center geometry the radial
// menu anchors on — is unit-testable (SamplePadGridGeometryTests) instead of
// only reachable through a live view.
//
// The launchpad convention: grid row 1 = BOTTOM, row 8 = top; col 1 = left.
// A compact 4×4 kit is the TOP-LEFT quadrant of the 8×8 grid (grid rows 5–8,
// cols 1–4), so a local row shifts UP by (8 − rows). An 8×8 grid is identity.

import CoreGraphics

public enum SamplePadGridGeometry {

    /// Local (row 1 = bottom) grid coordinate → the 8×8 PadIndex coordinate the
    /// coordinator binds. A 4×4 grid maps into the top-left quadrant by adding
    /// `8 - rows` to the row; an 8×8 grid (`rows == 8`) is the identity.
    ///
    /// This is the exact map used by `SamplePadGrid4x4.gridIndex`. Getting it
    /// wrong routes a tap/waveform to the wrong pad (the 64-grid routing bug).
    public static func gridIndex(
        row: Int, col: Int, rows: Int
    ) -> (row: Int, col: Int) {
        (row + (8 - rows), col)
    }

    /// Center point of a pad cell within a container of `size`, for a
    /// `cols`×`rows` grid with `tileSpacing` gaps. `localRow`/`localCol` are
    /// 1-based with row 1 = BOTTOM (so it is flipped to a top-origin screen
    /// row). The radial menu anchors on this; an off-by-one here dropped the
    /// hold-radial onto the wrong pad.
    public static func padCenter(
        localRow: Int, localCol: Int, rows: Int, cols: Int,
        tileSpacing: CGFloat, size: CGSize
    ) -> CGPoint {
        let totalGapWidth = tileSpacing * CGFloat(cols - 1)
        let totalGapHeight = tileSpacing * CGFloat(rows - 1)
        let cellWidth = (size.width - totalGapWidth) / CGFloat(cols)
        let cellHeight = (size.height - totalGapHeight) / CGFloat(rows)

        let screenCol = localCol - 1      // 0-based from the left
        let screenRow = rows - localRow   // flip: local row 1 = bottom

        let x = CGFloat(screenCol) * (cellWidth + tileSpacing) + cellWidth / 2
        let y = CGFloat(screenRow) * (cellHeight + tileSpacing) + cellHeight / 2
        return CGPoint(x: x, y: y)
    }
}
