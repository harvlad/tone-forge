// BorrowLayoutTests.swift
//
// The pure borrow arranger (web kit.js `arrangeBorrowLayout` twin) plus a
// small controller check that a borrow grid mounts at the right slots with
// per-pad source labels and forces the 64 grid.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

final class BorrowLayoutTests: XCTestCase {

    private func refs(
        initial: Int, donor: Int, initialBase: Int = 0, donorBase: Int = 8
    ) -> [BorrowPadRef] {
        var out: [BorrowPadRef] = []
        for i in 0..<initial {
            out.append(.init(padIdx: initialBase + i, source: .initial))
        }
        for i in 0..<donor {
            out.append(.init(padIdx: donorBase + i, source: .donor))
        }
        return out
    }

    // MARK: - Single-stem borrow (8 + 8): the common case.

    func testSingleStemBorrowTopDividerBelow() {
        // Backend hands 8 initial (padIdx 0..7) then 8 donor (padIdx 8..15).
        let layout = arrangeBorrowLayout(refs(initial: 8, donor: 8))

        XCTAssertEqual(layout.dividerRow, 1,
                       "one full initial row → blank divider on row 1")

        let initial = layout.placements.filter { $0.source == .initial }
        let donor = layout.placements.filter { $0.source == .donor }
        XCTAssertEqual(initial.count, 8)
        XCTAssertEqual(donor.count, 8)

        // Current song fills the top row (slots 0..7).
        XCTAssertEqual(initial.map(\.gridSlot).sorted(), Array(0..<8))
        // Donor starts on the NEXT full row after the blank divider: row 2,
        // i.e. slots 16..23 (row 1 = slots 8..15 stays empty).
        XCTAssertEqual(donor.map(\.gridSlot).sorted(), Array(16..<24))
        // Nothing lands on the divider row.
        XCTAssertFalse(layout.placements.contains { (8..<16).contains($0.gridSlot) })
    }

    // MARK: - Additive: every input placed, none dropped.

    func testAllPadsPlacedNoneDropped() {
        let input = refs(initial: 5, donor: 7)
        let layout = arrangeBorrowLayout(input)
        XCTAssertEqual(layout.placements.count, input.count)
        // Slots are unique (no two pads collide).
        XCTAssertEqual(Set(layout.placements.map(\.gridSlot)).count,
                       layout.placements.count)
        // Every input index appears exactly once.
        XCTAssertEqual(Set(layout.placements.map(\.inputIndex)),
                       Set(0..<input.count))
    }

    // MARK: - Full 4-stem borrow (32 + 32): divider dropped to fit 64.

    func testFullGridDropsDivider() {
        let layout = arrangeBorrowLayout(refs(initial: 32, donor: 32))
        XCTAssertEqual(layout.dividerRow, -1,
                       "32 + 32 fills all 64 cells → no room for a divider")
        let donor = layout.placements.filter { $0.source == .donor }
        // Donor packs flush after the 32 initial pads: slots 32..63.
        XCTAssertEqual(donor.map(\.gridSlot).sorted(), Array(32..<64))
        // All 64 slots used, none dropped.
        XCTAssertEqual(Set(layout.placements.map(\.gridSlot)), Set(0..<64))
    }

    // MARK: - Uneven blocks still get a divider when they fit.

    func testUnevenBlocksDivider() {
        // 3 initial (1 row) + 5 donor (1 row): 1 + 1 + 1 = 3 <= 8 → divider.
        let layout = arrangeBorrowLayout(refs(initial: 3, donor: 5))
        XCTAssertEqual(layout.dividerRow, 1)
        let donor = layout.placements.filter { $0.source == .donor }
        // donorBase = (1 + 1) * 8 = 16.
        XCTAssertEqual(donor.map(\.gridSlot).sorted(), Array(16..<21))
    }

    // MARK: - Order within a block follows padIdx even when shuffled.

    func testWithinBlockOrderByPadIdx() {
        let input: [BorrowPadRef] = [
            .init(padIdx: 5, source: .initial),
            .init(padIdx: 1, source: .initial),
            .init(padIdx: 3, source: .initial),
        ]
        let layout = arrangeBorrowLayout(input)
        // Sorted by padIdx: 1 → slot 0, 3 → slot 1, 5 → slot 2.
        let bySlot = layout.placements.sorted { $0.gridSlot < $1.gridSlot }
        XCTAssertEqual(bySlot.map { input[$0.inputIndex].padIdx }, [1, 3, 5])
    }

    // MARK: - Only-initial (no donor) → no divider, no donor pads.

    func testOnlyInitialNoDivider() {
        let layout = arrangeBorrowLayout(refs(initial: 4, donor: 0))
        XCTAssertEqual(layout.dividerRow, -1)
        XCTAssertTrue(layout.placements.allSatisfy { $0.source == .initial })
        XCTAssertEqual(layout.placements.map(\.gridSlot).sorted(), Array(0..<4))
    }

    // MARK: - Compact (16 = 4×4): best-of-both, never drop a song.

    /// Score-carrying refs: initial padIdx `i` scores `i` (higher idx = better),
    /// donor padIdx `100+i` scores `11-i` (lower idx = better).
    private func scoredRefs(initial: Int, donor: Int) -> [BorrowPadRef] {
        var out: [BorrowPadRef] = []
        for i in 0..<initial {
            out.append(.init(padIdx: i, source: .initial, score: Double(i)))
        }
        for i in 0..<donor {
            out.append(.init(padIdx: 100 + i, source: .donor, score: Double(11 - i)))
        }
        return out
    }

    func testCompactBestOfBoth() {
        // 12 + 12 candidates, 4×4 = 16 slots → keep the best 8 of EACH song.
        let layout = arrangeBorrowLayout(scoredRefs(initial: 12, donor: 12),
                                         cols: 4, rows: 4)
        XCTAssertEqual(layout.dividerRow, -1, "no divider at 16")
        XCTAssertEqual(layout.placements.count, 16)

        let initial = layout.placements.filter { $0.source == .initial }
        let donor = layout.placements.filter { $0.source == .donor }
        // BOTH songs survive the shrink — the donor is no longer dropped.
        XCTAssertEqual(initial.count, 8)
        XCTAssertEqual(donor.count, 8)
        // 8 initial in idx 0..7, 8 donor in idx 8..15.
        XCTAssertEqual(initial.map(\.gridSlot).sorted(), Array(0..<8))
        XCTAssertEqual(donor.map(\.gridSlot).sorted(), Array(8..<16))

        // best-by-score selection: highest-scored 8 initial = source padIdx
        // 4..11; highest-scored 8 donor = source padIdx 100..107.
        let input = scoredRefs(initial: 12, donor: 12)
        XCTAssertEqual(initial.map { input[$0.inputIndex].padIdx }.sorted(),
                       Array(4..<12))
        XCTAssertEqual(donor.map { input[$0.inputIndex].padIdx }.sorted(),
                       Array(100..<108))
        // within-block section order preserved (grid slot ascends with padIdx).
        let initBySlot = initial.sorted { $0.gridSlot < $1.gridSlot }
        XCTAssertEqual(initBySlot.map { input[$0.inputIndex].padIdx },
                       Array(4..<12))
    }

    func testCompactUnderflowOtherSongFills() {
        // 3 initial + 20 donor at 16: initial keeps all 3, donor fills the 13
        // remaining slots — grid never left emptier than needed, both present.
        var input: [BorrowPadRef] = []
        for i in 0..<3 { input.append(.init(padIdx: i, source: .initial, score: 1)) }
        for i in 0..<20 { input.append(.init(padIdx: 100 + i, source: .donor, score: Double(i))) }
        let layout = arrangeBorrowLayout(input, cols: 4, rows: 4)
        let initial = layout.placements.filter { $0.source == .initial }
        let donor = layout.placements.filter { $0.source == .donor }
        XCTAssertEqual(layout.placements.count, 16, "grid filled")
        XCTAssertEqual(initial.count, 3)
        XCTAssertEqual(donor.count, 13)
        XCTAssertEqual(initial.map(\.gridSlot).sorted(), Array(0..<3))
        XCTAssertEqual(donor.map(\.gridSlot).min(), 3, "donor starts after initial")
    }

    // MARK: - Controller: mount, labels, force 64, re-arrange on toggle.

    @MainActor
    func testAdoptBorrowAssignmentsPlacesLabelsAndForces64() {
        let lp = LaunchpadController(nowProvider: { 0 })
        lp.padCount = 16   // start compact; a borrow must expand to 64
        let chop = Chop(idx: 0, startSec: 0, endSec: 1, durationSec: 1,
                        kind: "phrase")
        lp.adoptBorrowAssignments([
            .init(chop: chop, stem: "drums", sourceLabel: "My Song", source: .initial),
            .init(chop: chop, stem: "drums", sourceLabel: "Donor Song", source: .donor),
        ])
        XCTAssertEqual(lp.padCount, 64, "borrow expands to the full grid")
        XCTAssertNotNil(lp.assignments[LaunchpadPad(row: 0, col: 0)])
        XCTAssertNotNil(lp.assignments[LaunchpadPad(row: 2, col: 0)])
        // Divider row (row 1) stays empty.
        XCTAssertNil(lp.assignments[LaunchpadPad(row: 1, col: 0)])
        XCTAssertEqual(lp.sourceLabel(for: LaunchpadPad(row: 0, col: 0)), "My Song")
        XCTAssertEqual(lp.sourceLabel(for: LaunchpadPad(row: 2, col: 0)), "Donor Song")
        // A subsequent single-song grid load clears the borrow labels.
        lp.setChops([chop], stem: "drums", sliceMode: "chord")
        XCTAssertNil(lp.sourceLabel(for: LaunchpadPad(row: 0, col: 0)))
    }

    @MainActor
    func testBorrowToggleTo16RearrangesBestOfBoth() {
        let lp = LaunchpadController(nowProvider: { 0 })
        func mount(idx: Int, source: BorrowPadSource, score: Double)
            -> LaunchpadController.BorrowMount {
            let chop = Chop(idx: idx, startSec: 0, endSec: 1, durationSec: 1,
                            kind: "phrase", performanceScore: score)
            return .init(chop: chop, stem: "drums",
                         sourceLabel: source == .donor ? "Donor" : "Host",
                         source: source)
        }
        var mounts: [LaunchpadController.BorrowMount] = []
        for i in 0..<12 { mounts.append(mount(idx: i, source: .initial, score: Double(i))) }
        for i in 0..<12 { mounts.append(mount(idx: 100 + i, source: .donor, score: Double(11 - i))) }
        lp.adoptBorrowAssignments(mounts)
        XCTAssertEqual(lp.padCount, 64)
        XCTAssertEqual(lp.assignments.count, 24, "all 24 borrow pads at 64")

        // Toggle to 16: best-of-both, both songs present, none of the donor lost.
        lp.padCount = 16
        XCTAssertEqual(lp.assignments.count, 16, "16 pads at 4×4")
        for slot in 0..<8 {
            XCTAssertEqual(
                lp.sourceLabel(for: LaunchpadPad(row: slot / 8, col: slot % 8)),
                "Host", "initial fills the top half at 16")
        }
        for slot in 8..<16 {
            XCTAssertEqual(
                lp.sourceLabel(for: LaunchpadPad(row: slot / 8, col: slot % 8)),
                "Donor", "donor fills the bottom half at 16")
        }

        // Toggle back to 64 restores the FULL set (re-arranged from the retained
        // mounts, not the clipped 16 view).
        lp.padCount = 64
        XCTAssertEqual(lp.assignments.count, 24, "64 restores all pads")
    }
}
