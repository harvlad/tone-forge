// JamPadGrid12MappingTests.swift
//
// D-022 Phase 5: the Jam tab's 12 performance pads as a window onto
// the 8×8 open-jam grid. Every pad must be a valid grid coordinate
// (so presses round-trip through the ContributionEventBus) and the
// pitches must be the first 12 scale tones from the key root at or
// above E2 — pad 8 exactly one octave over pad 1.

import XCTest
@testable import ToneForgeEngine

final class JamPadGrid12MappingTests: XCTestCase {

    private let aMinor = MusicalKey(root: PitchClass(9), scale: .minor)
    private let cMajor = MusicalKey(root: PitchClass(0), scale: .major)
    private let dMinor = MusicalKey(root: PitchClass(2), scale: .minor)

    private func midis(_ pads: [PadIndex]) -> [Int] {
        pads.map { OpenJamGrid.midi(for: $0)! }
    }

    func testTwelveValidUniqueAscendingPads() {
        for key in [aMinor, cMajor, dMinor, nil] {
            let pads = JamPadGrid12Mapping.pads(key: key)
            XCTAssertEqual(pads.count, 12)
            XCTAssertTrue(pads.allSatisfy(\.isValid))
            XCTAssertEqual(Set(pads).count, 12, "duplicate pads")
            let m = midis(pads)
            XCTAssertEqual(m, m.sorted(), "pads must ascend by pitch")
        }
    }

    func testNoKeyIsChromaticFromE2() {
        let pads = JamPadGrid12Mapping.pads(key: nil)
        XCTAssertEqual(midis(pads), Array(40..<52))
    }

    func testAMinorScaleTones() {
        // First A at/above E2 is A2 (45); natural-minor walk upward.
        let pads = JamPadGrid12Mapping.pads(key: aMinor)
        XCTAssertEqual(
            midis(pads),
            [45, 47, 48, 50, 52, 53, 55, 57, 59, 60, 62, 64]
        )
    }

    func testCMajorScaleTones() {
        // First C at/above E2 is C3 (48).
        let pads = JamPadGrid12Mapping.pads(key: cMajor)
        XCTAssertEqual(
            midis(pads),
            [48, 50, 52, 53, 55, 57, 59, 60, 62, 64, 65, 67]
        )
    }

    func testPadEightIsPadOneAnOctaveUp() {
        for key in [aMinor, cMajor, dMinor] {
            let m = midis(JamPadGrid12Mapping.pads(key: key))
            XCTAssertEqual(m[7], m[0] + 12, "\(key)")
        }
    }

    func testRootPadMatchesKeyRoot() {
        for key in [aMinor, cMajor, dMinor] {
            let pads = JamPadGrid12Mapping.pads(key: key)
            XCTAssertEqual(
                OpenJamGrid.pitchClass(for: pads[0]),
                key.root.rawValue,
                "\(key)"
            )
        }
    }

    func testEveryKeyedPadIsInKeyOnTheGrid() {
        // Round-trip through OpenJamGrid: each mapped pad must carry
        // a degree label (in-key) and light up (color ≠ .off).
        for key in [aMinor, cMajor, dMinor] {
            let grid = OpenJamGrid(key: key)
            for pad in JamPadGrid12Mapping.pads(key: key) {
                guard case .note(_, _, let degreeLabel) =
                    grid.meaning(for: pad)
                else {
                    XCTFail("pad \(pad.rawValue) has no note meaning")
                    continue
                }
                XCTAssertNotNil(
                    degreeLabel,
                    "pad \(pad.rawValue) out of key in \(key)"
                )
                XCTAssertNotEqual(grid.color(for: pad), .off)
            }
        }
    }
}
