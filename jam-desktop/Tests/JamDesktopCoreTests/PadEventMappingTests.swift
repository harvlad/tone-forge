// PadEventMappingTests.swift
//
// Locks the desktop grid ↔ ContributionEvent coordinate conversion:
// row inversion (pad row 0 = top, event row 1 = bottom), 1-based
// columns, out-of-range rejection and PadIndex derivation.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

final class PadEventMappingTests: XCTestCase {

    func testCornersMatchDocumentedFormula() {
        // Top-left pad = event row 8, col 1.
        var coords = PadEventMapping.eventCoordinates(
            for: LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(coords?.row, 8)
        XCTAssertEqual(coords?.col, 1)
        // Bottom-right pad = event row 1, col 8.
        coords = PadEventMapping.eventCoordinates(
            for: LaunchpadPad(row: 7, col: 7))
        XCTAssertEqual(coords?.row, 1)
        XCTAssertEqual(coords?.col, 8)
    }

    func testRoundTripAllSixtyFourPads() {
        for row in 0..<8 {
            for col in 0..<8 {
                let pad = LaunchpadPad(row: row, col: col)
                guard let coords = PadEventMapping.eventCoordinates(for: pad)
                else { return XCTFail("nil coords for \(pad)") }
                XCTAssertEqual(
                    PadEventMapping.launchpadPad(
                        row: coords.row, col: coords.col),
                    pad
                )
            }
        }
    }

    func testOffGridPadsRejected() {
        XCTAssertNil(PadEventMapping.eventCoordinates(
            for: LaunchpadPad(row: -1, col: 0)))
        XCTAssertNil(PadEventMapping.eventCoordinates(
            for: LaunchpadPad(row: 8, col: 0)))
        XCTAssertNil(PadEventMapping.eventCoordinates(
            for: LaunchpadPad(row: 0, col: 8)))
    }

    func testOutOfRangeEventCoordinatesRejected() {
        XCTAssertNil(PadEventMapping.launchpadPad(row: 0, col: 1))
        XCTAssertNil(PadEventMapping.launchpadPad(row: 9, col: 1))
        XCTAssertNil(PadEventMapping.launchpadPad(row: 1, col: 0))
        XCTAssertNil(PadEventMapping.launchpadPad(row: 1, col: 9))
    }

    func testPadIndexUsesEventConvention() {
        // Top-left pad → event (row 8, col 1) → rawValue 81.
        XCTAssertEqual(
            PadEventMapping.padIndex(
                for: LaunchpadPad(row: 0, col: 0))?.rawValue, 81)
        // Bottom-right pad → event (row 1, col 8) → rawValue 18.
        XCTAssertEqual(
            PadEventMapping.padIndex(
                for: LaunchpadPad(row: 7, col: 7))?.rawValue, 18)
        XCTAssertNil(PadEventMapping.padIndex(
            for: LaunchpadPad(row: 9, col: 0)))
    }
}
