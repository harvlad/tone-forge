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
}
