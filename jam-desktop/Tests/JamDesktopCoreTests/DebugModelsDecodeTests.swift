// DebugModelsDecodeTests.swift
//
// Wire-parity decode coverage for the Debug window models. The bundle
// fixture mirrors asdict(SessionBundle) from the backend, including a
// legacy section (no guidance extras) and a null
// repetition_period_beats. Inline JSON covers the three list
// endpoints.

import XCTest
@testable import JamDesktopCore

final class DebugModelsDecodeTests: XCTestCase {

    private func fixture(_ name: String) throws -> Data {
        let url = try XCTUnwrap(Bundle.module.url(
            forResource: "Fixtures/\(name)", withExtension: "json"
        ))
        return try Data(contentsOf: url)
    }

    // MARK: - /api/session/{id}

    func testDecodesBundleFixture() throws {
        let bundle = try JSONDecoder().decode(
            DebugBundle.self, from: fixture("debug_bundle"))
        XCTAssertEqual(bundle.sessionId, "abc123")
        XCTAssertEqual(bundle.audio?.durationS, 245.5)
        XCTAssertEqual(bundle.sections.count, 2)

        let intro = bundle.sections[0]
        XCTAssertEqual(intro.label, "intro")
        XCTAssertEqual(intro.guidanceMode, "chord")
        XCTAssertEqual(intro.guidanceConfidence, 0.82)
        XCTAssertEqual(intro.dominantStem, "other")
        XCTAssertEqual(intro.bpm, 120.0)
        XCTAssertEqual(intro.landmarkNotes?.count, 2)
        XCTAssertEqual(intro.landmarkNotes?[0].pitch, 60.0)
        XCTAssertEqual(intro.landmarkNotes?[0].velocity, 96)

        // Null repetition_period_beats decodes as nil, siblings intact.
        let features = try XCTUnwrap(intro.debugFeatures)
        XCTAssertEqual(features.count, 2)
        XCTAssertNil(features[0].repetitionPeriodBeats)
        XCTAssertEqual(features[0].noteCount, 42)
        XCTAssertEqual(features[1].repetitionPeriodBeats, 4.0)
        XCTAssertEqual(features[1].stemName, "vocals")
    }

    func testLegacySectionDecodesWithNils() throws {
        let bundle = try JSONDecoder().decode(
            DebugBundle.self, from: fixture("debug_bundle"))
        let verse = bundle.sections[1]
        XCTAssertEqual(verse.label, "verse")
        XCTAssertEqual(verse.guidanceMode, "riff")
        XCTAssertNil(verse.guidanceConfidence)
        XCTAssertNil(verse.dominantStem)
        XCTAssertNil(verse.landmarkNotes)
        XCTAssertNil(verse.debugFeatures)
    }

    func testChordsPreferBeatSnapped() throws {
        let bundle = try JSONDecoder().decode(
            DebugBundle.self, from: fixture("debug_bundle"))
        XCTAssertEqual(bundle.chords.count, 3)
        XCTAssertEqual(bundle.chords[2].symbol, "Bm")
    }

    func testChordsFallBackToRawWhenNoSnapped() throws {
        let bundle = DebugBundle(understanding: DebugUnderstanding(
            chords: [DebugChord(startS: 0, endS: 1, symbol: "C")],
            chordsBeatSnapped: []))
        XCTAssertEqual(bundle.chords.count, 1)
        XCTAssertEqual(bundle.chords[0].symbol, "C")
    }

    func testDurationFallsBackToLastSectionEdge() throws {
        let bundle = DebugBundle(understanding: DebugUnderstanding(
            sections: [
                DebugSection(startS: 0, endS: 10),
                DebugSection(startS: 10, endS: 32.5),
            ]))
        XCTAssertEqual(bundle.duration, 32.5)
    }

    // MARK: - /api/debug/sessions

    func testDecodesSessionsResponse() throws {
        let json = """
        {"sessions": [
          {"id": "s1", "name": "Song One", "timestamp": "2026-07-01T12:00:00",
           "detected_type": "full_song", "section_count": 8,
           "has_debug_features": true},
          {"id": "s2", "name": "Legacy"}
        ]}
        """
        let response = try JSONDecoder().decode(
            DebugSessionsResponse.self, from: Data(json.utf8))
        XCTAssertEqual(response.sessions.count, 2)
        XCTAssertEqual(response.sessions[0].sectionCount, 8)
        XCTAssertEqual(response.sessions[0].hasDebugFeatures, true)
        XCTAssertNil(response.sessions[1].hasDebugFeatures)
    }

    // MARK: - /api/debug/corpus

    func testDecodesCorpus() throws {
        let json = """
        {"songs": [
          {"slug": "wonderwall", "title": "Wonderwall", "artist": "Oasis",
           "ground_truth_sections": [
             {"label": "intro", "guidance_mode": "chord"},
             {"label": "verse", "guidance_mode": "riff"}
           ]},
          {"title": "No Slug Song"}
        ]}
        """
        let corpus = try JSONDecoder().decode(
            DebugCorpus.self, from: Data(json.utf8))
        XCTAssertEqual(corpus.songs.count, 2)
        XCTAssertEqual(corpus.songs[0].groundTruthSections?.count, 2)
        XCTAssertEqual(corpus.songs[0].groundTruthSections?[1].guidanceMode, "riff")
        XCTAssertNil(corpus.songs[1].slug)
    }

    // MARK: - /api/history

    func testDecodesHistoryResponse() throws {
        let json = """
        {"history": [
          {"id": "h1", "timestamp": "2026-07-01T12:00:00", "name": "Song",
           "filename": "song.mp3", "detected_type": "full_song",
           "tempo_bpm": 120.5, "detected_key": "C major"},
          {"id": "h2"}
        ]}
        """
        let response = try JSONDecoder().decode(
            DebugHistoryResponse.self, from: Data(json.utf8))
        XCTAssertEqual(response.history.count, 2)
        XCTAssertEqual(response.history[0].tempoBpm, 120.5)
        XCTAssertEqual(response.history[0].detectedKey, "C major")
        XCTAssertNil(response.history[1].name)
    }
}
