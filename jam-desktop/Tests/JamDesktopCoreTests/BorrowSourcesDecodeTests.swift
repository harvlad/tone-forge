// BorrowSourcesDecodeTests.swift
//
// Pins the pure per-pad source/stem sidecar decode extracted from
// SessionController.fetchBorrowRaw. Two regressions shipped from this being
// inline (untestable) code:
//   • the mount hard-coded stem "drums" → all borrow pads one color, and
//   • the `source` tag was dropped → the 64/divider layout couldn't tell the
//     current song's pads from the donor's.
// A sample /borrow payload exercises both here so they fail CI.

import XCTest
@testable import JamDesktopCore

final class BorrowSourcesDecodeTests: XCTestCase {

    /// A representative /borrow response: top pads = the current song
    /// (`initial`), lower pads = the donor, mixed stems, plus one pad with a
    /// MISSING source and one with a MISSING stem to prove the defaults.
    private let payload = Data("""
    {
      "name": "Some Donor · kit",
      "pads": [
        {"padIdx": 0, "source": "initial", "stem": "drums"},
        {"padIdx": 1, "source": "initial", "stem": "bass"},
        {"padIdx": 2, "source": "initial", "stem": "other"},
        {"padIdx": 8, "source": "donor",   "stem": "vocals"},
        {"padIdx": 9, "source": "donor",   "stem": "bass"},
        {"padIdx": 10, "stem": "drums"},
        {"padIdx": 11, "source": "donor"}
      ]
    }
    """.utf8)

    func testDonorPadsDecodeDonorAndStem() {
        let (sources, stems) = decodeBorrowSources(from: payload)
        XCTAssertEqual(sources[8], .donor)
        XCTAssertEqual(stems[8], "vocals")
        XCTAssertEqual(sources[9], .donor)
        XCTAssertEqual(stems[9], "bass")
    }

    func testInitialPadsDecodeInitialAndStem() {
        let (sources, stems) = decodeBorrowSources(from: payload)
        XCTAssertEqual(sources[0], .initial)
        XCTAssertEqual(stems[0], "drums")
        XCTAssertEqual(sources[2], .initial)
        XCTAssertEqual(stems[2], "other")
    }

    func testMissingSourceDefaultsToInitial() {
        // Pad 10 has a stem but no `source` — must read as the current song,
        // never silently become a donor pad.
        let (sources, stems) = decodeBorrowSources(from: payload)
        XCTAssertEqual(sources[10], .initial)
        XCTAssertEqual(stems[10], "drums")
    }

    func testMissingStemHasNoEntry() {
        // Pad 11 is a donor with no stem — source survives, stem map has no key
        // (the mount then defaults the stem to "other", i.e. chords).
        let (sources, stems) = decodeBorrowSources(from: payload)
        XCTAssertEqual(sources[11], .donor)
        XCTAssertNil(stems[11])
    }

    func testStemTagSurvivesForColoring() {
        // The whole point of carrying stem: distinct pad colors. Every stem in
        // the payload must be preserved, not collapsed to "drums".
        let (_, stems) = decodeBorrowSources(from: payload)
        XCTAssertEqual(Set(stems.values), ["drums", "bass", "other", "vocals"])
    }

    func testMalformedPayloadYieldsEmptyMapsNotCrash() {
        // A payload with no `pads` array must degrade to empty maps so a borrow
        // still mounts (callers default source .initial / stem "other").
        let (sources, stems) = decodeBorrowSources(
            from: Data(#"{"name":"x"}"#.utf8))
        XCTAssertTrue(sources.isEmpty)
        XCTAssertTrue(stems.isEmpty)

        let (s2, st2) = decodeBorrowSources(from: Data("not json".utf8))
        XCTAssertTrue(s2.isEmpty)
        XCTAssertTrue(st2.isEmpty)
    }
}
