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

    // MARK: - Controller: mount at slots, labels, forced 64 grid.

    @MainActor
    func testAdoptBorrowAssignmentsPlacesLabelsAndForces64() {
        let lp = LaunchpadController(nowProvider: { 0 })
        lp.padCount = 16   // start compact; a borrow must expand to 64
        let chop = Chop(idx: 0, startSec: 0, endSec: 1, durationSec: 1,
                        kind: "phrase")
        lp.adoptBorrowAssignments([
            .init(slot: 0, chop: chop, stem: "drums", sourceLabel: "My Song"),
            .init(slot: 16, chop: chop, stem: "drums", sourceLabel: "Donor Song"),
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
}
