// LoopAndBarMathTests.swift
//
// Coverage for the Phase-5 loop + bar-math engine pieces:
//   - LoopRegion: half-open membership, wrap only on a fresh
//     crossing (scrubs far past never snap back), degenerate
//     regions rejected;
//   - BarMath: downbeat-driven and tempo-fallback bar indices,
//     barsUntil, barCount;
//   - SectionBars: the numbered bar strip with a chord per bar
//     (4-bar chords → 1:Dm 5:Bb 9:C 13:F).

import XCTest
@testable import ToneForgeEngine

final class LoopRegionTests: XCTestCase {

    func testDegenerateRegionsRejected() {
        XCTAssertNil(LoopRegion(startSec: 10, endSec: 10))
        XCTAssertNil(LoopRegion(startSec: 10, endSec: 5))
    }

    func testContainsIsHalfOpen() {
        let r = LoopRegion(startSec: 10, endSec: 20)!
        XCTAssertTrue(r.contains(10))
        XCTAssertTrue(r.contains(19.999))
        XCTAssertFalse(r.contains(20))
        XCTAssertFalse(r.contains(9.999))
        XCTAssertEqual(r.lengthSec, 10)
    }

    func testWrapsJustAfterCrossingEnd() {
        let r = LoopRegion(startSec: 10, endSec: 20)!
        // A 30 Hz tick lands ~33 ms past the boundary.
        XCTAssertEqual(r.wrapTarget(now: 20.033), 10)
        // Exactly at the boundary counts as crossed (half-open).
        XCTAssertEqual(r.wrapTarget(now: 20), 10)
    }

    func testInsideRegionDoesNotWrap() {
        let r = LoopRegion(startSec: 10, endSec: 20)!
        XCTAssertNil(r.wrapTarget(now: 15))
        XCTAssertNil(r.wrapTarget(now: 10))
    }

    func testScrubFarPastDoesNotSnapBack() {
        let r = LoopRegion(startSec: 10, endSec: 20)!
        XCTAssertNil(r.wrapTarget(now: 21.5))   // beyond overshoot
        XCTAssertNil(r.wrapTarget(now: 45))
        XCTAssertNil(r.wrapTarget(now: 5))      // before the region
    }

    func testMaxOvershootIsConfigurable() {
        let r = LoopRegion(startSec: 0, endSec: 8)!
        XCTAssertEqual(r.wrapTarget(now: 9.5, maxOvershoot: 2), 0)
        XCTAssertNil(r.wrapTarget(now: 9.5, maxOvershoot: 1))
    }
}

final class BarMathTests: XCTestCase {

    // Downbeats every 2 s (120 bpm, 4/4).
    private let downbeats: [Double] = [0, 2, 4, 6, 8, 10, 12, 14]

    func testBarIndexFromDownbeats() {
        XCTAssertEqual(BarMath.barIndex(at: 0, downbeats: downbeats), 0)
        XCTAssertEqual(BarMath.barIndex(at: 1.9, downbeats: downbeats), 0)
        XCTAssertEqual(BarMath.barIndex(at: 2, downbeats: downbeats), 1)
        XCTAssertEqual(BarMath.barIndex(at: 15, downbeats: downbeats), 7)
    }

    func testBarIndexClampsBeforeFirstDownbeat() {
        let late: [Double] = [1.5, 3.5]
        XCTAssertEqual(BarMath.barIndex(at: 0.2, downbeats: late), 0)
    }

    func testBarIndexTempoFallback() {
        // 120 bpm 4/4 → 2 s bars.
        XCTAssertEqual(
            BarMath.barIndex(at: 5, downbeats: [], tempoBpm: 120), 2
        )
        XCTAssertEqual(
            BarMath.barIndex(at: 0, downbeats: [], tempoBpm: 120), 0
        )
    }

    func testBarIndexNilWithoutTimingData() {
        XCTAssertNil(BarMath.barIndex(at: 5, downbeats: []))
        XCTAssertNil(BarMath.barIndex(at: 5, downbeats: [], tempoBpm: 0))
    }

    func testBarsUntil() {
        // From t=1 (bar 0) to a chord landing on the downbeat at
        // t=8 (bar 4) → 4 bars ahead.
        XCTAssertEqual(
            BarMath.barsUntil(8, from: 1, downbeats: downbeats), 4
        )
        // Behind or same bar → 0, never negative.
        XCTAssertEqual(
            BarMath.barsUntil(1, from: 8, downbeats: downbeats), 0
        )
        XCTAssertEqual(
            BarMath.barsUntil(1.5, from: 0.5, downbeats: downbeats), 0
        )
    }

    func testBarCount() {
        XCTAssertEqual(
            BarMath.barCount(start: 0, end: 8, downbeats: downbeats), 4
        )
        // Tempo fallback: 32 s of 2 s bars.
        XCTAssertEqual(
            BarMath.barCount(start: 0, end: 32, downbeats: [], tempoBpm: 120),
            16
        )
        XCTAssertEqual(BarMath.barCount(start: 0, end: 8, downbeats: []), 0)
    }

    func testRegionStartAlwaysOpensBarOne() {
        // Section starts between downbeats — the pickup still counts
        // as bar 1, and the next downbeat opens bar 2.
        let n = BarMath.barCount(start: 1, end: 6, downbeats: downbeats)
        XCTAssertEqual(n, 3) // [1..2), [2..4), [4..6)
    }
}

final class SectionBarsTests: XCTestCase {

    func testFourBarChordsProduceMockupStrip() {
        // 120 bpm 4/4 → 2 s bars; 32 s section; chords change every
        // 4 bars: Dm, Bb, C, F.
        let section = SectionEvent(start: 0, end: 32, label: "verse")
        let downbeats = stride(from: 0.0, to: 32.0, by: 2.0).map { $0 }
        let chords = [
            ChordEvent(start: 0,  end: 8,  symbol: "Dm"),
            ChordEvent(start: 8,  end: 16, symbol: "Bb"),
            ChordEvent(start: 16, end: 24, symbol: "C"),
            ChordEvent(start: 24, end: 32, symbol: "F"),
        ]
        let bars = SectionBars.bars(
            section: section, downbeats: downbeats, chords: chords
        )

        XCTAssertEqual(bars.count, 16)
        XCTAssertEqual(bars.map(\.number), Array(1...16))
        XCTAssertEqual(bars[0].chordSymbol, "Dm")
        XCTAssertEqual(bars[4].chordSymbol, "Bb")   // bar 5
        XCTAssertEqual(bars[8].chordSymbol, "C")    // bar 9
        XCTAssertEqual(bars[12].chordSymbol, "F")   // bar 13
        XCTAssertEqual(bars[3].chordSymbol, "Dm")   // still bar 4
        // Bars tile the section exactly.
        XCTAssertEqual(bars.first?.startSec, 0)
        XCTAssertEqual(bars.last?.endSec, 32)
    }

    func testTempoFallbackStrip() {
        let section = SectionEvent(start: 10, end: 18, label: nil)
        let bars = SectionBars.bars(
            section: section, downbeats: [], chords: [], tempoBpm: 120
        )
        XCTAssertEqual(bars.count, 4)
        XCTAssertEqual(bars[0].startSec, 10)
        XCTAssertNil(bars[0].chordSymbol)
    }

    func testChordlessGapYieldsNilSymbol() {
        let section = SectionEvent(start: 0, end: 4, label: nil)
        let chords = [ChordEvent(start: 2, end: 4, symbol: "C")]
        let bars = SectionBars.bars(
            section: section, downbeats: [0, 2], chords: chords
        )
        XCTAssertEqual(bars.count, 2)
        XCTAssertNil(bars[0].chordSymbol)
        XCTAssertEqual(bars[1].chordSymbol, "C")
    }

    func testEmptyWithoutTimingData() {
        let section = SectionEvent(start: 0, end: 8, label: nil)
        XCTAssertEqual(
            SectionBars.bars(section: section, downbeats: [], chords: []),
            []
        )
    }
}
