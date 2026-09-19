// BundleDecodeTests.swift
//
// Round-trip decode/encode tests for the SongBundle wire shape. If
// the backend adds or renames a field, one of these tests fails and
// forces an explicit sync in DECISIONS.md.

import XCTest
@testable import ToneForgeEngine

final class BundleDecodeTests: XCTestCase {

    /// The canonical minimal bundle. Every optional is nil so the
    /// decoder is exercised on the sparsest realistic payload.
    private let minimalJSON = """
    {
      "bundleVersion": 1,
      "analysisId": "abc12345",
      "meta": {
        "title": "Test Song",
        "artist": "",
        "sourceUrl": "",
        "durationSec": 60.0,
        "tempoBpm": null,
        "detectedKey": null
      },
      "timeline": {
        "chords": [],
        "sections": [],
        "beats": [],
        "downbeats": []
      },
      "stems": [],
      "presets": {}
    }
    """.data(using: .utf8)!

    func testMinimalBundleDecodes() throws {
        let bundle = try JSONDecoder().decode(SongBundle.self, from: minimalJSON)
        XCTAssertEqual(bundle.bundleVersion, 1)
        XCTAssertEqual(bundle.analysisId, "abc12345")
        XCTAssertEqual(bundle.meta.title, "Test Song")
        XCTAssertEqual(bundle.meta.durationSec, 60.0)
        XCTAssertNil(bundle.meta.tempoBpm)
        XCTAssertTrue(bundle.stems.isEmpty)
        XCTAssertTrue(bundle.presets.isEmpty)
        // Attribution keys (D-024) absent from old cached bundles —
        // they must decode to nil, not throw.
        XCTAssertNil(bundle.meta.license)
        XCTAssertNil(bundle.meta.licenseUrl)
        XCTAssertNil(bundle.meta.attribution)
    }

    func testMetaAttributionFieldsDecode() throws {
        // Server emits the D-024 attribution keys on new bundles.
        let json = """
        {
          "title": "Night Drive",
          "artist": "Some Artist",
          "sourceUrl": "https://example.org/night-drive",
          "durationSec": 12.3,
          "license": "CC-BY",
          "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
          "attribution": "“Night Drive” by Some Artist (CC BY)"
        }
        """.data(using: .utf8)!
        let meta = try JSONDecoder().decode(BundleMeta.self, from: json)
        XCTAssertEqual(meta.license, "CC-BY")
        XCTAssertEqual(meta.licenseUrl, "https://creativecommons.org/licenses/by/4.0/")
        XCTAssertEqual(meta.attribution, "“Night Drive” by Some Artist (CC BY)")
    }

    func testBundleRoundTrips() throws {
        let original = try JSONDecoder().decode(SongBundle.self, from: minimalJSON)
        let encoded = try JSONEncoder().encode(original)
        let redecoded = try JSONDecoder().decode(SongBundle.self, from: encoded)
        XCTAssertEqual(original, redecoded)
    }

    // MARK: - Per-stem chord lanes (richest-lane pick)

    func testChordsByStemDecodesAndOldBundlesFallBack() throws {
        // Absent chordsByStem (minimal bundle) → nil, resolvedChords
        // falls back to the flat `chords` lane.
        let legacy = try JSONDecoder().decode(SongBundle.self, from: minimalJSON)
        XCTAssertNil(legacy.timeline.chordsByStem)
        XCTAssertTrue(legacy.timeline.resolvedChords.isEmpty)
    }

    func testResolvedChordsPicksRichestLane() throws {
        // A thin residual "other" lane vs a guitar lane carrying the real
        // harmony — the Cross Bones Style shape. resolvedChords must pick
        // the guitar lane by COVERAGE (summed seconds), not lane name or
        // region count.
        let json = """
        {
          "bundleVersion": 1,
          "analysisId": "xbones01",
          "meta": {"title": "X", "artist": "", "sourceUrl": "", "durationSec": 30.0},
          "timeline": {
            "chords": [{"start": 0.0, "end": 2.0, "symbol": "Am"}],
            "chordsByStem": {
              "other": [{"start": 0.0, "end": 2.0, "symbol": "Am"}],
              "guitar_1": [
                {"start": 0.0, "end": 8.0, "symbol": "C#"},
                {"start": 8.0, "end": 16.0, "symbol": "F#"}
              ]
            },
            "sections": [], "beats": [], "downbeats": []
          },
          "stems": [], "presets": {}
        }
        """.data(using: .utf8)!
        let bundle = try JSONDecoder().decode(SongBundle.self, from: json)
        XCTAssertEqual(bundle.timeline.chordsByStem?.count, 2)
        // Legacy flat lane is the single-chord "other" residual.
        XCTAssertEqual(bundle.timeline.chords.map(\.symbol), ["Am"])
        // Resolved lane is the richer guitar progression.
        XCTAssertEqual(bundle.timeline.resolvedChords.map(\.symbol), ["C#", "F#"])
    }

    func testResolvedChordsPrefersCoverageNotCount() {
        // Many half-second slivers (12 × 0.5 s = 6 s) must NOT outrank one
        // long honest lane (2 × 8 s = 16 s).
        let slivers = (0..<12).map { i in
            ChordEvent(start: Double(i) * 0.5, end: Double(i) * 0.5 + 0.5, symbol: "N")
        }
        let honest = [
            ChordEvent(start: 0, end: 8, symbol: "G"),
            ChordEvent(start: 8, end: 16, symbol: "D"),
        ]
        let timeline = BundleTimeline(
            chords: [],
            chordsByStem: ["a_sliver": slivers, "z_honest": honest]
        )
        XCTAssertEqual(timeline.resolvedChords.map(\.symbol), ["G", "D"])
    }

    func testResolvedChordsExcludesVocalsAndDrums() {
        // A legacy/cached bundle whose vocals/drums lanes out-cover the
        // guitar lane must NOT win: vocals is melody-traced and drums is
        // unpitched — neither is harmony. Matches web (jam.js) + desktop
        // (ChordLaneSelection) so every surface picks the SAME lane for
        // the SAME song regardless of which backend served the bundle
        // (the fix for iOS being the one surface missing the exclusion).
        let vocals = [ChordEvent(start: 0, end: 200, symbol: "X")]   // huge coverage
        let drums = [ChordEvent(start: 0, end: 200, symbol: "N")]
        let guitar = [
            ChordEvent(start: 0, end: 8, symbol: "C#"),
            ChordEvent(start: 8, end: 16, symbol: "F#"),
        ]
        let timeline = BundleTimeline(
            chords: [ChordEvent(start: 0, end: 2, symbol: "Am")],
            chordsByStem: ["vocals": vocals, "drums": drums, "guitar_1": guitar]
        )
        XCTAssertEqual(timeline.resolvedChords.map(\.symbol), ["C#", "F#"])
    }

    func testResolvedChordsFallsBackWhenOnlyNonHarmonicLanes() {
        // If the per-stem dict carries ONLY vocals/drums (all excluded),
        // fall back to the flat `chords` lane rather than returning an
        // excluded lane or an empty result.
        let timeline = BundleTimeline(
            chords: [ChordEvent(start: 0, end: 4, symbol: "Am")],
            chordsByStem: [
                "vocals": [ChordEvent(start: 0, end: 100, symbol: "X")],
                "drums": [ChordEvent(start: 0, end: 100, symbol: "N")],
            ]
        )
        XCTAssertEqual(timeline.resolvedChords.map(\.symbol), ["Am"])
    }

    func testPadIndexArithmetic() {
        let bottomLeft = PadIndex(11)
        XCTAssertEqual(bottomLeft.row, 1)
        XCTAssertEqual(bottomLeft.col, 1)
        XCTAssertTrue(bottomLeft.isValid)

        let topRight = PadIndex.at(row: 8, col: 8)
        XCTAssertEqual(topRight.rawValue, 88)
        XCTAssertTrue(topRight.isValid)

        let invalid = PadIndex(99)
        XCTAssertFalse(invalid.isValid)
    }
}
