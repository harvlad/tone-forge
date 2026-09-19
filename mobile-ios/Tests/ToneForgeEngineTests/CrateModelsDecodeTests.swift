// CrateModelsDecodeTests.swift
//
// Pins the pure Vinyl Crate wire decode + facet-query builder (CrateModels.swift)
// against the REAL backend output — the nested `features`/`license` objects the
// backend serializes SNAKE_CASE (registry.features_to_dict / license_to_dict)
// plus the camelCase borrow-superset mirrors a `/candidates` row adds. The same
// discipline the desktop CrateModelsDecodeTests use, because the CC provenance
// on every row is an on-screen legal obligation — a dropped field is a
// compliance bug, not a cosmetic one.
//
// What must hold:
//   • a candidates row parses from `entryId`/`id`/`trackId`, and `matchScore`
//     falls back to `harmonic` when absent (a mixed response still ranks),
//   • the CC provenance (attribution / licenseId / exportEncumbered) survives,
//   • a search response decodes SNAKE_CASE nested features/license + per-facet
//     counts (whose bucket keys are NOT snake-mangled) + total,
//   • a partial track still parses,
//   • an unknown license id classifies as .unknown (never throws),
//   • the facet→query mapping is DETERMINISTIC and byte-identical to the other
//     surfaces (parity rule 4),
//   • Camelot codes match web keyToCamelot / backend key_to_camelot.

import XCTest
@testable import ToneForgeEngine

final class CrateModelsDecodeTests: XCTestCase {

    // MARK: Candidates (real /api/crate/candidates shape)

    // Mirrors the backend: track_to_dict base (snake_case nested `features`
    // + camelCase `attribution`/`licenseId`/`exportEncumbered` mirrors) with
    // the row.update() borrow-superset fields laid on top.
    private let candidatesJSON = Data("""
    {
      "sessionId": "song-1",
      "stem": "drums",
      "genreMode": "similar",
      "targetTempo": 120.0,
      "targetKey": "G minor",
      "candidates": [
        {
          "id": "crate:jamendo:123",
          "title": "Funky Break",
          "artist": "DJ Clean",
          "genre": "funk",
          "mood": "energetic",
          "tags": ["groove", "live"],
          "attribution": "Funky Break by DJ Clean \\u2014 CC-BY 4.0",
          "licenseId": "CC-BY-4.0",
          "exportEncumbered": false,
          "features": {
            "tempo_bpm": 118.0,
            "detected_key": "G minor",
            "available_stems": ["drums", "bass"],
            "has_vocals": false
          },
          "entryId": "crate:jamendo:123",
          "name": "Funky Break",
          "tempo": 118.0,
          "key": "G minor",
          "harmonic": 0.92,
          "matchScore": 0.87,
          "tempoDistance": 0.03,
          "targetStem": "drums"
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

    func testCandidateFullRowKeepsProvenanceAndNestedStems() throws {
        let c = try JSONDecoder().decode(
            CrateCandidatesResponse.self, from: candidatesJSON).candidates[0]
        XCTAssertEqual(c.trackId, "crate:jamendo:123")
        XCTAssertEqual(c.id, "crate:jamendo:123")
        XCTAssertEqual(c.artist, "DJ Clean")
        XCTAssertEqual(c.matchScore, 0.87)
        XCTAssertEqual(c.tempo, 118.0)
        XCTAssertEqual(c.key, "G minor")
        XCTAssertEqual(c.tags, ["groove", "live"])
        XCTAssertEqual(c.attribution, "Funky Break by DJ Clean \u{2014} CC-BY 4.0")
        XCTAssertEqual(c.licenseId, "CC-BY-4.0")
        XCTAssertFalse(c.exportEncumbered)
        // availableStems is NOT mirrored at top level — sourced from nested
        // snake_case features.available_stems.
        XCTAssertEqual(c.availableStems, ["drums", "bass"])
    }

    func testCandidateAcceptsEntryIdAndFallsBackMatchScore() throws {
        let c = try JSONDecoder().decode(
            CrateCandidatesResponse.self, from: candidatesJSON).candidates[1]
        XCTAssertEqual(c.trackId, "crate:fma:9")
        XCTAssertEqual(c.matchScore, 0.4, accuracy: 1e-9)   // fell back to harmonic
        XCTAssertTrue(c.exportEncumbered)                   // BY-SA badge fires
        XCTAssertEqual(c.artist, "")                        // defaulted, not a throw
    }

    // MARK: Search (real /api/crate/search shape — snake_case nested objects)

    private let searchJSON = Data("""
    {
      "total": 2,
      "facetCounts": {
        "genre": {"funk": 1, "hip_hop": 1},
        "mood": {"energetic": 1, "chill": 1},
        "key": {"G minor": 1},
        "camelot": {"6A": 1}
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
            "license_id": "CC-BY-4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "attribution": "Funky Break by DJ Clean \\u2014 CC-BY 4.0",
            "source": "jamendo",
            "source_url": "https://jamendo.com/track/123",
            "export_encumbered": false
          },
          "features": {
            "tempo_bpm": 118.0,
            "detected_key": "G minor",
            "key_confidence": 0.8,
            "duration_s": 182.0,
            "section_count": 5,
            "energy": 0.6,
            "available_stems": ["drums", "bass", "other"],
            "instrumentation": ["drums", "bass", "keys"],
            "has_vocals": false
          },
          "preview_url": "https://cdn/x.mp3",
          "graph_available": true,
          "attribution": "Funky Break by DJ Clean \\u2014 CC-BY 4.0",
          "licenseId": "CC-BY-4.0",
          "exportEncumbered": false
        },
        {
          "id": "crate:ccmixter:7",
          "title": "Sparse Pad",
          "license": {"license_id": "CC0", "attribution": "Sparse Pad (CC0)"},
          "features": {"tempo_bpm": 90.0}
        }
      ]
    }
    """.utf8)

    func testSearchEnvelopeDecodesAndFacetKeysUnmangled() throws {
        let resp = try JSONDecoder().decode(CrateSearchResponse.self, from: searchJSON)
        XCTAssertEqual(resp.total, 2)
        XCTAssertEqual(resp.tracks.count, 2)
        XCTAssertEqual(resp.facetCounts["genre"]?["funk"], 1)
        // The critical facet-bucket test: a value WITH an underscore must NOT
        // be snake-mangled (would break with .convertFromSnakeCase).
        XCTAssertEqual(resp.facetCounts["genre"]?["hip_hop"], 1)
        XCTAssertNil(resp.facetCounts["genre"]?["hipHop"])
        XCTAssertEqual(resp.facetCounts["key"]?["G minor"], 1)
        XCTAssertEqual(resp.facetCounts["camelot"]?["6A"], 1)
    }

    func testTrackUnionAndConveniencesFromSnakeCase() throws {
        let t = try JSONDecoder().decode(
            CrateSearchResponse.self, from: searchJSON).tracks[0]
        XCTAssertEqual(t.title, "Funky Break")
        XCTAssertEqual(t.year, 2019)
        XCTAssertEqual(t.subgenres, ["boogaloo"])
        XCTAssertEqual(t.attribution, "Funky Break by DJ Clean \u{2014} CC-BY 4.0")
        XCTAssertFalse(t.exportEncumbered)
        XCTAssertEqual(t.tempo, 118.0)                 // from features.tempo_bpm
        XCTAssertEqual(t.key, "G minor")               // from features.detected_key
        XCTAssertEqual(t.availableStems, ["drums", "bass", "other"])
        XCTAssertFalse(t.hasVocals)
        XCTAssertTrue(t.graphAvailable)                // from graph_available
        XCTAssertEqual(t.license.source, "jamendo")
        XCTAssertEqual(t.license.sourceUrl, "https://jamendo.com/track/123")
        XCTAssertEqual(t.license.kind, .ccBy)
        XCTAssertEqual(t.camelot, "6A")                // G minor → 6A
    }

    func testPartialTrackParsesWithDefaults() throws {
        let t = try JSONDecoder().decode(
            CrateSearchResponse.self, from: searchJSON).tracks[1]
        XCTAssertEqual(t.id, "crate:ccmixter:7")
        XCTAssertEqual(t.tempo, 90.0)
        XCTAssertEqual(t.license.licenseId, "CC0")
        XCTAssertEqual(t.license.kind, .cc0)
        XCTAssertNil(t.key)
        XCTAssertFalse(t.graphAvailable)               // absent → un-renderable
        XCTAssertTrue(t.availableStems.isEmpty)
    }

    // MARK: License classification (forward-compatible enum)

    func testLicenseKindClassification() {
        XCTAssertEqual(CrateLicenseKind(id: "CC0"), .cc0)
        XCTAssertEqual(CrateLicenseKind(id: "CC-BY-4.0"), .ccBy)
        XCTAssertEqual(CrateLicenseKind(id: "CC-BY-SA-4.0"), .ccBySa)   // BY-SA first
        XCTAssertEqual(CrateLicenseKind(id: "CC-NEW-9.9"), .unknown)    // never throws
    }

    func testLicenseLabels() {
        XCTAssertEqual(CrateLicenseKind(id: "CC0").label(rawId: "CC0"), "CC0")
        XCTAssertEqual(CrateLicenseKind(id: "CC-BY-4.0").label(rawId: "CC-BY-4.0"),
                       "CC-BY 4.0")
        XCTAssertEqual(
            CrateLicenseKind(id: "CC-BY-SA-4.0").label(rawId: "CC-BY-SA-4.0"),
            "CC-BY-SA 4.0")
    }

    func testShareAlikeFlag() {
        let bySa = CrateLicense(licenseId: "CC-BY-SA-4.0", attribution: "x",
                                exportEncumbered: true)
        XCTAssertTrue(bySa.isShareAlike)
        let by = CrateLicense(licenseId: "CC-BY-4.0", attribution: "x")
        XCTAssertFalse(by.isShareAlike)
    }

    // MARK: Camelot (parity with backend key_to_camelot)

    func testCamelotCodes() {
        XCTAssertEqual(CrateCamelot.code(for: "A minor"), "8A")
        XCTAssertEqual(CrateCamelot.code(for: "C major"), "8B")
        XCTAssertEqual(CrateCamelot.code(for: "G minor"), "6A")
        XCTAssertEqual(CrateCamelot.code(for: "Bb major"), "6B")   // enharmonic fold
        XCTAssertEqual(CrateCamelot.code(for: nil), "")
        XCTAssertEqual(CrateCamelot.code(for: "?"), "")            // no note letter
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
        let pairs = f.facetQueryItems().map { "\($0.name)=\($0.value ?? "")" }
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
        let pairs = f.searchQueryItems(limit: 40, offset: 20)
            .map { "\($0.name)=\($0.value ?? "")" }
        XCTAssertEqual(pairs, [
            "q=drums",
            "genre=funk",
            "sort=tempo",
            "limit=40",
            "offset=20",
        ])
    }

    func testWholeAndFractionalTempoFormatting() {
        XCTAssertEqual(
            CrateFacetQuery(tempoMin: 120).facetQueryItems().first?.value, "120")
        XCTAssertEqual(
            CrateFacetQuery(tempoMin: 120.5).facetQueryItems().first?.value, "120.5")
    }

    func testGenreModeRawValues() {
        XCTAssertEqual(CrateGenreMode.similar.rawValue, "similar")
        XCTAssertEqual(CrateGenreMode.contrast.rawValue, "contrast")
    }

    // MARK: camelCase tolerance (legacy/seed payloads still parse)

    func testCamelCaseNestedAlsoParses() throws {
        let json = Data("""
        {"tracks": [{
          "id": "crate:x:1", "title": "Camel",
          "license": {"licenseId": "CC0", "attribution": "Camel (CC0)"},
          "features": {"tempoBpm": 100.0, "detectedKey": "A minor",
                       "availableStems": ["drums"], "hasVocals": true}
        }]}
        """.utf8)
        let t = try JSONDecoder().decode(CrateSearchResponse.self, from: json).tracks[0]
        XCTAssertEqual(t.tempo, 100.0)
        XCTAssertEqual(t.key, "A minor")
        XCTAssertEqual(t.availableStems, ["drums"])
        XCTAssertTrue(t.hasVocals)
        XCTAssertEqual(t.license.kind, .cc0)
    }
}
