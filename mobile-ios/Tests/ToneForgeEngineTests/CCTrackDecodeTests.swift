// CCTrackDecodeTests.swift
//
// Wire-shape tests for the curated demo-track catalog (D-024).
// Pins the GET /api/cc-tracks response decode, including sparse
// entries where the server omitted optional keys.

import XCTest
@testable import ToneForgeEngine

final class CCTrackDecodeTests: XCTestCase {

    func testFullTrackDecodes() throws {
        let json = """
        {
          "id": "night-drive",
          "title": "Night Drive",
          "artist": "Some Artist",
          "license": "CC-BY",
          "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
          "sourceUrl": "https://example.org/night-drive",
          "attribution": "“Night Drive” by Some Artist (CC BY), https://example.org/night-drive",
          "durationSec": 12.3,
          "description": "moody synthwave"
        }
        """.data(using: .utf8)!
        let track = try JSONDecoder().decode(CCTrack.self, from: json)
        XCTAssertEqual(track.id, "night-drive")
        XCTAssertEqual(track.title, "Night Drive")
        XCTAssertEqual(track.artist, "Some Artist")
        XCTAssertEqual(track.license, "CC-BY")
        XCTAssertEqual(track.licenseUrl, "https://creativecommons.org/licenses/by/4.0/")
        XCTAssertEqual(track.sourceUrl, "https://example.org/night-drive")
        XCTAssertEqual(track.durationSec, 12.3)
        XCTAssertEqual(track.description, "moody synthwave")
    }

    func testSparseTrackDecodesWithDefaults() throws {
        // CC0 entries may legitimately omit artist/urls/attribution;
        // title falls back to the id when missing.
        let json = """
        {"id": "drum-loop", "license": "CC0"}
        """.data(using: .utf8)!
        let track = try JSONDecoder().decode(CCTrack.self, from: json)
        XCTAssertEqual(track.id, "drum-loop")
        XCTAssertEqual(track.title, "drum-loop")
        XCTAssertEqual(track.artist, "")
        XCTAssertEqual(track.license, "CC0")
        XCTAssertEqual(track.attribution, "")
        XCTAssertNil(track.durationSec)
    }

    func testCatalogArrayDecodes() throws {
        struct CatalogResponse: Decodable { let tracks: [CCTrack] }
        let json = """
        {"tracks": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]}
        """.data(using: .utf8)!
        let catalog = try JSONDecoder().decode(CatalogResponse.self, from: json)
        XCTAssertEqual(catalog.tracks.map(\.id), ["a", "b"])
    }
}
