// ChordAdvancerTests.swift
//
// Binary-search walker over a chord timeline. Nail down the boundary
// cases so the ribbon animation on-screen stays glitch-free.

import XCTest
@testable import ToneForgeEngine

final class ChordAdvancerTests: XCTestCase {

    private let timeline: [ChordEvent] = [
        ChordEvent(start: 0.0,  end: 2.0,  symbol: "C"),
        ChordEvent(start: 2.0,  end: 4.0,  symbol: "Am"),
        ChordEvent(start: 4.0,  end: 6.0,  symbol: "F"),
        ChordEvent(start: 8.0,  end: 10.0, symbol: "G"),  // gap 6..8
    ]

    private lazy var advancer = ChordAdvancer(chords: timeline)

    func testEmptyTimeline() {
        let a = ChordAdvancer(chords: [])
        let f = a.frame(at: 0)
        XCTAssertNil(f.active)
        XCTAssertNil(f.next)
        XCTAssertEqual(f.phase, 0)
        XCTAssertTrue(a.isEmpty)
    }

    func testBeforeFirstChord() {
        let a = ChordAdvancer(chords: [ChordEvent(start: 5, end: 6, symbol: "C")])
        let f = a.frame(at: 1)
        XCTAssertNil(f.active)
        XCTAssertEqual(f.next?.symbol, "C")
    }

    func testActiveFirstChord() {
        let f = advancer.frame(at: 0.5)
        XCTAssertEqual(f.active?.symbol, "C")
        XCTAssertEqual(f.next?.symbol, "Am")
        XCTAssertEqual(f.phase, 0.25, accuracy: 0.001)
    }

    func testExactBoundaryPrefersNextChord() {
        // At songSeconds == 2.0, C.end == 2.0 (exclusive) and
        // Am.start == 2.0 (inclusive). Active = Am.
        let f = advancer.frame(at: 2.0)
        XCTAssertEqual(f.active?.symbol, "Am")
        XCTAssertEqual(f.next?.symbol, "F")
    }

    func testMidTimeline() {
        let f = advancer.frame(at: 5.5)
        XCTAssertEqual(f.active?.symbol, "F")
        XCTAssertEqual(f.next?.symbol, "G")
        XCTAssertEqual(f.phase, 0.75, accuracy: 0.001)
    }

    func testInGap() {
        // Between F (ends 6) and G (starts 8) → no active, next = G.
        let f = advancer.frame(at: 7.0)
        XCTAssertNil(f.active)
        XCTAssertEqual(f.next?.symbol, "G")
        XCTAssertEqual(f.phase, 0)
    }

    func testLastChordNoNext() {
        let f = advancer.frame(at: 9.0)
        XCTAssertEqual(f.active?.symbol, "G")
        XCTAssertNil(f.next)
    }

    func testAfterEnd() {
        let f = advancer.frame(at: 100.0)
        XCTAssertNil(f.active)
        XCTAssertNil(f.next)
    }

    func testUnsortedInputIsSorted() {
        let unsorted = [
            ChordEvent(start: 4, end: 6, symbol: "F"),
            ChordEvent(start: 0, end: 2, symbol: "C"),
            ChordEvent(start: 2, end: 4, symbol: "Am"),
        ]
        let a = ChordAdvancer(chords: unsorted)
        let f = a.frame(at: 0.5)
        XCTAssertEqual(f.active?.symbol, "C")
        XCTAssertEqual(f.next?.symbol, "Am")
    }

    func testTotalDuration() {
        XCTAssertEqual(advancer.totalDuration, 10.0, accuracy: 0.0001)
    }
}
