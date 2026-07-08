// TimeSigEstimatorTests.swift
//
// D-022 Learn redesign: beats-per-bar from the analysed grids for
// the Learn tab's time-signature chip.

import XCTest
@testable import ToneForgeEngine

final class TimeSigEstimatorTests: XCTestCase {

    /// Regular grid: `beatsPerBar` beats per downbeat.
    private func grid(
        beatsPerBar: Int, bars: Int, beatDur: Double = 0.5
    ) -> (beats: [Double], downbeats: [Double]) {
        let beats = (0..<(beatsPerBar * bars)).map { Double($0) * beatDur }
        let downbeats = (0..<bars).map {
            Double($0 * beatsPerBar) * beatDur
        }
        return (beats, downbeats)
    }

    func testFourFour() {
        let g = grid(beatsPerBar: 4, bars: 8)
        XCTAssertEqual(
            TimeSigEstimator.numerator(beats: g.beats, downbeats: g.downbeats),
            4)
    }

    func testThreeFour() {
        let g = grid(beatsPerBar: 3, bars: 8)
        XCTAssertEqual(
            TimeSigEstimator.numerator(beats: g.beats, downbeats: g.downbeats),
            3)
    }

    func testSixEightStyleGrid() {
        let g = grid(beatsPerBar: 6, bars: 6, beatDur: 0.25)
        XCTAssertEqual(
            TimeSigEstimator.numerator(beats: g.beats, downbeats: g.downbeats),
            6)
    }

    func testModeWinsOverOutliers() {
        // Seven clean 4-beat bars plus one sloppy 5-beat bar (an
        // analysis hiccup) — the mode must still be 4.
        var (beats, downbeats) = grid(beatsPerBar: 4, bars: 8)
        beats.append(downbeats[3] + 0.1)   // extra beat inside bar 4
        beats.sort()
        XCTAssertEqual(
            TimeSigEstimator.numerator(beats: beats, downbeats: downbeats),
            4)
    }

    func testTieBreaksTowardSmallerNumerator() {
        // One 4-beat bar and one 8-beat bar: 50/50 → prefer 4.
        let beats = (0..<12).map { Double($0) * 0.5 }
        let downbeats = [0.0, 2.0, 6.0]     // 4 beats, then 8 beats
        XCTAssertEqual(
            TimeSigEstimator.numerator(beats: beats, downbeats: downbeats),
            4)
    }

    func testJitteredDownbeatStillCounts() {
        // Downbeats detected a hair before their beats must not
        // steal a beat from the previous bar.
        let g = grid(beatsPerBar: 4, bars: 6)
        let jittered = g.downbeats.map { $0 - 0.0005 }
        XCTAssertEqual(
            TimeSigEstimator.numerator(beats: g.beats, downbeats: jittered),
            4)
    }

    func testInsufficientDataReturnsNil() {
        XCTAssertNil(TimeSigEstimator.numerator(beats: [], downbeats: [0, 2]))
        XCTAssertNil(TimeSigEstimator.numerator(
            beats: [0, 0.5, 1], downbeats: [0]))
        XCTAssertNil(TimeSigEstimator.numerator(beats: [], downbeats: []))
    }
}
