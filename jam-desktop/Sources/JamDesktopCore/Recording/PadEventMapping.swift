// PadEventMapping.swift
//
// Conversion between the desktop grid coordinate (LaunchpadPad,
// row 0 = TOP, 0-based) and the ContributionEvent / PadIndex wire
// convention (row 1..8 with row 1 = BOTTOM, col 1..8) — the formula
// documented in ContributionEvent.swift:
//
//   event.row = 8 - pad.row      pad.row = 8 - event.row
//   event.col = pad.col + 1      pad.col = event.col - 1
//
// Captured sessions therefore use the exact same pad addressing as
// iOS takes, so recordings are cross-platform.

import Foundation
import ToneForgeEngine

public enum PadEventMapping {

    /// LaunchpadPad → (row, col) in the ContributionEvent convention.
    /// nil when the pad is outside the 8x8 grid.
    public static func eventCoordinates(
        for pad: LaunchpadPad
    ) -> (row: Int, col: Int)? {
        guard (0..<8).contains(pad.row), (0..<8).contains(pad.col) else {
            return nil
        }
        return (row: 8 - pad.row, col: pad.col + 1)
    }

    /// (row, col) in the ContributionEvent convention → LaunchpadPad.
    /// nil when either coordinate is outside 1..8.
    public static func launchpadPad(row: Int, col: Int) -> LaunchpadPad? {
        guard (1...8).contains(row), (1...8).contains(col) else { return nil }
        return LaunchpadPad(row: 8 - row, col: col - 1)
    }

    /// LaunchpadPad → PadIndex (the bounce renderer keys pad buffers
    /// by PadIndex.rawValue). nil off-grid.
    public static func padIndex(for pad: LaunchpadPad) -> PadIndex? {
        guard let coords = eventCoordinates(for: pad) else { return nil }
        let index = PadIndex.at(row: coords.row, col: coords.col)
        return index.isValid ? index : nil
    }
}
