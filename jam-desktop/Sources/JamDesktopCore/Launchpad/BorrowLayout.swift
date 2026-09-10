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
    /// Playable ranking (performanceScore ?? loopScore ?? 0). Used ONLY when
    /// the target grid can't hold both songs in full (compact 16) — the best
    /// `capacity/2` of each song are kept. Ignored at 64 (everyone fits).
    public let score: Double
    public init(padIdx: Int, source: BorrowPadSource, score: Double = 0) {
        self.padIdx = padIdx
        self.source = source
        self.score = score
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

/// Re-lay a borrow manifest's pads onto the target grid. `cols`×`rows` is the
/// grid CAPACITY: 8×8 (64) for the full grid, 4×4 (16) for the compact one.
///
///   • FULL (capacity ≥ 64): unchanged — current-song loops on the top rows, a
///     blank divider row, donor loops on the next full row. Additive.
///   • COMPACT (capacity < 64, i.e. 16): BEST-OF-BOTH — no room for both songs
///     in full, so keep the best `capacity/2` initial pads AND the best
///     `capacity/2` donor pads by score, initial block from slot 0 and donor
///     block right after, so BOTH songs survive the shrink (the bug: 16-mode
///     used to show only the top rows and drop the donor). One song short of
///     its half → the other fills the remainder by score. No divider at 16.
///
/// Pure — the exact twin of web `arrangeBorrowLayout(pads, cols, rows)`.
public func arrangeBorrowLayout(
    _ pads: [BorrowPadRef], cols: Int = 8, rows: Int = 8
) -> BorrowGridLayout {
    let cols = max(1, cols)
    let rows = max(1, rows)
    let capacity = cols * rows

    // Partition by source, keeping the original input index so callers can
    // recover the concrete pad. "initial" or anything non-donor → current.
    var initial: [(index: Int, ref: BorrowPadRef)] = []
    var donor: [(index: Int, ref: BorrowPadRef)] = []
    for (i, p) in pads.enumerated() {
        if p.source == .donor { donor.append((i, p)) }
        else { initial.append((i, p)) }
    }

    if capacity >= 64 {
        // FULL layout — initial top, blank divider, donor below (unchanged).
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

    // COMPACT (16): best `capacity/2` of each song, initial block then donor.
    let perSong = capacity / 2
    // Rank by score desc, padIdx asc as a stable tie-break.
    let byScore: (
        (index: Int, ref: BorrowPadRef), (index: Int, ref: BorrowPadRef)
    ) -> Bool = {
        $0.ref.score != $1.ref.score
            ? $0.ref.score > $1.ref.score
            : $0.ref.padIdx < $1.ref.padIdx
    }
    let initByScore = initial.sorted(by: byScore)
    let donByScore = donor.sorted(by: byScore)
    var initialTake = min(initial.count, perSong)
    var donorTake = min(donor.count, perSong)
    // One song short of its half? Let the other fill the leftover by score, so
    // the grid isn't left emptier than it needs to be (initial fills first).
    var leftover = capacity - initialTake - donorTake
    if leftover > 0 {
        let addI = min(leftover, initial.count - initialTake)
        initialTake += addI; leftover -= addI
    }
    if leftover > 0 {
        let addD = min(leftover, donor.count - donorTake)
        donorTake += addD; leftover -= addD
    }
    // Take top-N by score, then restore section order (padIdx) within each block.
    let initialSel = initByScore.prefix(initialTake)
        .sorted { $0.ref.padIdx < $1.ref.padIdx }
    let donorSel = donByScore.prefix(donorTake)
        .sorted { $0.ref.padIdx < $1.ref.padIdx }

    var placements: [BorrowPlacement] = []
    placements.reserveCapacity(initialTake + donorTake)
    for (slot, item) in initialSel.enumerated() {
        placements.append(.init(
            inputIndex: item.index, gridSlot: slot, source: .initial))
    }
    for (slot, item) in donorSel.enumerated() {
        placements.append(.init(
            inputIndex: item.index, gridSlot: initialTake + slot, source: .donor))
    }
    return BorrowGridLayout(placements: placements, dividerRow: -1)
}
