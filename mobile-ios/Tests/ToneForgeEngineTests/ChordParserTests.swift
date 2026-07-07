// ChordParserTests.swift
//
// Verifies the Swift port of launchpad.js `_parseChordSymbol` +
// `_classifyQuality` matches JS output byte-for-byte. If either side
// drifts, one of these assertions fires and forces a sync (D-006).

import XCTest
@testable import ToneForgeEngine

final class ChordParserTests: XCTestCase {

    // MARK: - PitchClass

    func testPitchClassNormalisesNegatives() {
        XCTAssertEqual(PitchClass(-1).rawValue, 11)
        XCTAssertEqual(PitchClass(-13).rawValue, 11)
        XCTAssertEqual(PitchClass(12).rawValue, 0)
        XCTAssertEqual(PitchClass(23).rawValue, 11)
    }

    // MARK: - parse() happy paths

    func testParseBareMajor() {
        let p = ChordParser.parse("C")
        XCTAssertEqual(p?.root.rawValue, 0)
        XCTAssertEqual(p?.quality, .maj)
    }

    func testParseSharpRoot() {
        let p = ChordParser.parse("F#m")
        XCTAssertEqual(p?.root.rawValue, 6)
        XCTAssertEqual(p?.quality, .min)
    }

    func testParseFlatRoot() {
        let p = ChordParser.parse("Bbmaj7")
        XCTAssertEqual(p?.root.rawValue, 10)
        XCTAssertEqual(p?.quality, .maj7)
    }

    func testEnharmonicCollapse() {
        // C# and Db must yield the same canonical key.
        XCTAssertEqual(
            ChordParser.parse("C#m")?.canonicalKey,
            ChordParser.parse("Dbm")?.canonicalKey
        )
        // G# and Ab.
        XCTAssertEqual(
            ChordParser.parse("G#7")?.canonicalKey,
            ChordParser.parse("Ab7")?.canonicalKey
        )
        // B# and C.
        XCTAssertEqual(
            ChordParser.parse("B#")?.canonicalKey,
            ChordParser.parse("C")?.canonicalKey
        )
    }

    func testLowercaseRootLetter() {
        // Regex accepts a-g and uppercases internally.
        let p = ChordParser.parse("gm7")
        XCTAssertEqual(p?.root.rawValue, 7)
        XCTAssertEqual(p?.quality, .min7)
    }

    // MARK: - parse() rejection paths

    func testParseEmptyStringReturnsNil() {
        XCTAssertNil(ChordParser.parse(""))
        XCTAssertNil(ChordParser.parse("   "))
    }

    func testParseNonNoteReturnsNil() {
        XCTAssertNil(ChordParser.parse("Xm"))
        XCTAssertNil(ChordParser.parse("123"))
        XCTAssertNil(ChordParser.parse("N.C."))
    }

    // MARK: - classifyQuality full sweep

    func testClassifyPlainMajor() {
        for suffix in ["", "maj", "major", "add9", "6", "maj6"] {
            XCTAssertEqual(ChordParser.classifyQuality(suffix), .maj, "suffix=\(suffix)")
        }
    }

    func testClassifyMajorSevenths() {
        for suffix in ["maj7", "maj9", "maj13"] {
            XCTAssertEqual(ChordParser.classifyQuality(suffix), .maj7, "suffix=\(suffix)")
        }
    }

    func testClassifyMinorSevenths() {
        for suffix in ["m7", "min7", "m9", "min9", "m11"] {
            XCTAssertEqual(ChordParser.classifyQuality(suffix), .min7, "suffix=\(suffix)")
        }
    }

    func testClassifyPlainMinor() {
        for suffix in ["m", "min", "minor", "m6"] {
            XCTAssertEqual(ChordParser.classifyQuality(suffix), .min, "suffix=\(suffix)")
        }
    }

    func testClassifySuspended() {
        for suffix in ["sus", "sus2", "sus4"] {
            XCTAssertEqual(ChordParser.classifyQuality(suffix), .sus, "suffix=\(suffix)")
        }
    }

    func testClassifyDiminished() {
        for suffix in ["dim", "dim7", "m7b5", "ø"] {
            XCTAssertEqual(ChordParser.classifyQuality(suffix), .dim, "suffix=\(suffix)")
        }
    }

    func testClassifyAugmented() {
        for suffix in ["aug", "+", "aug7"] {
            XCTAssertEqual(ChordParser.classifyQuality(suffix), .aug, "suffix=\(suffix)")
        }
    }

    func testClassifyDominant() {
        for suffix in ["7", "9", "11", "13", "dom7"] {
            XCTAssertEqual(ChordParser.classifyQuality(suffix), .dom7, "suffix=\(suffix)")
        }
    }

    func testClassifyOther() {
        for suffix in ["/E", "wat", "add11b13"] {
            XCTAssertEqual(ChordParser.classifyQuality(suffix), .other, "suffix=\(suffix)")
        }
    }

    // MARK: - family collapse

    func testFamilyMapping() {
        XCTAssertEqual(ChordParser.parse("C")?.family, .major)
        XCTAssertEqual(ChordParser.parse("Cmaj7")?.family, .major)
        XCTAssertEqual(ChordParser.parse("Csus4")?.family, .major)
        XCTAssertEqual(ChordParser.parse("Cm")?.family, .minor)
        XCTAssertEqual(ChordParser.parse("Cm7")?.family, .minor)
        XCTAssertEqual(ChordParser.parse("C7")?.family, .dom7)
        XCTAssertEqual(ChordParser.parse("Cdim")?.family, .dim)
        XCTAssertEqual(ChordParser.parse("Caug")?.family, .aug)
    }

    // MARK: - canonicalKey shape

    func testCanonicalKeyFormat() {
        XCTAssertEqual(ChordParser.parse("Am7")?.canonicalKey, "9:min7")
        XCTAssertEqual(ChordParser.parse("G")?.canonicalKey, "7:maj")
        XCTAssertEqual(ChordParser.parse("F#dim")?.canonicalKey, "6:dim")
    }
}
