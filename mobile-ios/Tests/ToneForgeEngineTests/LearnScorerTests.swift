// LearnScorerTests.swift
//
// Pins the Learn scoring contract (redesign Phase 8):
//
//   hit       — pressed chord matches the timeline chord active at
//               press time: same root pitch class (enharmonics
//               collapse) + collapsed quality (maj7/dom7 → maj,
//               min7 → min), with ±150 ms edge grace.
//   accuracy  — hits / presses (0 with no presses).
//   coverage  — fraction of the section's chord windows with ≥1 hit.
//   isPassing — hits > 0, full coverage, accuracy ≥ 0.8.
//   streak    — session-wide consecutive hits (foldStreak reducer).

import XCTest
@testable import ToneForgeEngine

final class LearnScorerTests: XCTestCase {

    /// Dm | Bb | C | F — four 2-second windows, section 0..8.
    private let chords: [ChordEvent] = [
        ChordEvent(start: 0, end: 2, symbol: "Dm"),
        ChordEvent(start: 2, end: 4, symbol: "Bb"),
        ChordEvent(start: 4, end: 6, symbol: "C"),
        ChordEvent(start: 6, end: 8, symbol: "F"),
    ]

    // MARK: - matches

    func testMatchesExactSymbol() {
        XCTAssertTrue(LearnScorer.matches(pressed: "Dm", target: "Dm"))
        XCTAssertFalse(LearnScorer.matches(pressed: "Dm", target: "D"))
        XCTAssertFalse(LearnScorer.matches(pressed: "Em", target: "Dm"))
    }

    func testMatchesCollapsesEnharmonics() {
        XCTAssertTrue(LearnScorer.matches(pressed: "A#", target: "Bb"))
        XCTAssertTrue(LearnScorer.matches(pressed: "Bb", target: "A#"))
        XCTAssertTrue(LearnScorer.matches(pressed: "C#m", target: "Dbm"))
    }

    func testMatchesCollapsesSeventhQualities() {
        // min7 folds into min; maj7 and dom7 fold into maj.
        XCTAssertTrue(LearnScorer.matches(pressed: "Dm", target: "Dm7"))
        XCTAssertTrue(LearnScorer.matches(pressed: "Dm7", target: "Dm"))
        XCTAssertTrue(LearnScorer.matches(pressed: "C", target: "Cmaj7"))
        XCTAssertTrue(LearnScorer.matches(pressed: "C", target: "C7"))
        // Collapse never crosses maj/min.
        XCTAssertFalse(LearnScorer.matches(pressed: "Cm", target: "Cmaj7"))
    }

    func testMatchesRejectsUnparseable() {
        XCTAssertFalse(LearnScorer.matches(pressed: "???", target: "Dm"))
        XCTAssertFalse(LearnScorer.matches(pressed: "Dm", target: ""))
    }

    // MARK: - isHit

    func testHitInsideWindow() {
        XCTAssertTrue(LearnScorer.isHit(
            press: LearnPress(songTime: 1.0, symbol: "Dm"),
            chords: chords
        ))
    }

    func testWrongChordIsMiss() {
        XCTAssertFalse(LearnScorer.isHit(
            press: LearnPress(songTime: 1.0, symbol: "C"),
            chords: chords
        ))
    }

    func testPressInGapIsMiss() {
        // No chord window covers t=10 even with grace.
        XCTAssertFalse(LearnScorer.isHit(
            press: LearnPress(songTime: 10.0, symbol: "Dm"),
            chords: chords
        ))
    }

    func testEdgeGraceExtendsWindows() {
        // Dm ends at 2.0; a Dm press at 2.1 is inside the +0.15 s
        // grace (2.1 < 2.15).
        XCTAssertTrue(LearnScorer.isHit(
            press: LearnPress(songTime: 2.1, symbol: "Dm"),
            chords: chords
        ))
        // Bb starts at 2.0; a Bb press at 1.9 is inside its -grace.
        XCTAssertTrue(LearnScorer.isHit(
            press: LearnPress(songTime: 1.9, symbol: "Bb"),
            chords: chords
        ))
        // Outside the grace on both sides.
        XCTAssertFalse(LearnScorer.isHit(
            press: LearnPress(songTime: 2.5, symbol: "Dm"),
            chords: chords
        ))
    }

    // MARK: - score

    func testPerfectPassIsPassing() {
        let presses = [
            LearnPress(songTime: 1.0, symbol: "Dm"),
            LearnPress(songTime: 3.0, symbol: "Bb"),
            LearnPress(songTime: 5.0, symbol: "C"),
            LearnPress(songTime: 7.0, symbol: "F"),
        ]
        let result = LearnScorer.score(
            presses: presses, chords: chords,
            sectionStart: 0, sectionEnd: 8
        )
        XCTAssertEqual(result.hits, 4)
        XCTAssertEqual(result.misses, 0)
        XCTAssertEqual(result.accuracy, 1.0)
        XCTAssertEqual(result.coverage, 1.0)
        XCTAssertTrue(result.isPassing)
    }

    func testMissedWindowFailsCoverage() {
        // Perfect accuracy, but the F window is never hit.
        let presses = [
            LearnPress(songTime: 1.0, symbol: "Dm"),
            LearnPress(songTime: 3.0, symbol: "Bb"),
            LearnPress(songTime: 5.0, symbol: "C"),
        ]
        let result = LearnScorer.score(
            presses: presses, chords: chords,
            sectionStart: 0, sectionEnd: 8
        )
        XCTAssertEqual(result.accuracy, 1.0)
        XCTAssertEqual(result.coverage, 0.75)
        XCTAssertFalse(result.isPassing)
    }

    func testLowAccuracyFailsEvenWithFullCoverage() {
        // Every window hit once, but 4 extra misses → 4/8 accuracy.
        let presses = [
            LearnPress(songTime: 1.0, symbol: "Dm"),
            LearnPress(songTime: 1.2, symbol: "F"),
            LearnPress(songTime: 3.0, symbol: "Bb"),
            LearnPress(songTime: 3.2, symbol: "F"),
            LearnPress(songTime: 5.0, symbol: "C"),
            LearnPress(songTime: 5.2, symbol: "F"),
            LearnPress(songTime: 7.0, symbol: "F"),
            LearnPress(songTime: 7.2, symbol: "C"),
        ]
        let result = LearnScorer.score(
            presses: presses, chords: chords,
            sectionStart: 0, sectionEnd: 8
        )
        XCTAssertEqual(result.coverage, 1.0)
        XCTAssertEqual(result.accuracy, 0.5)
        XCTAssertFalse(result.isPassing)
    }

    func testEightyPercentAccuracyPasses() {
        // 4 hits covering everything + 1 miss = 0.8 exactly.
        let presses = [
            LearnPress(songTime: 1.0, symbol: "Dm"),
            LearnPress(songTime: 3.0, symbol: "Bb"),
            LearnPress(songTime: 5.0, symbol: "C"),
            LearnPress(songTime: 7.0, symbol: "F"),
            LearnPress(songTime: 7.5, symbol: "C"),
        ]
        let result = LearnScorer.score(
            presses: presses, chords: chords,
            sectionStart: 0, sectionEnd: 8
        )
        XCTAssertEqual(result.accuracy, 0.8)
        XCTAssertTrue(result.isPassing)
    }

    func testNoPressesIsNotPassing() {
        let result = LearnScorer.score(
            presses: [], chords: chords,
            sectionStart: 0, sectionEnd: 8
        )
        XCTAssertEqual(result.hits, 0)
        XCTAssertEqual(result.accuracy, 0)
        XCTAssertFalse(result.isPassing)
    }

    func testWindowsClippedToSection() {
        // Section covers only Dm + Bb; C and F are out of scope, so
        // two hits give full coverage.
        let presses = [
            LearnPress(songTime: 1.0, symbol: "Dm"),
            LearnPress(songTime: 3.0, symbol: "Bb"),
        ]
        let result = LearnScorer.score(
            presses: presses, chords: chords,
            sectionStart: 0, sectionEnd: 4
        )
        XCTAssertEqual(result.coverage, 1.0)
        XCTAssertTrue(result.isPassing)
    }

    func testNoChordWindowsCoverageIsFull() {
        let result = LearnScorer.score(
            presses: [LearnPress(songTime: 1.0, symbol: "Dm")],
            chords: [],
            sectionStart: 0, sectionEnd: 8
        )
        XCTAssertEqual(result.coverage, 1.0)
        // The press can't hit anything, so it still fails.
        XCTAssertEqual(result.hits, 0)
        XCTAssertFalse(result.isPassing)
    }

    // MARK: - foldStreak

    func testStreakFolding() {
        var s = (current: 0, longest: 0)
        s = LearnScorer.foldStreak(current: s.current, longest: s.longest, hit: true)
        s = LearnScorer.foldStreak(current: s.current, longest: s.longest, hit: true)
        s = LearnScorer.foldStreak(current: s.current, longest: s.longest, hit: true)
        XCTAssertEqual(s.current, 3)
        XCTAssertEqual(s.longest, 3)
        s = LearnScorer.foldStreak(current: s.current, longest: s.longest, hit: false)
        XCTAssertEqual(s.current, 0)
        XCTAssertEqual(s.longest, 3)
        s = LearnScorer.foldStreak(current: s.current, longest: s.longest, hit: true)
        XCTAssertEqual(s.current, 1)
        XCTAssertEqual(s.longest, 3)
    }
}
