// ChordPadControllerTests.swift
//
// Trigger-seam state machine for the Chord Pads surface (Phase 12):
// momentary press visuals, latch toggling (no retrigger on unlatch),
// octave clamping, and the song-less C-major key fallback. PadSynth
// triggers are safe engine-less (postTrigger no-ops without render).

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class ChordPadControllerTests: XCTestCase {

    /// The controller holds `unowned let app`, so the test case must
    /// keep the AppState alive for the whole test (same pattern as
    /// LearnSessionControllerTests).
    private var app: AppState!

    override func setUp() async throws {
        try await super.setUp()
        app = AppState()
    }

    override func tearDown() async throws {
        app = nil
        try await super.tearDown()
    }

    private var controller: ChordPadController { app.chordPadController }

    func testKeyFallsBackToCMajorWithoutSong() {
        XCTAssertEqual(
            controller.key,
            MusicalKey(root: PitchClass(0), scale: .major)
        )
        XCTAssertEqual(controller.keyLabel, "C Major")
        XCTAssertEqual(controller.cells.count, 16)
    }

    func testMomentaryHoldsWhileDown() {
        controller.padDown(index: 0)
        XCTAssertEqual(controller.heldCells, [0])
        XCTAssertTrue(controller.latchedCells.isEmpty)
        controller.padUp(index: 0)
        XCTAssertTrue(controller.heldCells.isEmpty)
    }

    func testLatchTogglesAndSurvivesPadUp() {
        controller.triggerMode = .latch
        controller.padDown(index: 5)
        controller.padUp(index: 5)
        XCTAssertEqual(controller.latchedCells, [5])
        // Second press unlatches.
        controller.padDown(index: 5)
        controller.padUp(index: 5)
        XCTAssertTrue(controller.latchedCells.isEmpty)
    }

    func testClearLatchesEmptiesBothSets() {
        controller.triggerMode = .latch
        controller.padDown(index: 1)
        controller.triggerMode = .momentary
        controller.padDown(index: 2)
        controller.clearLatches()
        XCTAssertTrue(controller.heldCells.isEmpty)
        XCTAssertTrue(controller.latchedCells.isEmpty)
    }

    func testOctaveShiftClampsToPlusMinusThree() {
        controller.setOctaveShift(9)
        XCTAssertEqual(controller.octaveShift, 3)
        controller.setOctaveShift(-9)
        XCTAssertEqual(controller.octaveShift, -3)
        controller.setOctaveShift(1)
        XCTAssertEqual(controller.octaveShift, 1)
    }

    func testOutOfRangeIndexIsIgnored() {
        controller.padDown(index: 99)
        XCTAssertTrue(controller.heldCells.isEmpty)
        XCTAssertTrue(controller.latchedCells.isEmpty)
    }
}
