// CrateModelsDecodeTests.swift
//
// Pins the pure crate wire decode + facet-query builder (CrateModels.swift),
// the same discipline that caught the borrow source/stem regressions: the
// decode + request-building live in JamDesktopCore precisely so they fail CI
// here rather than in the user's picker.
//
// What must hold:
//   • a candidates row parses from EITHER `trackId` or the borrow-superset
//     `entryId`, and `matchScore` falls back to `harmonic` when absent (a mixed
//     response still ranks),
//   • the CC provenance (attribution / licenseId / exportEncumbered) survives —
//     it is the on-screen obligation, so a dropped field is a compliance bug,
//   • a search response decodes tracks + per-facet counts + total,
//   • a partial track (missing optional analysis fields) still parses,
//   • the facet→query mapping is DETERMINISTIC and stable — identical filters
//     must produce an identical request on every surface (parity rule 4).

import XCTest
@testable import JamDesktopCore

final class CrateModelsDecodeTests: XCTestCase {

    // MARK: Candidates

    private let candidatesJSON = Data("""
    {
      "sessionId": "song-1",
      "stem": "drums",
      "targetTempo": 120.0,
      "targetKey": "G minor",
      "candidates": [
        {
          "trackId": "crate:jamendo:123",
          "name": "Funky Break",
          "artist": "DJ Clean",
          "tempo": 118.0,
          "key": "G minor",
          "harmonic": 0.92,
          "matchScore": 0.87,
          "tempoDistance": 0.03,
          "genre": "funk",
          "mood": "energetic",
          "tags": ["groove", "live"],
          "attribution": "Funky Break by DJ Clean — CC-BY 4.0",
          "licenseId": "CC-BY-4.0",
          "exportEncumbered": false,
          "availableStems": ["drums", "bass"]
        },
        {
          "entryId": "crate:fma:9",
          "name": "SA Loop",
          "tempo": 120.0,
          "harmonic": 0.4,
          "licenseId": "CC-BY-SA-4.0",
          "exportEncumbered": true
        }
      ]
    }
    """.utf8)

    func testCandidatesEnvelopeDecodes() throws {
        let resp = try JSONDecoder().decode(
            CrateCandidatesResponse.self, from: candidatesJSON)
        XCTAssertEqual(resp.sessionId, "song-1")
        XCTAssertEqual(resp.stem, "drums")
        XCTAssertEqual(resp.targetTempo, 120.0)
        XCTAssertEqual(resp.targetKey, "G minor")
        XCTAssertEqual(resp.candidates.count, 2)
    }

    func testCandidateFullRowKeepsProvenance() throws {
        let c = try JSONDecoder().decode(
            CrateCandidatesResponse.self, from: candidatesJSON).candidates[0]
        XCTAssertEqual(c.trackId, "crate:jamendo:123")
        XCTAssertEqual(c.id, "crate:jamendo:123")
        XCTAssertEqual(c.artist, "DJ Clean")
        XCTAssertEqual(c.matchScore, 0.87)
        XCTAssertEqual(c.tags, ["groove", "live"])
        XCTAssertEqual(c.attribution, "Funky Break by DJ Clean — CC-BY 4.0")
        XCTAssertEqual(c.licenseId, "CC-BY-4.0")
        XCTAssertFalse(c.exportEncumbered)
        XCTAssertEqual(c.availableStems, ["drums", "bass"])
    }

    func testCandidateAcceptsEntryIdAndFallsBackMatchScore() throws {
        // The second row uses the borrow-superset `entryId` and omits
        // matchScore — it must still identify + rank (matchScore ← harmonic).
        let c = try JSONDecoder().decode(
            CrateCandidatesResponse.self, from: candidatesJSON).candidates[1]
        XCTAssertEqual(c.trackId, "crate:fma:9")
        XCTAssertEqual(c.matchScore, 0.4, accuracy: 1e-9)   // fell back to harmonic
        XCTAssertTrue(c.exportEncumbered)                   // BY-SA badge fires
        XCTAssertEqual(c.artist, "")                        // defaulted, not a throw
    }

    // MARK: Search

    private let searchJSON = Data("""
    {
      "total": 2,
      "facetCounts": {
        "genre": {"funk": 1, "ambient": 1},
        "mood": {"energetic": 1, "chill": 1},
        "key": {"G minor": 1}
      },
      "tracks": [
        {
          "id": "crate:jamendo:123",
          "title": "Funky Break",
          "artist": "DJ Clean",
          "album": "Breaks Vol 1",
          "year": 2019,
          "genre": "funk",
          "subgenres": ["boogaloo"],
          "tags": ["groove", "live"],
          "mood": "energetic",
          "license": {
            "licenseId": "CC-BY-4.0",
            "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
            "attribution": "Funky Break by DJ Clean — CC-BY 4.0",
            "source": "jamendo",
            "sourceUrl": "https://jamendo.com/track/123",
            "exportEncumbered": false
          },
          "features": {
            "tempoBpm": 118.0,
            "detectedKey": "G minor",
            "keyConfidence": 0.8,
            "durationS": 182.0,
            "sectionCount": 5,
            "energy": 0.6,
            "availableStems": ["drums", "bass", "other"],
            "instrumentation": ["drums", "bass", "keys"],
            "hasVocals": false
          },
          "previewUrl": "https://cdn/x.mp3",
          "graphAvailable": true
        },
        {
          "id": "crate:ccmixter:7",
          "title": "Sparse Pad",
          "license": {"licenseId": "CC0", "attribution": "Sparse Pad (CC0)"},
          "features": {"tempoBpm": 90.0}
        }
      ]
    }
    """.utf8)

    func testSearchEnvelopeDecodes() throws {
        let resp = try JSONDecoder().decode(CrateSearchResponse.self, from: searchJSON)
        XCTAssertEqual(resp.total, 2)
        XCTAssertEqual(resp.tracks.count, 2)
        XCTAssertEqual(resp.facetCounts["genre"]?["funk"], 1)
        XCTAssertEqual(resp.facetCounts["mood"]?["chill"], 1)
        XCTAssertEqual(resp.facetCounts["key"]?["G minor"], 1)
    }

    func testTrackUnionAndConveniences() throws {
        let t = try JSONDecoder().decode(
            CrateSearchResponse.self, from: searchJSON).tracks[0]
        XCTAssertEqual(t.title, "Funky Break")
        XCTAssertEqual(t.year, 2019)
        XCTAssertEqual(t.subgenres, ["boogaloo"])
        // Convenience passthroughs into the nested license/features shapes.
        XCTAssertEqual(t.attribution, "Funky Break by DJ Clean — CC-BY 4.0")
        XCTAssertFalse(t.exportEncumbered)
        XCTAssertEqual(t.tempo, 118.0)
        XCTAssertEqual(t.key, "G minor")
        XCTAssertEqual(t.availableStems, ["drums", "bass", "other"])
        XCTAssertFalse(t.hasVocals)
        XCTAssertTrue(t.graphAvailable)
        XCTAssertEqual(t.license.source, "jamendo")
    }

    func testPartialTrackParsesWithDefaults() throws {
        // A seed/fixture row with only id/title/license/tempo must still parse
        // (partial analyses are expected) — never drop the whole response.
        let t = try JSONDecoder().decode(
            CrateSearchResponse.self, from: searchJSON).tracks[1]
        XCTAssertEqual(t.id, "crate:ccmixter:7")
        XCTAssertEqual(t.tempo, 90.0)
        XCTAssertEqual(t.license.licenseId, "CC0")
        XCTAssertNil(t.key)
        XCTAssertFalse(t.graphAvailable)      // absent → un-renderable, greyed
        XCTAssertTrue(t.availableStems.isEmpty)
    }

    // MARK: Facet query building (the parity contract)

    func testEmptyFacetsEmitNoFilterItems() {
        XCTAssertTrue(CrateFacetQuery().facetQueryItems().isEmpty)
        XCTAssertTrue(CrateFacetQuery().isEmpty)
    }

    func testFacetQueryIsDeterministicAndTyped() {
        let f = CrateFacetQuery(
            text: " funk break ", genre: "funk", mood: "energetic",
            tags: ["groove", "live"], tempoMin: 110, tempoMax: 130,
            key: "G minor", stems: ["drums", "bass"], hasVocals: false,
            license: "CC0", cleanExportOnly: true)
        let items = f.facetQueryItems()
        // Deterministic order + multi-valued facets repeat their key (OR).
        let pairs = items.map { "\($0.name)=\($0.value ?? "")" }
        XCTAssertEqual(pairs, [
            "genre=funk",
            "mood=energetic",
            "tags=groove",
            "tags=live",
            "tempo_min=110",       // whole numbers render without ".0"
            "tempo_max=130",
            "key=G minor",
            "stems=drums",
            "stems=bass",
            "has_vocals=0",
            "license=CC0",
            "clean_export=1",
        ])
        XCTAssertFalse(f.isEmpty)
    }

    func testSearchQueryAddsTextSortAndPaging() {
        var f = CrateFacetQuery(text: "  drums  ", genre: "funk")
        f.sort = "tempo"
        let items = f.searchQueryItems(limit: 40, offset: 20)
        let pairs = items.map { "\($0.name)=\($0.value ?? "")" }
        // q is trimmed and leads; facets in the middle; sort + paging trail.
        XCTAssertEqual(pairs, [
            "q=drums",
            "genre=funk",
            "sort=tempo",
            "limit=40",
            "offset=20",
        ])
    }

    func testWholeAndFractionalTempoFormatting() {
        let whole = CrateFacetQuery(tempoMin: 120).facetQueryItems()
        XCTAssertEqual(whole.first?.value, "120")
        let frac = CrateFacetQuery(tempoMin: 120.5).facetQueryItems()
        XCTAssertEqual(frac.first?.value, "120.5")
    }

    func testGenreModeRawValues() {
        XCTAssertEqual(CrateGenreMode.similar.rawValue, "similar")
        XCTAssertEqual(CrateGenreMode.contrast.rawValue, "contrast")
    }
}
