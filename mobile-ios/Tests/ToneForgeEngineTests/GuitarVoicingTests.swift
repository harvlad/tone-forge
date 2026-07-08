// GuitarVoicingTests.swift
//
// D-022 Learn redesign: pins the open-position folk shapes to the
// chord charts everyone knows. If the window search changes, these
// exact shapes must survive — they're what the Learn fretboards show.

import XCTest
@testable import ToneForgeEngine

final class GuitarVoicingTests: XCTestCase {

    /// "x02210"-style string → expected states for readable asserts.
    private func assertShape(
        _ symbol: String,
        _ pattern: String,
        baseFret: Int = 1,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        guard let shape = GuitarVoicing.shape(symbol: symbol) else {
            XCTFail("No shape for \(symbol)", file: file, line: line)
            return
        }
        let expected: [GuitarStringState] = pattern.map { ch in
            switch ch {
            case "x": return .muted
            case "0": return .open
            default:  return .fretted(Int(String(ch))!)
            }
        }
        XCTAssertEqual(shape.strings, expected, "\(symbol)", file: file, line: line)
        XCTAssertEqual(shape.baseFret, baseFret, "\(symbol) baseFret", file: file, line: line)
    }

    // MARK: - Pinned open-position shapes (plan Phase 4)

    func testAm() { assertShape("Am", "x02210") }
    func testE()  { assertShape("E",  "022100") }
    func testC()  { assertShape("C",  "x32010") }
    func testDm() { assertShape("Dm", "xx0231") }
    func testG7() { assertShape("G7", "320001") }

    // MARK: - Structural properties

    func testAlwaysSixStrings() {
        for symbol in ["A", "Bm", "F#m", "Bb", "Cmaj7", "Dsus4", "Eb"] {
            guard let shape = GuitarVoicing.shape(symbol: symbol) else {
                XCTFail("No shape for \(symbol)")
                continue
            }
            XCTAssertEqual(shape.strings.count, 6, symbol)
        }
    }

    func testBassIsTheRoot() {
        // The lowest sounding string must carry the chord root.
        let cases: [(String, Int)] = [
            ("Am", 9), ("E", 4), ("C", 0), ("Dm", 2),
            ("G7", 7), ("Bb", 10), ("F#m", 6),
        ]
        for (symbol, rootPC) in cases {
            guard let shape = GuitarVoicing.shape(symbol: symbol) else {
                XCTFail("No shape for \(symbol)")
                continue
            }
            let bassPC: Int? = zip(
                GuitarVoicing.standardTuning, shape.strings
            ).compactMap { openMidi, state -> Int? in
                switch state {
                case .muted: return nil
                case .open: return openMidi % 12
                case .fretted(let f): return (openMidi + f) % 12
                }
            }.first
            XCTAssertEqual(bassPC, rootPC, symbol)
        }
    }

    func testSoundingNotesAreChordTones() {
        for symbol in ["Am", "E", "C", "Dm", "G7", "Bb", "Cmaj7", "Em7"] {
            guard let shape = GuitarVoicing.shape(symbol: symbol) else {
                XCTFail("No shape for \(symbol)")
                continue
            }
            let tones = ChordVoicing.pitchClassSet(symbol: symbol)
            for (openMidi, state) in zip(
                GuitarVoicing.standardTuning, shape.strings
            ) {
                switch state {
                case .muted: continue
                case .open:
                    XCTAssertTrue(
                        tones.contains(openMidi % 12),
                        "\(symbol): open string \(openMidi) out of chord")
                case .fretted(let f):
                    XCTAssertTrue(
                        tones.contains((openMidi + f) % 12),
                        "\(symbol): fret \(f) on \(openMidi) out of chord")
                }
            }
        }
    }

    func testFrettedNotesStayInsideTheWindow() {
        for symbol in ["Am", "Bb", "Eb", "F#m", "Abm", "C#"] {
            guard let shape = GuitarVoicing.shape(symbol: symbol) else {
                XCTFail("No shape for \(symbol)")
                continue
            }
            for state in shape.strings {
                if case .fretted(let f) = state {
                    XCTAssertGreaterThanOrEqual(f, 1, symbol)
                    // Window is 4 frets from the base.
                    let base = shape.baseFret == 1 ? 1 : shape.baseFret
                    XCTAssertLessThan(f, base + 4, symbol)
                }
            }
        }
    }

    func testUnparseableSymbolReturnsNil() {
        XCTAssertNil(GuitarVoicing.shape(symbol: ""))
        XCTAssertNil(GuitarVoicing.shape(symbol: "??"))
    }
}
