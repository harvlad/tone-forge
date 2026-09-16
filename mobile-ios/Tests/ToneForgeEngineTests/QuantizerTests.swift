// QuantizerTests.swift
//
// Pure-logic tests for `Quantizer.nextQuantized`. Covers:
//
//   - `.off` returns t unchanged.
//   - Grace window at 0 seconds (exactly on boundary) and at
//     `graceSeconds - ε` (still within grace).
//   - Grace window at `graceSeconds + ε` (past grace → snaps forward).
//   - `.quarter` with bundle-provided beats.
//   - `.bar` with bundle-provided downbeats.
//   - `.eighth` interpolates halfway between beats.
//   - `.half` follows the anchor when a first-downbeat is known.
//   - `.phrase` snaps to next section boundary.
//   - Missing beats + tempoBpm → synthesised grid at the correct
//     interval.
//   - Missing beats + missing tempo → degrades to t (no fabricated
//     grid).
//   - Past the last known boundary → extrapolated forward at tempo
//     from that boundary (padengine.js:409-414 / :1029-1036); returns
//     t only when there is no tempo to extrapolate with.
//   - Before the first grid entry → waits for grid[0] (web
//     nextGridTimeSec parity) — never a synthetic substitute grid.

import XCTest
@testable import ToneForgeEngine

final class QuantizerTests: XCTestCase {

    // MARK: - .off

    func testOffReturnsTUnchanged() {
        let out = Quantizer.nextQuantized(
            songSeconds: 3.14,
            mode: .off,
            beats: [0, 1, 2, 3, 4],
            downbeats: [0, 4],
            sections: [],
            tempoBpm: 120
        )
        XCTAssertEqual(out, 3.14, accuracy: 1e-9)
    }

    // MARK: - Grace window

    func testGraceAtExactBoundaryReturnsT() {
        // Tapping exactly on a beat should play now, not snap forward.
        let out = Quantizer.nextQuantized(
            songSeconds: 1.0,
            mode: .quarter,
            beats: [0, 1, 2, 3],
            downbeats: [0],
            sections: [],
            tempoBpm: nil,
            graceSeconds: 0.08
        )
        XCTAssertEqual(out, 1.0, accuracy: 1e-9)
    }

    func testGraceWithinWindowReturnsT() {
        // 40 ms after a beat → still within 80 ms grace.
        let out = Quantizer.nextQuantized(
            songSeconds: 1.04,
            mode: .quarter,
            beats: [0, 1, 2, 3],
            downbeats: [0],
            sections: [],
            tempoBpm: nil,
            graceSeconds: 0.08
        )
        XCTAssertEqual(out, 1.04, accuracy: 1e-9)
    }

    func testJustPastGraceSnapsForward() {
        // 100 ms after beat 1 (past grace) → snaps to beat 2.
        let out = Quantizer.nextQuantized(
            songSeconds: 1.10,
            mode: .quarter,
            beats: [0, 1, 2, 3],
            downbeats: [0],
            sections: [],
            tempoBpm: nil,
            graceSeconds: 0.08
        )
        XCTAssertEqual(out, 2.0, accuracy: 1e-9)
    }

    // MARK: - Modes with bundle-provided grid

    func testQuarterSnapsToNextBeat() {
        let out = Quantizer.nextQuantized(
            songSeconds: 1.5,
            mode: .quarter,
            beats: [0, 1, 2, 3, 4],
            downbeats: [0, 4],
            sections: [],
            tempoBpm: nil
        )
        XCTAssertEqual(out, 2.0, accuracy: 1e-9)
    }

    func testBarSnapsToNextDownbeat() {
        let out = Quantizer.nextQuantized(
            songSeconds: 1.5,
            mode: .bar,
            beats: [0, 1, 2, 3, 4, 5, 6, 7, 8],
            downbeats: [0, 4, 8],
            sections: [],
            tempoBpm: nil
        )
        XCTAssertEqual(out, 4.0, accuracy: 1e-9)
    }

    func testEighthInterpolatesHalfwayBetweenBeats() {
        // Beat=1.0, next beat=2.0. Half point = 1.5. Tap at 1.2 (past
        // grace of 80 ms) → snap to 1.5.
        let out = Quantizer.nextQuantized(
            songSeconds: 1.2,
            mode: .eighth,
            beats: [0, 1, 2, 3],
            downbeats: [0],
            sections: [],
            tempoBpm: nil
        )
        XCTAssertEqual(out, 1.5, accuracy: 1e-9)
    }

    func testHalfAnchorsOnFirstDownbeat() {
        // First downbeat at beat 1 (t=1.0) → half-grid = [1, 3, 5, ...].
        // Tap at 1.5 → snap to 3.0.
        let out = Quantizer.nextQuantized(
            songSeconds: 1.5,
            mode: .half,
            beats: [0, 1, 2, 3, 4, 5],
            downbeats: [1],
            sections: [],
            tempoBpm: nil
        )
        XCTAssertEqual(out, 3.0, accuracy: 1e-9)
    }

    func testPhraseSnapsToNextSectionBoundary() {
        let sections = [
            SectionEvent(start: 0, end: 8, label: "Intro"),
            SectionEvent(start: 8, end: 24, label: "Verse"),
            SectionEvent(start: 24, end: 40, label: "Chorus"),
        ]
        let out = Quantizer.nextQuantized(
            songSeconds: 5.0,
            mode: .phrase,
            beats: [],
            downbeats: [],
            sections: sections,
            tempoBpm: nil
        )
        XCTAssertEqual(out, 8.0, accuracy: 1e-9)
    }

    // MARK: - Synthetic grid fallback

    func testQuarterSyntheticFromTempoBpm() {
        // 120 BPM → beat interval 0.5 s. Grid = [0, 0.5, 1.0, 1.5, ...].
        // Tap at 0.6 → past grace, snap to 1.0.
        let out = Quantizer.nextQuantized(
            songSeconds: 0.6,
            mode: .quarter,
            beats: [],
            downbeats: [],
            sections: [],
            tempoBpm: 120
        )
        XCTAssertEqual(out, 1.0, accuracy: 1e-9)
    }

    func testBarSyntheticFromTempoBpmIs4Beats() {
        // 120 BPM → bar interval = 4 * 0.5 = 2.0 s. Grid = [0, 2, 4, ...].
        // Tap at 1.0 (well past grace of 0.08) → snap to 2.0.
        let out = Quantizer.nextQuantized(
            songSeconds: 1.0,
            mode: .bar,
            beats: [],
            downbeats: [],
            sections: [],
            tempoBpm: 120
        )
        XCTAssertEqual(out, 2.0, accuracy: 1e-9)
    }

    func testEighthSyntheticFromTempoBpm() {
        // 120 BPM → eighth = 0.25 s. Grid = [0, 0.25, 0.5, 0.75, ...].
        // Tap at 0.4 → snap to 0.5.
        let out = Quantizer.nextQuantized(
            songSeconds: 0.4,
            mode: .eighth,
            beats: [],
            downbeats: [],
            sections: [],
            tempoBpm: 120
        )
        XCTAssertEqual(out, 0.5, accuracy: 1e-9)
    }

    func testMissingBeatsAndTempoDegradesToT() {
        let out = Quantizer.nextQuantized(
            songSeconds: 2.3,
            mode: .quarter,
            beats: [],
            downbeats: [],
            sections: [],
            tempoBpm: nil
        )
        XCTAssertEqual(out, 2.3, accuracy: 1e-9)
    }

    // MARK: - Edge cases

    /// Without a tempo there is nothing to extrapolate with — the tail
    /// of the analysed timeline keeps the play-now behavior.
    func testPastLastBoundaryWithoutTempoReturnsT() {
        let out = Quantizer.nextQuantized(
            songSeconds: 10.0,
            mode: .quarter,
            beats: [0, 1, 2, 3, 4],
            downbeats: [0, 4],
            sections: [],
            tempoBpm: nil
        )
        XCTAssertEqual(out, 10.0, accuracy: 1e-9)
    }

    // MARK: - Extrapolation past the last analysed boundary (FIX: web
    // parity — padengine.js:1029-1036 extrapolates at tempo from the
    // last real grid time; instant fire dropped late-song presses off
    // the grid the ringing loops were launched on)

    func testPastLastBeatExtrapolatesAtTempo() {
        // 120 BPM → beat 0.5 s, last beat 4.0. t = 10.1: phase 0.1 past
        // the extrapolated 10.0 boundary (> 0.08 grace) → next is 10.5.
        let out = Quantizer.nextQuantized(
            songSeconds: 10.1,
            mode: .quarter,
            beats: [0, 1, 2, 3, 4],
            downbeats: [0, 4],
            sections: [],
            tempoBpm: 120
        )
        XCTAssertEqual(out, 10.5, accuracy: 1e-9)
    }

    func testPastLastDownbeatExtrapolatesBarUnit() {
        // 120 BPM → bar 2.0 s, last downbeat 8.0. t = 9.0 → 10.0.
        let out = Quantizer.nextQuantized(
            songSeconds: 9.0,
            mode: .bar,
            beats: [0, 1, 2, 3, 4, 5, 6, 7, 8],
            downbeats: [0, 4, 8],
            sections: [],
            tempoBpm: 120
        )
        XCTAssertEqual(out, 10.0, accuracy: 1e-9)
    }

    func testExtrapolatedBoundaryHonorsGrace() {
        // t = 10.05: within 0.08 s past the extrapolated boundary at
        // 10.0 (last downbeat 8.0 + one 2.0 s bar) → play now.
        let out = Quantizer.nextQuantized(
            songSeconds: 10.05,
            mode: .bar,
            beats: [0, 1, 2, 3, 4, 5, 6, 7, 8],
            downbeats: [0, 4, 8],
            sections: [],
            tempoBpm: 120
        )
        XCTAssertEqual(out, 10.05, accuracy: 1e-9)
    }

    /// `.phrase` has no tempo period — no extrapolation, tail plays now.
    func testPhrasePastLastSectionPlaysNow() {
        let sections = [
            SectionEvent(start: 0, end: 8, label: "Intro"),
            SectionEvent(start: 8, end: 24, label: "Verse"),
        ]
        let out = Quantizer.nextQuantized(
            songSeconds: 30.0,
            mode: .phrase,
            beats: [],
            downbeats: [],
            sections: sections,
            tempoBpm: 120
        )
        XCTAssertEqual(out, 30.0, accuracy: 1e-9)
    }

    // MARK: - Pre-first-boundary (FIX: web parity — nextGridTimeSec
    // returns grid[0] for a songNow before the first entry; the old
    // iOS-only synthetic substitute fired intro taps on a lattice the
    // song never plays)

    /// Just-before-first-beat taps snap to beats[0].
    func testJustBeforeFirstBoundarySnapsToFirst() {
        let out = Quantizer.nextQuantized(
            songSeconds: -0.1,
            mode: .quarter,
            beats: [0, 1, 2, 3],
            downbeats: [0],
            sections: [],
            tempoBpm: nil
        )
        XCTAssertEqual(out, 0.0, accuracy: 1e-9)
    }

    /// A tap during the intro — before the first analysed downbeat —
    /// WAITS for that first real downbeat, even when a tempo is known
    /// (the synthetic grid is only for bundles with no grid arrays).
    func testPreFirstDownbeatWaitsForFirstRealEntry() {
        let out = Quantizer.nextQuantized(
            songSeconds: 1.0,
            mode: .bar,
            beats: [5.0, 5.5, 6.0, 6.5],
            downbeats: [5.0, 7.0],
            sections: [],
            tempoBpm: 120
        )
        XCTAssertEqual(out, 5.0, accuracy: 1e-9,
                       "intro tap must arm until the music's real downbeat")
    }

    func testPreFirstBeatWaitsForFirstRealEntry() {
        let out = Quantizer.nextQuantized(
            songSeconds: 1.0,
            mode: .quarter,
            beats: [5.0, 5.5, 6.0, 6.5],
            downbeats: [5.0],
            sections: [],
            tempoBpm: 120
        )
        XCTAssertEqual(out, 5.0, accuracy: 1e-9)
    }

    func testZeroBpmDoesNotFabricateGrid() {
        let out = Quantizer.nextQuantized(
            songSeconds: 1.0,
            mode: .quarter,
            beats: [],
            downbeats: [],
            sections: [],
            tempoBpm: 0
        )
        XCTAssertEqual(out, 1.0, accuracy: 1e-9)
    }
}
