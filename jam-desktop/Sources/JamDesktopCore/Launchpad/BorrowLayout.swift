// BorrowLayout.swift
//
// Borrow ("Add from another song") pad arrangement — the PURE twin of
// web's kit.js `arrangeBorrowLayout`. A borrow manifest carries BOTH
// songs' loop pads, each tagged `initial` (the current song) or `donor`
// (the borrowed song). The backend packs them as one undivided block, so
// this re-lays them on the 8-wide 64 grid so the shape reads clearly:
//
//   • the current song's loops fill the TOP rows,
//   • ONE full blank divider row separates the two songs,
//   • the donor's loops start on the next FULL row.
//
// Additive: every source pad is placed (none dropped) and the grid stays
// 64. When both blocks plus a divider can't fit 64 (a rare full 4-stem
// borrow = 32 + 32) the divider is dropped and the donor block is packed
// flush after the initial one, so no donor pad is ever pushed off.
//
// Kept bit-parallel with kit.js so the two surfaces lay a borrow out
// identically (parity doctrine, rule 4).

import Foundation

/// Which song a borrow pad came from.
public enum BorrowPadSource: String, Sendable, Equatable {
    case initial   // the current (host) song — blue tint
    case donor     // the borrowed song — amber tint
}

/// One borrow pad as delivered by the manifest: its backend `padIdx`
/// (used only for stable within-source ordering — it also keys the
/// downloaded sample file, so callers must preserve it) and its source.
public struct BorrowPadRef: Sendable, Equatable {
    public let padIdx: Int
    public let source: BorrowPadSource
    public init(padIdx: Int, source: BorrowPadSource) {
        self.padIdx = padIdx
        self.source = source
    }
}

/// Where one input pad lands: which input it is, the row-major grid slot
/// (0..<64) it takes on the 8×8 launchpad, and its source.
public struct BorrowPlacement: Sendable, Equatable {
    /// Index into the `pads` array passed to `arrangeBorrowLayout`.
    public let inputIndex: Int
    /// Row-major slot 0..<64 (row = slot / 8, col = slot % 8).
    public let gridSlot: Int
    public let source: BorrowPadSource
    public init(inputIndex: Int, gridSlot: Int, source: BorrowPadSource) {
        self.inputIndex = inputIndex
        self.gridSlot = gridSlot
        self.source = source
    }
}

/// Result of arranging a borrow manifest onto the grid.
public struct BorrowGridLayout: Sendable, Equatable {
    public let placements: [BorrowPlacement]
    /// 0-based row of the blank divider, or -1 when the grid is too full
    /// to spare one (32 + 32 borrow).
    public let dividerRow: Int
    public init(placements: [BorrowPlacement], dividerRow: Int) {
        self.placements = placements
        self.dividerRow = dividerRow
    }
}

/// Re-lay a borrow manifest's pads onto the 8×8 (64) grid: current-song
/// loops on the top rows, a blank divider row, donor loops on the next
/// full row. Pure — mirrors web `arrangeBorrowLayout(pads, cols)`.
public func arrangeBorrowLayout(
    _ pads: [BorrowPadRef], cols: Int = 8
) -> BorrowGridLayout {
    let cols = max(1, cols)
    let rows = 8                         // 8×8 launchpad grid = 64 cells

    // Partition by source, keeping the original input index so callers can
    // recover the concrete pad. "initial" or anything non-donor → current.
    var initial: [(index: Int, ref: BorrowPadRef)] = []
    var donor: [(index: Int, ref: BorrowPadRef)] = []
    for (i, p) in pads.enumerated() {
        if p.source == .donor { donor.append((i, p)) }
        else { initial.append((i, p)) }
    }
    // Keep the backend's within-block section order (Verse, Chorus, …).
    initial.sort { $0.ref.padIdx < $1.ref.padIdx }
    donor.sort { $0.ref.padIdx < $1.ref.padIdx }

    let initialRows = (initial.count + cols - 1) / cols
    let donorRows = (donor.count + cols - 1) / cols
    // Blank divider row only when the grid has room. A full 32 + 32 borrow
    // fills all 64 cells, so drop the divider and pack the donor block flush
    // after the initial pads rather than lose donor pads.
    let wantDivider = !initial.isEmpty && !donor.isEmpty
        && (initialRows + 1 + donorRows) <= rows
    let dividerRow = wantDivider ? initialRows : -1
    let donorBase = wantDivider ? (initialRows + 1) * cols : initial.count

    var placements: [BorrowPlacement] = []
    placements.reserveCapacity(initial.count + donor.count)
    for (slot, item) in initial.enumerated() {
        placements.append(.init(
            inputIndex: item.index, gridSlot: slot, source: .initial))
    }
    for (slot, item) in donor.enumerated() {
        placements.append(.init(
            inputIndex: item.index, gridSlot: donorBase + slot, source: .donor))
    }
    return BorrowGridLayout(placements: placements, dividerRow: dividerRow)
}
