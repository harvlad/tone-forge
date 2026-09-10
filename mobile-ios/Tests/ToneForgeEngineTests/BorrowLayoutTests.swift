// BorrowLayoutTests.swift
//
// Pins SampleBank.arrangeBorrowLayout to the web kit.js#arrangeBorrowLayout
// placement semantics (ToneForgeEngine/launchpad.js port-parity rule): a
// borrow manifest re-lays onto the 8×8 grid as current-song-top / blank
// divider row / donor-below, row-major 8 wide, every pad placed, grid stays
// 64. A 32+32 borrow drops the divider to fit; a non-borrow pack is untouched.

import XCTest
@testable import ToneForgeEngine

final class BorrowLayoutTests: XCTestCase {

    private func pad(_ idx: Int, source: String) -> SamplePad {
        SamplePad(padIdx: idx, name: "L\(idx)", family: .mixed,
                  colorHint: source == "donor" ? "#F59E0B" : "#3B82F6",
                  loopable: true, source: source)
    }

    private func pack(_ pads: [SamplePad]) -> SamplePack {
        SamplePack(packId: "borrow-x", name: "Donor Song · kit",
                   family: .mixed, pads: pads)
    }

    // 8 initial + 8 donor → initial on row 0 (padIdx 0..7), a blank divider
    // row (8..15 unused), donor starting on the next full row (16..23).
    func testDividerBetweenBlocks() {
        var pads = (0..<8).map { pad($0, source: "initial") }
        pads += (0..<8).map { pad(100 + $0, source: "donor") }
        let out = SampleBank.arrangeBorrowLayout(
            pack(pads), hostName: "This Song", donorName: "Donor")

        let initial = out.pads.filter { $0.source != "donor" }.sorted { $0.padIdx < $1.padIdx }
        let donor = out.pads.filter { $0.source == "donor" }.sorted { $0.padIdx < $1.padIdx }
        XCTAssertEqual(initial.map(\.padIdx), Array(0..<8))
        XCTAssertEqual(donor.map(\.padIdx), Array(16..<24))   // row 0 initial, row 1 divider, row 2 donor
        // Source labels stamped from host/donor names.
        XCTAssertTrue(initial.allSatisfy { $0.sourceName == "This Song" })
        XCTAssertTrue(donor.allSatisfy { $0.sourceName == "Donor" })
        // Every pad placed; grid never exceeds 64.
        XCTAssertEqual(out.pads.count, 16)
        XCTAssertTrue(out.pads.allSatisfy { (0..<64).contains($0.padIdx) })
    }

    // A full 4-stem borrow (32 initial + 32 donor) fills all 64 cells, so the
    // divider is dropped and the donor block packs flush after the initial one.
    func testFullBorrowDropsDivider() {
        var pads = (0..<32).map { pad($0, source: "initial") }
        pads += (0..<32).map { pad(100 + $0, source: "donor") }
        let out = SampleBank.arrangeBorrowLayout(pack(pads))

        let donor = out.pads.filter { $0.source == "donor" }.sorted { $0.padIdx < $1.padIdx }
        XCTAssertEqual(donor.first?.padIdx, 32)   // flush after 32 initial — no divider
        XCTAssertEqual(donor.last?.padIdx, 63)
        XCTAssertEqual(out.pads.count, 64)
    }

    // COMPACT (16 = 4×4): the 16/64 toggle RE-ARRANGES a borrow rather than
    // clipping it — best 8 of EACH song, so the donor is never dropped.
    private func scoredPad(_ idx: Int, source: String, score: Double) -> SamplePad {
        SamplePad(padIdx: idx, name: "L\(idx)", family: .mixed,
                  colorHint: source == "donor" ? "#F59E0B" : "#3B82F6",
                  loopScore: nil, loopable: true, performanceScore: score,
                  source: source)
    }

    func testCompactBestOfBoth() {
        // 12 initial (score = idx, higher idx better) + 12 donor (score =
        // 11-idx, lower idx better) → 8 + 8 selected into a 16 grid.
        var pads = (0..<12).map { scoredPad($0, source: "initial", score: Double($0)) }
        pads += (0..<12).map { scoredPad(100 + $0, source: "donor", score: Double(11 - $0)) }
        let out = SampleBank.arrangeBorrowLayout(
            pack(pads), hostName: "This Song", donorName: "Donor",
            cols: 4, rows: 4)

        let initial = out.pads.filter { $0.source != "donor" }.sorted { $0.padIdx < $1.padIdx }
        let donor = out.pads.filter { $0.source == "donor" }.sorted { $0.padIdx < $1.padIdx }
        // BOTH songs survive the shrink.
        XCTAssertEqual(out.pads.count, 16)
        XCTAssertEqual(initial.count, 8)
        XCTAssertEqual(donor.count, 8)
        // 8 initial in idx 0..7, 8 donor in idx 8..15.
        XCTAssertEqual(initial.map(\.padIdx), Array(0..<8))
        XCTAssertEqual(donor.map(\.padIdx), Array(8..<16))
        // best-by-score: highest 8 initial = original name L4..L11; highest 8
        // donor = L100..L107 (the re-lay preserves each pad's name).
        XCTAssertEqual(initial.map(\.name), (4..<12).map { "L\($0)" })
        XCTAssertEqual(donor.map(\.name), (100..<108).map { "L\($0)" })
        // Source labels still stamped after the compact re-lay.
        XCTAssertTrue(initial.allSatisfy { $0.sourceName == "This Song" })
        XCTAssertTrue(donor.allSatisfy { $0.sourceName == "Donor" })
    }

    // COMPACT underflow: one song short of its half → the other fills the rest,
    // both present, grid never left emptier than needed.
    func testCompactUnderflowOtherSongFills() {
        var pads = (0..<3).map { scoredPad($0, source: "initial", score: 1) }
        pads += (0..<20).map { scoredPad(100 + $0, source: "donor", score: Double($0)) }
        let out = SampleBank.arrangeBorrowLayout(pack(pads), cols: 4, rows: 4)
        let initial = out.pads.filter { $0.source != "donor" }
        let donor = out.pads.filter { $0.source == "donor" }
        XCTAssertEqual(out.pads.count, 16)
        XCTAssertEqual(initial.count, 3)
        XCTAssertEqual(donor.count, 13)
        XCTAssertEqual(initial.map(\.padIdx).sorted(), [0, 1, 2])
        XCTAssertEqual(donor.map(\.padIdx).min(), 3)   // donor starts after initial
    }

    // Not a borrow manifest (no source tags) → returned unchanged (default-off).
    func testNonBorrowUntouched() {
        let plain = SamplePack(
            packId: "starter", name: "Starter", family: .mixed,
            pads: [SamplePad(padIdx: 5, name: "Kick", family: .percussion)])
        let out = SampleBank.arrangeBorrowLayout(plain)
        XCTAssertEqual(out.pads.map(\.padIdx), [5])
        XCTAssertNil(out.pads.first?.sourceName)
    }
}
