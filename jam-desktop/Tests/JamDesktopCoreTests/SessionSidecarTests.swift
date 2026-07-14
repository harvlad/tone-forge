// SessionSidecarTests.swift
//
// Sidecar decode (legacy_* keys on /api/session/{id}), tone display
// names, and the LeadNotePicker port (preference order, melody
// filter, density threshold, densest fallback).

import XCTest
@testable import JamDesktopCore

final class SessionSidecarTests: XCTestCase {

    // MARK: Decode

    func testDecodesAllSidecars() throws {
        let json = Data("""
        {
          "session_id": "abc",
          "stems": [],
          "legacy_tone": {
            "tier": "medium",
            "rationale": "Tempo and key suggest a mid-gain crunch.",
            "apply": {"chain_id": "tfc.classic_rock", "action": "suggest"},
            "match": {"chain_id": "tfc.classic_rock", "display_name": "Classic Rock",
                      "distance": 0.42, "confidence": 0.7},
            "alternates": [
              {"chain_id": "tfc.blues_break", "display_name": "Blues Break", "distance": 0.55}
            ]
          },
          "legacy_midi_stems": {
            "guitar": {"notes": [{"start": 0.5, "end": 1.0, "pitch": 64, "velocity": 90}],
                       "note_count": 1}
          },
          "legacy_attribution": {
            "title": "Song", "artist": "Band", "license": "CC-BY",
            "license_url": "https://example.com/l", "source_url": "https://example.com/s",
            "attribution": "Song by Band"
          }
        }
        """.utf8)
        let sidecar = try SessionSidecarClient.decode(json)
        XCTAssertEqual(sidecar.tone?.tier, "medium")
        XCTAssertEqual(sidecar.tone?.apply?.chainId, "tfc.classic_rock")
        XCTAssertEqual(sidecar.tone?.match?.displayName, "Classic Rock")
        XCTAssertEqual(sidecar.tone?.alternates?.first?.chainId, "tfc.blues_break")
        XCTAssertEqual(sidecar.midiStems?["guitar"]?.notes?.first?.pitch, 64)
        XCTAssertEqual(sidecar.attribution?.artist, "Band")
        XCTAssertEqual(sidecar.attribution?.licenseUrl, "https://example.com/l")
    }

    func testDecodesWithAllSidecarsAbsent() throws {
        let sidecar = try SessionSidecarClient.decode(Data(#"{"session_id":"x"}"#.utf8))
        XCTAssertNil(sidecar.tone)
        XCTAssertNil(sidecar.midiStems)
        XCTAssertNil(sidecar.attribution)
    }

    func testDecodesNullTone() throws {
        let sidecar = try SessionSidecarClient.decode(
            Data(#"{"legacy_tone": null, "legacy_midi_stems": {}}"#.utf8))
        XCTAssertNil(sidecar.tone)
        XCTAssertEqual(sidecar.midiStems, [:])
    }

    // MARK: Display names (jam.js toneChainDisplayName parity)

    func testChainDisplayNameStripsPrefixAndTitleCases() {
        XCTAssertEqual(
            ToneRecommendation.displayName(forChainId: "tfc.classic_rock"),
            "Classic Rock")
        XCTAssertEqual(
            ToneRecommendation.displayName(forChainId: "tube-break"),
            "Tube Break")
        XCTAssertEqual(ToneRecommendation.displayName(forChainId: nil), "")
    }

    func testCardTitlePrefersMatchDisplayName() {
        var rec = ToneRecommendation(
            apply: .init(chainId: "tfc.fallback_clean"),
            match: .init(chainId: "tfc.classic_rock", displayName: "Classic Rock"))
        XCTAssertEqual(rec.cardTitle, "Classic Rock")
        rec.match = nil
        XCTAssertEqual(rec.cardTitle, "Fallback Clean")
    }

    // MARK: LeadNotePicker

    private func notes(_ count: Int, role: String? = nil) -> [MidiNote] {
        (0..<count).map {
            MidiNote(start: Double($0), end: Double($0) + 0.5, pitch: 60, role: role)
        }
    }

    func testPrefersGuitarWhenDenseEnough() {
        let stems = [
            "guitar": MidiStem(notes: notes(30)),
            "bass": MidiStem(notes: notes(50)),
        ]
        // 30 notes / 10 s = 3.0/s >= 0.5 — guitar wins on preference.
        let picked = LeadNotePicker.pick(stems: stems, durationSec: 10)
        XCTAssertEqual(picked.count, 30)
    }

    func testSparsePreferredStemLosesToDenserLowerPriority() {
        let stems = [
            "guitar": MidiStem(notes: notes(2)),   // 0.02/s: sparse
            "vocals": MidiStem(notes: notes(30)),  // 0.3/s: sparse too
        ]
        // Both under floor at 100 s — densest (vocals) wins.
        let picked = LeadNotePicker.pick(stems: stems, durationSec: 100)
        XCTAssertEqual(picked.count, 30)
    }

    func testMelodyFilterDropsHarmonyUnlessEmpty() {
        let mixed = notes(4, role: "melody") + notes(6, role: "harmony")
        XCTAssertEqual(LeadNotePicker.melodyOnly(mixed).count, 4)

        let allHarmony = notes(6, role: "harmony")
        // Filtering would empty the lane — fall back to full list.
        XCTAssertEqual(LeadNotePicker.melodyOnly(allHarmony).count, 6)

        let untagged = notes(5)
        XCTAssertEqual(LeadNotePicker.melodyOnly(untagged).count, 5)
    }

    func testEmptyOrMissingStemsYieldEmpty() {
        XCTAssertEqual(LeadNotePicker.pick(stems: nil, durationSec: 10), [])
        XCTAssertEqual(LeadNotePicker.pick(stems: [:], durationSec: 10), [])
        XCTAssertEqual(
            LeadNotePicker.pick(
                stems: ["drums": MidiStem(notes: notes(50))], durationSec: 10),
            [])  // drums not in preference list
    }

    func testDurationClampsToOneSecondFloor() {
        let stems = ["guitar": MidiStem(notes: notes(1))]
        // duration 0 would divide-by-zero; floor makes density 1.0/s.
        let picked = LeadNotePicker.pick(stems: stems, durationSec: 0)
        XCTAssertEqual(picked.count, 1)
    }
}
