// RomanNumeralTests.swift
//
// D-022 Learn redesign: chord symbol → roman-numeral function in a
// key. Complements DiatonicChordsTests (the key → triads direction).

import XCTest
@testable import ToneForgeEngine

final class RomanNumeralTests: XCTestCase {

    private let cMajor = MusicalKey(root: PitchClass(0), scale: .major)
    private let aMinor = MusicalKey(root: PitchClass(9), scale: .minor)

    func testDiatonicTriadsInCMajor() {
        XCTAssertEqual(RomanNumeral.label(symbol: "C", key: cMajor), "I")
        XCTAssertEqual(RomanNumeral.label(symbol: "Dm", key: cMajor), "ii")
        XCTAssertEqual(RomanNumeral.label(symbol: "Em", key: cMajor), "iii")
        XCTAssertEqual(RomanNumeral.label(symbol: "F", key: cMajor), "IV")
        XCTAssertEqual(RomanNumeral.label(symbol: "G", key: cMajor), "V")
        XCTAssertEqual(RomanNumeral.label(symbol: "Am", key: cMajor), "vi")
        XCTAssertEqual(RomanNumeral.label(symbol: "Bdim", key: cMajor), "vii°")
    }

    func testMinorKeyDegrees() {
        XCTAssertEqual(RomanNumeral.label(symbol: "Am", key: aMinor), "i")
        XCTAssertEqual(RomanNumeral.label(symbol: "C", key: aMinor), "III")
        XCTAssertEqual(RomanNumeral.label(symbol: "Dm", key: aMinor), "iv")
        XCTAssertEqual(RomanNumeral.label(symbol: "F", key: aMinor), "VI")
        XCTAssertEqual(RomanNumeral.label(symbol: "G", key: aMinor), "VII")
    }

    func testSeventhQualitiesKeepTheirSuffix() {
        XCTAssertEqual(RomanNumeral.label(symbol: "G7", key: cMajor), "V7")
        XCTAssertEqual(RomanNumeral.label(symbol: "Dm7", key: cMajor), "ii7")
        XCTAssertEqual(RomanNumeral.label(symbol: "Fmaj7", key: cMajor), "IVmaj7")
        XCTAssertEqual(RomanNumeral.label(symbol: "Csus4", key: cMajor), "Isus")
    }

    func testEnharmonicRootMatches() {
        // Db in C# terms — same pitch class, same degree.
        let dFlatKey = MusicalKey(root: PitchClass(1), scale: .major)
        XCTAssertEqual(RomanNumeral.label(symbol: "C#", key: dFlatKey), "I")
        XCTAssertEqual(RomanNumeral.label(symbol: "Db", key: dFlatKey), "I")
    }

    func testChromaticRootReturnsNil() {
        XCTAssertNil(RomanNumeral.label(symbol: "C#", key: cMajor))
        XCTAssertNil(RomanNumeral.label(symbol: "Eb", key: cMajor))
    }

    func testNilKeyAndBadSymbolReturnNil() {
        XCTAssertNil(RomanNumeral.label(symbol: "C", key: nil))
        XCTAssertNil(RomanNumeral.label(symbol: "??", key: cMajor))
        // Slash chords classify as .other — no functional label.
        XCTAssertNil(RomanNumeral.label(symbol: "G/B", key: cMajor))
    }

    /// Round-trip with DiatonicChords: every generated triad's symbol
    /// labels back to its own roman numeral.
    func testAgreesWithDiatonicChords() {
        for key in [cMajor, aMinor,
                    MusicalKey(root: PitchClass(2), scale: .dorian)] {
            for triad in DiatonicChords.triads(key: key) {
                XCTAssertEqual(
                    RomanNumeral.label(symbol: triad.symbol, key: key),
                    triad.romanNumeral,
                    "\(triad.symbol) in \(key)"
                )
            }
        }
    }
}
