// ChordLaneSelectionTests.swift
//
// The desktop port of jam.js `_richestChordLane`: the native chord
// surfaces must follow the RICHEST per-stem lane, not the sparse
// legacy "other" lane. Pins the coverage rule, the vocals/drums
// exclusion, deterministic tie-break, and the legacy fallback.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

final class ChordLaneSelectionTests: XCTestCase {

    private func ch(_ start: Double, _ end: Double, _ symbol: String = "C") -> ChordEvent {
        ChordEvent(start: start, end: end, symbol: symbol)
    }

    func testFallsBackToFlatLaneWhenNoPerStemLanes() {
        let flat = [ch(0, 4, "C"), ch(4, 8, "G")]
        let timeline = BundleTimeline(chords: flat, chordsByStem: nil)
        XCTAssertEqual(timeline.richestChordLane, flat)

        let empty = BundleTimeline(chords: flat, chordsByStem: [:])
        XCTAssertEqual(empty.richestChordLane, flat)
    }

    func testPicksRichestByCoverageNotCount() {
        // "other": 5 tiny slivers (2.5s total). "guitar": 2 long
        // regions (16s total). Coverage seconds must win over count.
        let other = [ch(0, 0.5), ch(1, 1.5), ch(2, 2.5), ch(3, 3.5), ch(4, 4.5)]
        let guitar = [ch(0, 8, "C#"), ch(8, 16, "A#m")]
        let timeline = BundleTimeline(
            chords: other,
            chordsByStem: ["other": other, "guitar": guitar]
        )
        XCTAssertEqual(timeline.richestChordLane, guitar)
    }

    func testExcludesVocalsAndDrumsLanes() {
        // vocals lane has the most coverage but must be ignored — it
        // traces the monophonic melody, not harmony.
        let vocals = [ch(0, 100, "C")]
        let guitar = [ch(0, 20, "C#"), ch(20, 40, "F#")]
        let drums = [ch(0, 50, "N")]
        let timeline = BundleTimeline(
            chords: [ch(0, 4)],
            chordsByStem: ["vocals": vocals, "guitar": guitar, "drums": drums]
        )
        XCTAssertEqual(timeline.richestChordLane, guitar)
    }

    func testFallsBackWhenOnlyNonHarmonicLanesExist() {
        let flat = [ch(0, 4, "C")]
        let timeline = BundleTimeline(
            chords: flat,
            chordsByStem: ["vocals": [ch(0, 100)], "drums": [ch(0, 100)]]
        )
        XCTAssertEqual(timeline.richestChordLane, flat)
    }

    func testTieBreaksDeterministicallyByName() {
        // Equal coverage → the alphabetically-first stem wins, matching
        // web's `Object.keys(byStem).sort()`.
        let bass = [ch(0, 10, "E")]
        let guitar = [ch(0, 10, "C#")]
        let timeline = BundleTimeline(
            chords: [],
            chordsByStem: ["guitar": guitar, "bass": bass]
        )
        // "bass" < "guitar" and both cover 10s → bass wins.
        XCTAssertEqual(timeline.richestChordLane, bass)
    }
}
