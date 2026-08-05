// BeatClockTests.swift
//
// Beat/bar phase + quantize boundary. Covers the marker-array path
// (drift-accurate), the tempo-grid fallback, and the no-timing case.

import XCTest
@testable import ToneForgeEngine

final class BeatClockTests: XCTestCase {

    // MARK: - Grid fallback (tempo only)

    func testBeatPhaseFromTempoGrid() {
        let c = BeatClock(tempoBpm: 120)   // beat = 0.5 s
        XCTAssertEqual(c.beatDuration!, 0.5, accuracy: 1e-9)
        XCTAssertEqual(c.beatPhase(at: 0.0)!, 0.0, accuracy: 1e-9)
        XCTAssertEqual(c.beatPhase(at: 0.25)!, 0.5, accuracy: 1e-9)
        XCTAssertEqual(c.beatPhase(at: 0.5)!, 0.0, accuracy: 1e-9)
        XCTAssertEqual(c.beatPhase(at: 0.75)!, 0.5, accuracy: 1e-9)
    }

    func testBarPhaseFromTempoGrid() {
        let c = BeatClock(tempoBpm: 120, beatsPerBar: 4)  // bar = 2 s
        XCTAssertEqual(c.barPhase(at: 0.0)!, 0.0, accuracy: 1e-9)
        XCTAssertEqual(c.barPhase(at: 1.0)!, 0.5, accuracy: 1e-9)
        XCTAssertEqual(c.barPhase(at: 2.0)!, 0.0, accuracy: 1e-9)
    }

    // MARK: - Marker arrays (source of truth, absorbs drift)

    func testBeatPhaseFromBeatsAbsorbsDrift() {
        // Uneven beats: 0.0, 0.4 (fast), 1.0 (slow).
        let c = BeatClock(tempoBpm: 120, beats: [0.0, 0.4, 1.0])
        // Inside [0.0, 0.4): span 0.4 → t=0.2 is phase 0.5
        XCTAssertEqual(c.beatPhase(at: 0.2)!, 0.5, accuracy: 1e-9)
        // Inside [0.4, 1.0): span 0.6 → t=0.7 is phase 0.5
        XCTAssertEqual(c.beatPhase(at: 0.7)!, 0.5, accuracy: 1e-9)
    }

    func testBeatPhaseOutsideMarkersFallsToGrid() {
        // t beyond last beat → grid fallback (beat=0.5): t=2.25 → phase 0.5
        let c = BeatClock(tempoBpm: 120, beats: [0.0, 0.5, 1.0])
        XCTAssertEqual(c.beatPhase(at: 2.25)!, 0.5, accuracy: 1e-9)
    }

    func testBarPhaseFromDownbeats() {
        let c = BeatClock(tempoBpm: 120, downbeats: [0.0, 2.0, 4.0])
        XCTAssertEqual(c.barPhase(at: 1.0)!, 0.5, accuracy: 1e-9)
        XCTAssertEqual(c.barPhase(at: 3.0)!, 0.5, accuracy: 1e-9)
    }

    // MARK: - Quantization

    func testNextBoundaryPerBeat() {
        let c = BeatClock(tempoBpm: 120)  // beat 0.5, anchor 0
        XCTAssertEqual(c.nextBoundary(after: 0.1)!, 0.5, accuracy: 1e-9)
        XCTAssertEqual(c.nextBoundary(after: 0.5)!, 0.5, accuracy: 1e-9)  // on the line
        XCTAssertEqual(c.nextBoundary(after: 0.6)!, 1.0, accuracy: 1e-9)
    }

    func testNextBoundarySubdivisionAndAnchor() {
        // 1/8 grid (0.25 s) anchored on first beat 0.3.
        let c = BeatClock(tempoBpm: 120, beats: [0.3, 0.8, 1.3])
        XCTAssertEqual(c.nextBoundary(after: 0.2, subdivisionBeats: 0.5)!, 0.3, accuracy: 1e-9)
        XCTAssertEqual(c.nextBoundary(after: 0.4, subdivisionBeats: 0.5)!, 0.55, accuracy: 1e-9)
    }

    // MARK: - No timing

    func testNoTimingReturnsNil() {
        let c = BeatClock()
        XCTAssertFalse(c.hasTiming)
        XCTAssertNil(c.beatPhase(at: 1.0))
        XCTAssertNil(c.barPhase(at: 1.0))
        XCTAssertNil(c.nextBoundary(after: 1.0))
        XCTAssertNil(c.beatDuration)
    }

    func testHasTimingWithArraysOnly() {
        let c = BeatClock(beats: [0.0, 0.5])
        XCTAssertTrue(c.hasTiming)
        // No tempo → beat phase still works off markers…
        XCTAssertEqual(c.beatPhase(at: 0.25)!, 0.5, accuracy: 1e-9)
        // …but quantize needs a tempo.
        XCTAssertNil(c.nextBoundary(after: 0.1))
    }
}
