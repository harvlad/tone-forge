// ChordTransitionHintTests.swift
//
// Pins the Swift port of the web's _computeTransitionHint (jam.js) to
// the exact web output strings — the hint text is a cross-platform
// parity surface, so a wording drift here is a bug, not a style
// choice. Shapes are built from the same "x02210" pattern notation
// GuitarVoicingTests uses.

import XCTest
@testable import ToneForgeEngine

final class ChordTransitionHintTests: XCTestCase {

    /// "x02210"-style pattern → shape (single-digit frets suffice for
    /// every case here).
    private func shape(_ pattern: String, baseFret: Int = 1) -> GuitarChordShape {
        let strings: [GuitarStringState] = pattern.map { ch in
            switch ch {
            case "x": return .muted
            case "0": return .open
            default:  return .fretted(Int(String(ch))!)
            }
        }
        return GuitarChordShape(baseFret: baseFret, strings: strings)
    }

    // MARK: - Same shape shifted (barre movements)

    func testSameShapeShiftedUp() {
        // F → G as E-shape barres: every sounding string +2.
        let f = shape("133211")
        let g = shape("355433", baseFret: 3)
        XCTAssertEqual(
            ChordTransitionHint.hint(from: f, to: g), "Same shape +2 frets")
    }

    func testSameShapeShiftedDownKeepsSign() {
        let f = shape("133211")
        let g = shape("355433", baseFret: 3)
        XCTAssertEqual(
            ChordTransitionHint.hint(from: g, to: f), "Same shape -2 frets")
    }

    func testSingleFretShiftIsSingular() {
        // F → F#: "fret", not "frets" (web pluralization rule).
        let f = shape("133211")
        let fs = shape("244322", baseFret: 2)
        XCTAssertEqual(
            ChordTransitionHint.hint(from: f, to: fs), "Same shape +1 fret")
    }

    // MARK: - Finger-count hints

    func testOneFingerMove() {
        // D → Dsus4: only the high-e fret changes (2 → 3).
        XCTAssertEqual(
            ChordTransitionHint.hint(from: shape("xx0232"), to: shape("xx0233")),
            "Move one finger only")
    }

    func testTwoFingerMove() {
        // Am → C: the A string (0→3) and G string (2→0) change.
        XCTAssertEqual(
            ChordTransitionHint.hint(from: shape("x02210"), to: shape("x32010")),
            "Two-finger move")
    }

    // MARK: - Barre entry

    func testBarreEntry() {
        // C → F: mute structure differs (low E x→1) and four strings
        // change, so it falls through to the next shape's barre.
        XCTAssertEqual(
            ChordTransitionHint.hint(from: shape("x32010"), to: shape("133211")),
            "Barre at fret 1")
    }

    // MARK: - No rule fires

    func testOpenToDHasNoHint() {
        // E → D: six strings change and D has no barre → nil (web
        // parity: empty string, panel hidden).
        XCTAssertNil(
            ChordTransitionHint.hint(from: shape("022100"), to: shape("xx0232")))
    }

    // MARK: - Symbol-driven overload (GuitarVoicing lookups)

    func testSymbolOverloadUsesVoicedShapes() {
        // Am → x02210, C → x32010 are pinned by GuitarVoicingTests.
        XCTAssertEqual(
            ChordTransitionHint.hint(from: "Am", to: "C"), "Two-finger move")
    }

    func testSameOrMissingSymbolsReturnNil() {
        XCTAssertNil(ChordTransitionHint.hint(from: "Am", to: "Am"))
        XCTAssertNil(ChordTransitionHint.hint(from: nil, to: "C"))
        XCTAssertNil(ChordTransitionHint.hint(from: "Am", to: nil))
        XCTAssertNil(ChordTransitionHint.hint(from: "Am", to: "not-a-chord"))
    }

    func testWireFretMapping() {
        // GuitarChordShape → web wire array: -1 muted, 0 open, n fret.
        XCTAssertEqual(
            ChordTransitionHint.frets(of: shape("x02210")),
            [-1, 0, 2, 2, 1, 0])
    }
}
