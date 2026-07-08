// MetronomeTests.swift
//
// Pure-math coverage for MetronomeGrid (Sketch plan, Phase 2): click
// times at multiple BPMs, accent cycles per time signature, and the
// rolling-window walk the Metronome node performs — all without an
// audio engine.

import XCTest
@testable import ToneForgeMobile

final class MetronomeTests: XCTestCase {

    // MARK: - Beat times

    func testSecondsPerBeatAcrossBpms() {
        XCTAssertEqual(MetronomeGrid(bpm: 120, beatsPerBar: 4).secondsPerBeat, 0.5, accuracy: 1e-12)
        XCTAssertEqual(MetronomeGrid(bpm: 60, beatsPerBar: 4).secondsPerBeat, 1.0, accuracy: 1e-12)
        XCTAssertEqual(MetronomeGrid(bpm: 90, beatsPerBar: 3).secondsPerBeat, 2.0 / 3.0, accuracy: 1e-12)
        XCTAssertEqual(MetronomeGrid(bpm: 200, beatsPerBar: 4).secondsPerBeat, 0.3, accuracy: 1e-12)
    }

    func testSongTimeOfBeat() {
        let grid = MetronomeGrid(bpm: 120, beatsPerBar: 4)
        XCTAssertEqual(grid.songTime(ofBeat: 0), 0.0, accuracy: 1e-12)
        XCTAssertEqual(grid.songTime(ofBeat: 7), 3.5, accuracy: 1e-12)
        // Negative beats (count-in window) sit before zero.
        XCTAssertEqual(grid.songTime(ofBeat: -4), -2.0, accuracy: 1e-12)
    }

    // MARK: - beatIndex(onOrAfter:)

    func testBeatIndexOnOrAfter() {
        let grid = MetronomeGrid(bpm: 120, beatsPerBar: 4) // 0.5 s/beat
        XCTAssertEqual(grid.beatIndex(onOrAfter: 0.0), 0)     // exactly on a beat
        XCTAssertEqual(grid.beatIndex(onOrAfter: 0.5), 1)     // exactly on a beat
        XCTAssertEqual(grid.beatIndex(onOrAfter: 0.501), 2)   // just past → next
        XCTAssertEqual(grid.beatIndex(onOrAfter: 0.499), 1)   // just before
        XCTAssertEqual(grid.beatIndex(onOrAfter: -0.75), -1)  // negative window
    }

    func testBeatIndexOnOrAfterToleratesFloatNoise() {
        // 90 BPM: beat = 2/3 s — inexact in binary. Accumulating 3
        // beats of float error must not skip the beat we're exactly on.
        let grid = MetronomeGrid(bpm: 90, beatsPerBar: 3)
        let t3 = grid.songTime(ofBeat: 3)
        XCTAssertEqual(grid.beatIndex(onOrAfter: t3), 3)
    }

    // MARK: - Accents

    func testAccentCycleFourFour() {
        let grid = MetronomeGrid(bpm: 120, beatsPerBar: 4)
        XCTAssertTrue(grid.isAccent(beatIndex: 0))
        XCTAssertFalse(grid.isAccent(beatIndex: 1))
        XCTAssertFalse(grid.isAccent(beatIndex: 3))
        XCTAssertTrue(grid.isAccent(beatIndex: 4))
        XCTAssertTrue(grid.isAccent(beatIndex: 8))
    }

    func testAccentCycleThreeFour() {
        let grid = MetronomeGrid(bpm: 120, beatsPerBar: 3)
        XCTAssertTrue(grid.isAccent(beatIndex: 0))
        XCTAssertFalse(grid.isAccent(beatIndex: 2))
        XCTAssertTrue(grid.isAccent(beatIndex: 3))
        XCTAssertTrue(grid.isAccent(beatIndex: 6))
    }

    func testAccentCycleNegativeBeats() {
        // Count-in runs beats -4…-1 in 4/4: -4 is a bar downbeat.
        let grid = MetronomeGrid(bpm: 120, beatsPerBar: 4)
        XCTAssertTrue(grid.isAccent(beatIndex: -4))
        XCTAssertFalse(grid.isAccent(beatIndex: -3))
        XCTAssertFalse(grid.isAccent(beatIndex: -1))
        XCTAssertTrue(grid.isAccent(beatIndex: -8))
    }

    // MARK: - Window walk

    func testClicksWindowContents() {
        let grid = MetronomeGrid(bpm: 120, beatsPerBar: 4) // 0.5 s/beat
        let clicks = grid.clicks(fromBeatIndex: 0, before: 1.2)
        XCTAssertEqual(clicks.map(\.beatIndex), [0, 1, 2])
        XCTAssertEqual(clicks.map(\.songTime), [0.0, 0.5, 1.0])
        XCTAssertEqual(clicks.map(\.isAccent), [true, false, false])
    }

    func testConsecutiveWindowsNeverOverlapOrGap() {
        // Simulate the rolling refill: each pass resumes from the
        // last scheduled beat + 1. Across many passes at an awkward
        // BPM the union must be every beat exactly once.
        let grid = MetronomeGrid(bpm: 137, beatsPerBar: 4)
        var next = grid.beatIndex(onOrAfter: 0)
        var all: [Int] = []
        var now = 0.0
        for _ in 0..<50 {
            let window = grid.clicks(fromBeatIndex: next, before: now + 1.2)
            all.append(contentsOf: window.map(\.beatIndex))
            if let last = window.last { next = last.beatIndex + 1 }
            now += 0.1 // refill cadence
        }
        XCTAssertFalse(all.isEmpty)
        XCTAssertEqual(all, Array(all[0]...all[all.count - 1]))
        XCTAssertEqual(Set(all).count, all.count, "no beat scheduled twice")
    }

    func testClicksEmptyWhenWindowBeforeNextBeat() {
        let grid = MetronomeGrid(bpm: 60, beatsPerBar: 4) // 1 s/beat
        XCTAssertTrue(grid.clicks(fromBeatIndex: 5, before: 4.9).isEmpty)
    }

    func testClicksAcrossZeroBoundary() {
        // Count-in → downbeat: window spanning negative and positive
        // song time yields the full run including beat 0's accent.
        let grid = MetronomeGrid(bpm: 120, beatsPerBar: 4)
        let clicks = grid.clicks(fromBeatIndex: -2, before: 0.6)
        XCTAssertEqual(clicks.map(\.beatIndex), [-2, -1, 0, 1])
        XCTAssertEqual(clicks[2].isAccent, true)
        XCTAssertEqual(clicks[2].songTime, 0.0, accuracy: 1e-12)
    }

    // MARK: - Accent patterns (redesign Phase 6)

    func testAccentOneAndThree() {
        let grid = MetronomeGrid(bpm: 120, beatsPerBar: 4, accent: .oneAndThree)
        XCTAssertTrue(grid.isAccent(beatIndex: 0))
        XCTAssertFalse(grid.isAccent(beatIndex: 1))
        XCTAssertTrue(grid.isAccent(beatIndex: 2))
        XCTAssertFalse(grid.isAccent(beatIndex: 3))
        XCTAssertTrue(grid.isAccent(beatIndex: 4))
        // Negative (count-in) beats use floored modulo: -4 is beat 1
        // of a bar, -2 is beat 3.
        XCTAssertTrue(grid.isAccent(beatIndex: -4))
        XCTAssertFalse(grid.isAccent(beatIndex: -3))
        XCTAssertTrue(grid.isAccent(beatIndex: -2))
        XCTAssertFalse(grid.isAccent(beatIndex: -1))
    }

    func testAccentEveryBeatAndNone() {
        let every = MetronomeGrid(bpm: 120, beatsPerBar: 4, accent: .everyBeat)
        let none = MetronomeGrid(bpm: 120, beatsPerBar: 4, accent: .none)
        for beat in [-4, -1, 0, 1, 3, 7] {
            XCTAssertTrue(every.isAccent(beatIndex: beat))
            XCTAssertFalse(none.isAccent(beatIndex: beat))
        }
    }

    func testGridEqualityIncludesAccentAndSubdivide() {
        // update(grid:) re-anchors on inequality — accent/subdivide
        // changes must count as a grid change.
        let base = MetronomeGrid(bpm: 120, beatsPerBar: 4)
        XCTAssertNotEqual(base, MetronomeGrid(bpm: 120, beatsPerBar: 4, accent: .everyBeat))
        XCTAssertNotEqual(base, MetronomeGrid(bpm: 120, beatsPerBar: 4, subdivide: true))
        XCTAssertEqual(base, MetronomeGrid(bpm: 120, beatsPerBar: 4, accent: .downbeat, subdivide: false))
    }

    // MARK: - Subdivision click indices (redesign Phase 6)

    func testClickIndexMatchesBeatIndexWithoutSubdivide() {
        let grid = MetronomeGrid(bpm: 120, beatsPerBar: 4)
        for t in [-0.75, 0.0, 0.499, 0.5, 0.501, 3.2] {
            XCTAssertEqual(grid.clickIndex(onOrAfter: t), grid.beatIndex(onOrAfter: t))
        }
        XCTAssertEqual(
            grid.clicks(fromClickIndex: 0, before: 1.2),
            grid.clicks(fromBeatIndex: 0, before: 1.2)
        )
    }

    func testSubdivideDoublesClicks() {
        let grid = MetronomeGrid(bpm: 120, beatsPerBar: 4, subdivide: true) // 0.25 s/click
        XCTAssertEqual(grid.secondsPerClick, 0.25, accuracy: 1e-12)
        let clicks = grid.clicks(fromClickIndex: 0, before: 1.0)
        XCTAssertEqual(clicks.map(\.songTime), [0.0, 0.25, 0.5, 0.75])
        XCTAssertEqual(clicks.map(\.isSubdivision), [false, true, false, true])
        XCTAssertEqual(clicks.map(\.beatIndex), [0, 0, 1, 1])
        // Half-beat clicks are never accented.
        XCTAssertEqual(clicks.map(\.isAccent), [true, false, false, false])
    }

    func testSubdivideAcrossZeroBoundary() {
        // Negative click indices (count-in) map to the right beat via
        // floored division: click -3 is the half-beat of beat -2.
        let grid = MetronomeGrid(bpm: 120, beatsPerBar: 4, subdivide: true)
        let clicks = grid.clicks(fromClickIndex: -3, before: 0.6)
        XCTAssertEqual(clicks.map(\.beatIndex), [-2, -1, -1, 0, 0, 1])
        XCTAssertEqual(
            clicks.map(\.isSubdivision),
            [true, false, true, false, true, false]
        )
        XCTAssertEqual(clicks[3].isAccent, true)
        XCTAssertEqual(clicks[3].songTime, 0.0, accuracy: 1e-12)
        XCTAssertEqual(clicks[0].songTime, -0.75, accuracy: 1e-12)
    }

    func testSubdivideRollingWindowNeverDropsHalfBeats() {
        // The window-boundary case: a refill window that ends between
        // a beat and its half-beat must not lose the half-beat on the
        // next pass. Walking click indices makes the union of windows
        // every click exactly once.
        let grid = MetronomeGrid(bpm: 137, beatsPerBar: 4, subdivide: true)
        var next = grid.clickIndex(onOrAfter: 0)
        var times: [Double] = []
        var subs = 0
        var now = 0.0
        for _ in 0..<50 {
            let window = grid.clicks(fromClickIndex: next, before: now + 1.2)
            times.append(contentsOf: window.map(\.songTime))
            subs += window.filter(\.isSubdivision).count
            next += window.count
            now += 0.1 // refill cadence
        }
        XCTAssertFalse(times.isEmpty)
        for (i, t) in times.enumerated() {
            XCTAssertEqual(t, Double(i) * grid.secondsPerClick, accuracy: 1e-9)
        }
        // Half the clicks are subdivisions.
        XCTAssertEqual(subs, times.count / 2)
    }

    func testClickIndexOnOrAfterWithSubdivide() {
        let grid = MetronomeGrid(bpm: 120, beatsPerBar: 4, subdivide: true) // 0.25 s/click
        XCTAssertEqual(grid.clickIndex(onOrAfter: 0.0), 0)
        XCTAssertEqual(grid.clickIndex(onOrAfter: 0.25), 1)
        XCTAssertEqual(grid.clickIndex(onOrAfter: 0.26), 2)
        XCTAssertEqual(grid.clickIndex(onOrAfter: -0.30), -1)
    }
}
