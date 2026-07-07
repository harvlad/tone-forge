// TheoryTests.swift
//
// Coverage for the Phase-4 music-theory core:
//   - NoteNames: flat keys spell flats, sharp keys spell sharps,
//     modes resolve via their relative Ionian;
//   - ChordVoicing: chord-tone tables, pitch-class parity with the
//     old ModeCoordinator table for unchanged qualities, midiNotes
//     anchoring + octave shifts;
//   - DiatonicChords: the D-natural-minor mockup row exactly,
//     harmonic minor's major V / vii°, flat-key spelling;
//   - ChordSuggestions: Dm in D minor → C, Bb (mockup), major-key
//     table, non-diatonic fallback;
//   - MusicalKey.parse: two-word harmonic/melodic minor names.

import XCTest
@testable import ToneForgeEngine

final class NoteNamesTests: XCTestCase {

    func testFlatKeySpellsFlats() {
        // pc 10 in D minor (relative major F) → Bb.
        let dMinor = MusicalKey(root: PitchClass(2), scale: .minor)
        XCTAssertTrue(dMinor.prefersFlats)
        XCTAssertEqual(NoteNames.name(pitchClass: 10, key: dMinor), "Bb")
    }

    func testSharpKeySpellsSharps() {
        // pc 10 in B major → A#.
        let bMajor = MusicalKey(root: PitchClass(11), scale: .major)
        XCTAssertFalse(bMajor.prefersFlats)
        XCTAssertEqual(NoteNames.name(pitchClass: 10, key: bMajor), "A#")
    }

    func testNoKeyDefaultsToSharps() {
        XCTAssertEqual(NoteNames.name(pitchClass: 1, key: nil), "C#")
    }

    func testModesResolveViaRelativeIonian() {
        // C mixolydian = F-major pitches → flats.
        let cMixo = MusicalKey(root: PitchClass(0), scale: .mixolydian)
        XCTAssertTrue(cMixo.prefersFlats)
        // E dorian = D-major pitches → sharps.
        let eDorian = MusicalKey(root: PitchClass(4), scale: .dorian)
        XCTAssertFalse(eDorian.prefersFlats)
    }

    func testHarmonicMinorFollowsMinorSpelling() {
        let dHarm = MusicalKey(root: PitchClass(2), scale: .harmonicMinor)
        XCTAssertTrue(dHarm.prefersFlats)
        // A minor stays sharp-side (relative C major).
        let aHarm = MusicalKey(root: PitchClass(9), scale: .harmonicMinor)
        XCTAssertFalse(aHarm.prefersFlats)
    }

    func testPitchClassWrapsModulo12() {
        XCTAssertEqual(NoteNames.name(pitchClass: 12, key: nil), "C")
        XCTAssertEqual(NoteNames.name(pitchClass: -1, key: nil), "B")
    }
}

final class ChordVoicingTests: XCTestCase {

    func testMin7IncludesSeventh() {
        // Dm7 → D F A C.
        XCTAssertEqual(
            ChordVoicing.pitchClassSet(symbol: "Dm7"), [2, 5, 9, 0]
        )
    }

    func testSusVoicesTheFourth() {
        // Csus4 → C F G.
        XCTAssertEqual(
            ChordVoicing.pitchClassSet(symbol: "Csus4"), [0, 5, 7]
        )
    }

    func testUnparseableSymbolIsEmpty() {
        XCTAssertEqual(ChordVoicing.pitchClassSet(symbol: ""), [])
        XCTAssertEqual(ChordVoicing.pitchClassSet(symbol: "??"), [])
    }

    /// The qualities the launchpad LED tests pin must match the old
    /// inline ModeCoordinator table exactly.
    func testParityWithOldPitchClassTable() {
        XCTAssertEqual(ChordVoicing.pitchClassSet(symbol: "C"), [0, 4, 7])
        XCTAssertEqual(ChordVoicing.pitchClassSet(symbol: "Am"), [9, 0, 4])
        XCTAssertEqual(
            ChordVoicing.pitchClassSet(symbol: "G7"), [7, 11, 2, 5]
        )
        XCTAssertEqual(
            ChordVoicing.pitchClassSet(symbol: "Bdim"), [11, 2, 5]
        )
        XCTAssertEqual(
            ChordVoicing.pitchClassSet(symbol: "Caug"), [0, 4, 8]
        )
    }

    func testMidiNotesAnchorAtBase() {
        // Base C3 (48): C major sits right on the base.
        XCTAssertEqual(ChordVoicing.midiNotes(symbol: "C"), [48, 52, 55])
        // D minor roots at the first D at/above 48 → 50.
        XCTAssertEqual(ChordVoicing.midiNotes(symbol: "Dm"), [50, 53, 57])
    }

    func testMidiNotesOctaveShift() {
        XCTAssertEqual(
            ChordVoicing.midiNotes(symbol: "C", octaveShift: 1),
            [60, 64, 67]
        )
        XCTAssertEqual(
            ChordVoicing.midiNotes(symbol: "C", octaveShift: -1),
            [36, 40, 43]
        )
    }

    func testMidiNotesUnparseableIsEmpty() {
        XCTAssertEqual(ChordVoicing.midiNotes(symbol: "nope"), [])
    }

    func testChordTonesRootFirstAscending() {
        let maj7 = ParsedChord(root: PitchClass(0), quality: .maj7)
        XCTAssertEqual(ChordVoicing.chordTones(for: maj7), [0, 4, 7, 11])
        let other = ParsedChord(root: PitchClass(0), quality: .other)
        XCTAssertEqual(ChordVoicing.chordTones(for: other), [0])
    }
}

final class DiatonicChordsTests: XCTestCase {

    func testDNaturalMinorMatchesMockupRow() {
        // The Jam in Key mockup's degree row for D minor:
        // Dm i / Edim ii° / F III / Gm iv / Am v / Bb VI / C VII.
        let key = MusicalKey(root: PitchClass(2), scale: .minor)
        let triads = DiatonicChords.triads(key: key)

        XCTAssertEqual(triads.count, 7)
        XCTAssertEqual(
            triads.map(\.symbol),
            ["Dm", "Edim", "F", "Gm", "Am", "Bb", "C"]
        )
        XCTAssertEqual(
            triads.map(\.romanNumeral),
            ["i", "ii°", "III", "iv", "v", "VI", "VII"]
        )
        XCTAssertEqual(triads.map(\.degree), [1, 2, 3, 4, 5, 6, 7])
    }

    func testHarmonicMinorHasMajorVAndDiminishedVii() {
        let key = MusicalKey(root: PitchClass(2), scale: .harmonicMinor)
        let triads = DiatonicChords.triads(key: key)

        XCTAssertEqual(triads[4].quality, .maj)      // A major
        XCTAssertEqual(triads[4].romanNumeral, "V")
        XCTAssertEqual(triads[4].symbol, "A")
        XCTAssertEqual(triads[6].quality, .dim)      // C# dim
        XCTAssertEqual(triads[6].romanNumeral, "vii°")
        // Augmented III on the raised 7th.
        XCTAssertEqual(triads[2].quality, .aug)
        XCTAssertEqual(triads[2].romanNumeral, "III+")
    }

    func testCMajorTriads() {
        let key = MusicalKey(root: PitchClass(0), scale: .major)
        let triads = DiatonicChords.triads(key: key)
        XCTAssertEqual(
            triads.map(\.symbol),
            ["C", "Dm", "Em", "F", "G", "Am", "Bdim"]
        )
        XCTAssertEqual(
            triads.map(\.romanNumeral),
            ["I", "ii", "iii", "IV", "V", "vi", "vii°"]
        )
    }

    func testSymbolsRoundTripThroughChordParser() {
        let key = MusicalKey(root: PitchClass(2), scale: .harmonicMinor)
        for triad in DiatonicChords.triads(key: key) {
            let parsed = ChordParser.parse(triad.symbol)
            XCTAssertEqual(parsed?.root, triad.root, triad.symbol)
        }
    }

    func testFlatKeySpelling() {
        // Eb major → Eb F Gm Ab Bb Cm Ddim, all flat-spelled.
        let key = MusicalKey(root: PitchClass(3), scale: .major)
        XCTAssertEqual(
            DiatonicChords.triads(key: key).map(\.symbol),
            ["Eb", "Fm", "Gm", "Ab", "Bb", "Cm", "Ddim"]
        )
    }
}

final class ChordSuggestionsTests: XCTestCase {

    private let dMinor = MusicalKey(root: PitchClass(2), scale: .minor)

    func testTonicMinorSuggestsVIIAndVI() {
        // The mockup: current chord Dm in D minor → Suggested C, Bb.
        let out = ChordSuggestions.suggestions(after: "Dm", in: dMinor)
        XCTAssertEqual(out.map(\.symbol), ["C", "Bb"])
    }

    func testMajorTonicSuggestsIVAndV() {
        let cMajor = MusicalKey(root: PitchClass(0), scale: .major)
        let out = ChordSuggestions.suggestions(after: "C", in: cMajor)
        XCTAssertEqual(out.map(\.symbol), ["F", "G"])
    }

    func testMatchesByRootEvenWhenQualityDiffers() {
        // Dm7 still resolves to degree 1 in D minor.
        let out = ChordSuggestions.suggestions(after: "Dm7", in: dMinor)
        XCTAssertEqual(out.map(\.symbol), ["C", "Bb"])
    }

    func testNonDiatonicRootFallsBack() {
        // C# isn't in D natural minor → tonic-anchored fallback,
        // never empty.
        let out = ChordSuggestions.suggestions(after: "C#", in: dMinor)
        XCTAssertEqual(out.count, 2)
        XCTAssertEqual(out.first?.degree, 1)
    }

    func testAlwaysExactlyTwo() {
        let cMajor = MusicalKey(root: PitchClass(0), scale: .major)
        for sym in ["C", "Dm", "Em", "F", "G", "Am", "Bdim", "", "X"] {
            XCTAssertEqual(
                ChordSuggestions.suggestions(after: sym, in: cMajor).count,
                2, sym
            )
        }
    }
}

final class ScaleVariantParseTests: XCTestCase {

    func testHarmonicMinorParses() {
        let key = MusicalKey.parse("D harmonic minor")
        XCTAssertEqual(key?.root, PitchClass(2))
        XCTAssertEqual(key?.scale, .harmonicMinor)
    }

    func testMelodicMinorParses() {
        let key = MusicalKey.parse("F# melodic minor")
        XCTAssertEqual(key?.root, PitchClass(6))
        XCTAssertEqual(key?.scale, .melodicMinor)
    }

    func testPlainMinorStillParses() {
        let key = MusicalKey.parse("D minor")
        XCTAssertEqual(key?.scale, .minor)
    }

    func testIntervalTables() {
        XCTAssertEqual(
            ScaleIntervals.intervals(for: .harmonicMinor),
            [0, 2, 3, 5, 7, 8, 11]
        )
        XCTAssertEqual(
            ScaleIntervals.intervals(for: .melodicMinor),
            [0, 2, 3, 5, 7, 9, 11]
        )
    }
}
