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
