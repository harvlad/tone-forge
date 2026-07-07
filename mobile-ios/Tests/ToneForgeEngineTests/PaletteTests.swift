// PaletteTests.swift
//
// Locks the RGB values to the exact numbers launchpad.js emits. Any
// palette drift between the web and mobile clients would show up as
// pads painting different colors on the DOM mirror vs the touchscreen,
// so this test file exists to catch that in CI.

import XCTest
@testable import ToneForgeEngine

final class PaletteTests: XCTestCase {

    // MARK: - Song family (FAMILY_RGB)

    func testSongFamilyMajor() {
        XCTAssertEqual(Palette.songFamily(.major), PadColor(127, 80, 0))
    }

    func testSongFamilyMinor() {
        XCTAssertEqual(Palette.songFamily(.minor), PadColor(0, 60, 127))
    }

    func testSongFamilyDom7() {
        XCTAssertEqual(Palette.songFamily(.dom7), PadColor(127, 20, 20))
    }

    func testSongFamilyDim() {
        XCTAssertEqual(Palette.songFamily(.dim), PadColor(80, 0, 80))
    }

    func testSongFamilyAug() {
        XCTAssertEqual(Palette.songFamily(.aug), PadColor(20, 120, 20))
    }

    func testSongFamilyOther() {
        XCTAssertEqual(Palette.songFamily(.other), PadColor(60, 60, 60))
    }

    // MARK: - Open-jam constants

    func testOpenJamRoot() {
        XCTAssertEqual(Palette.openJamRoot, PadColor(127, 100, 0))
    }

    func testOpenJamChordTone() {
        XCTAssertEqual(Palette.openJamChordTone, PadColor(0, 110, 110))
    }

    func testOpenJamChromaticDim() {
        XCTAssertEqual(Palette.openJamChromaticDim, PadColor(4, 4, 4))
    }

    // MARK: - Degree base

    func testOpenJamDegreeBaseValues() {
        XCTAssertEqual(Palette.openJamDegreeBase(degree: 1), PadColor(127, 90, 0))
        XCTAssertEqual(Palette.openJamDegreeBase(degree: 2), PadColor(40, 40, 90))
        XCTAssertEqual(Palette.openJamDegreeBase(degree: 3), PadColor(40, 40, 90))
        XCTAssertEqual(Palette.openJamDegreeBase(degree: 4), PadColor(0, 40, 90))
        XCTAssertEqual(Palette.openJamDegreeBase(degree: 5), PadColor(10, 80, 20))
        XCTAssertEqual(Palette.openJamDegreeBase(degree: 6), PadColor(40, 40, 90))
        XCTAssertEqual(Palette.openJamDegreeBase(degree: 7), PadColor(80, 10, 10))
    }

    func testOpenJamDegreeBaseOutOfRangeFallsBackToI() {
        // JS falls back to DEGREE_RGB[0] for anything out of range.
        XCTAssertEqual(Palette.openJamDegreeBase(degree: 0), PadColor(127, 90, 0))
        XCTAssertEqual(Palette.openJamDegreeBase(degree: 99), PadColor(127, 90, 0))
        XCTAssertEqual(Palette.openJamDegreeBase(degree: -1), PadColor(127, 90, 0))
    }

    // MARK: - Dimmed = base × 0.6

    func testOpenJamDegreeDimmed() {
        // I = (127, 90, 0) × 0.6 = (76.2, 54, 0) → rounds to (76, 54, 0)
        XCTAssertEqual(Palette.openJamDegreeDimmed(degree: 1), PadColor(76, 54, 0))
        // V = (10, 80, 20) × 0.6 = (6, 48, 12)
        XCTAssertEqual(Palette.openJamDegreeDimmed(degree: 5), PadColor(6, 48, 12))
    }

    // MARK: - scaled utility

    func testScaledHalf() {
        XCTAssertEqual(Palette.scaled(PadColor(100, 100, 100), 0.5), PadColor(50, 50, 50))
    }

    func testScaledZero() {
        XCTAssertEqual(Palette.scaled(PadColor(127, 127, 127), 0.0), PadColor(0, 0, 0))
    }

    func testScaledClampsAtUInt8Max() {
        // Factor > 1 could overflow — the clamp keeps us in bounds.
        let big = Palette.scaled(PadColor(200, 200, 200), 2.0)
        XCTAssertEqual(big, PadColor(255, 255, 255))
    }
}
