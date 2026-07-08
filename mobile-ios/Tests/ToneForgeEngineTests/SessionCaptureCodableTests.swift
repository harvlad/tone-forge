// SessionCaptureCodableTests.swift
//
// Freezes the SessionCapture v1 wire shape. If any of these fail
// after a code change, the change broke session-file compatibility —
// fix the code, never the fixture (schema evolution = new keys via
// decodeIfPresent, or a schemaVersion bump).

import XCTest
@testable import ToneForgeEngine

final class SessionCaptureCodableTests: XCTestCase {

    private let sessionId = UUID(
        uuidString: "11111111-2222-3333-4444-555555555555")!

    private func makeSession(
        events: [ContributionEvent] = [],
        songBackendId: String? = "song-1",
        tempoBpm: Double? = 120
    ) -> SessionCapture {
        SessionCapture(
            sessionId: sessionId,
            songBackendId: songBackendId,
            appMode: .sample,
            capturedAt: Date(timeIntervalSince1970: 1_700_000_000),
            tempoBpm: tempoBpm,
            events: events,
            padMapping: [
                PadAddress(mode: .sample, pad: PadIndex(11)):
                    .packPad(packId: "pack-1", padIdx: 23)
            ]
        )
    }

    // MARK: - Frozen encode

    func testEncodedShapeIsFrozen() throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let json = String(
            decoding: try encoder.encode(makeSession()), as: UTF8.self)
        XCTAssertEqual(json, """
            {"appMode":"sample","capturedAtEpoch":1700000000,"events":[],\
            "padMapping":[{"address":{"mode":"sample","pad":11},\
            "ref":{"packId":"pack-1","padIdx":23,"type":"packPad"}}],\
            "schemaVersion":1,\
            "sessionId":"11111111-2222-3333-4444-555555555555",\
            "songBackendId":"song-1","tempoBpm":120}
            """)
    }

    func testNilOptionalsAreOmitted() throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let json = String(
            decoding: try encoder.encode(
                makeSession(songBackendId: nil, tempoBpm: nil)),
            as: UTF8.self)
        XCTAssertFalse(json.contains("songBackendId"))
        XCTAssertFalse(json.contains("tempoBpm"))
    }

    // MARK: - Frozen decode

    func testDecodesFrozenV1Payload() throws {
        let payload = """
            {"appMode":"hybrid","capturedAtEpoch":1700000000,
             "events":[
               {"hostTime":0,"isReplay":false,
                "kind":{"col":3,"row":2,"type":"padDown"},
                "source":{"type":"launchpad"},
                "timestamp":1.5,"velocity":0.5},
               {"hostTime":0,"isReplay":false,
                "kind":{"seconds":-4,"type":"gap"},
                "source":{"name":"transport","type":"future"},
                "timestamp":6,"velocity":1}
             ],
             "padMapping":[
               {"address":{"mode":"hybrid","pad":58},
                "ref":{"id":"AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
                       "type":"localSample"}}
             ],
             "schemaVersion":1,
             "sessionId":"11111111-2222-3333-4444-555555555555",
             "tempoBpm":97.5}
            """
        let session = try JSONDecoder().decode(
            SessionCapture.self, from: Data(payload.utf8))
        XCTAssertEqual(session.schemaVersion, 1)
        XCTAssertEqual(session.sessionId, sessionId)
        XCTAssertNil(session.songBackendId)
        XCTAssertEqual(session.appMode, .hybrid)
        XCTAssertEqual(session.tempoBpm, 97.5)
        XCTAssertEqual(session.events.count, 2)
        XCTAssertEqual(session.events[0].kind, .padDown(row: 2, col: 3))
        XCTAssertEqual(session.events[0].velocity, 0.5)
        XCTAssertEqual(session.events[1].kind, .gap(seconds: -4))
        XCTAssertEqual(session.events[1].source, .future("transport"))
        XCTAssertEqual(
            session.padMapping[
                PadAddress(mode: .hybrid, pad: PadIndex(58))],
            .localSample(id: UUID(
                uuidString: "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")!))
    }

    func testMissingPadMappingDecodesEmpty() throws {
        let payload = """
            {"appMode":"sample","capturedAtEpoch":0,"events":[],
             "schemaVersion":1,
             "sessionId":"11111111-2222-3333-4444-555555555555"}
            """
        let session = try JSONDecoder().decode(
            SessionCapture.self, from: Data(payload.utf8))
        XCTAssertTrue(session.padMapping.isEmpty)
    }

    // MARK: - Round trip

    func testRoundTripWithEventsIsLossless() throws {
        let events = [
            ContributionEvent(
                source: .touch, kind: .padDown(row: 1, col: 1),
                timestamp: 0.25, hostTime: 42, velocity: 0.75),
            ContributionEvent(
                source: .launchpad,
                kind: .midiNote(note: 60, velocity: 100, on: true),
                timestamp: 1.0, hostTime: 43),
            ContributionEvent(
                source: .future("transport"), kind: .gap(seconds: 0),
                timestamp: 2.0, hostTime: 0),
        ]
        let original = makeSession(events: events)
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(
            SessionCapture.self, from: data)
        XCTAssertEqual(decoded, original)
    }

    // MARK: - Determinism

    func testPadMappingEncodingIsOrderIndependent() throws {
        // Same mapping built in different insertion orders must
        // produce byte-identical JSON (sorted pair array).
        var a = makeSession()
        var b = makeSession()
        let extra: [(PadAddress, PadSampleReference)] = [
            (PadAddress(mode: .hybrid, pad: PadIndex(88)),
             .packPad(packId: "p", padIdx: 88)),
            (PadAddress(mode: .sample, pad: PadIndex(42)),
             .localSample(id: sessionId)),
        ]
        for (addr, ref) in extra { a.padMapping[addr] = ref }
        for (addr, ref) in extra.reversed() { b.padMapping[addr] = ref }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        XCTAssertEqual(try encoder.encode(a), try encoder.encode(b))
    }

    func testDurationSecIsLastEventTimestamp() {
        XCTAssertEqual(makeSession().durationSec, 0)
        let events = [
            ContributionEvent(
                source: .touch, kind: .padDown(row: 1, col: 1),
                timestamp: 3.5, hostTime: 0),
            ContributionEvent(
                source: .touch, kind: .padUp(row: 1, col: 1),
                timestamp: 1.0, hostTime: 0),
        ]
        XCTAssertEqual(makeSession(events: events).durationSec, 3.5)
    }

    // MARK: - slotLabel (D-022 Phase 7)

    func testSlotLabelDecodesNilFromOldSession() throws {
        // V1 sessions captured before Phase 7 have no slotLabel key.
        let payload = """
            {"appMode":"sample","capturedAtEpoch":0,"events":[],
             "schemaVersion":1,
             "sessionId":"11111111-2222-3333-4444-555555555555"}
            """
        let session = try JSONDecoder().decode(
            SessionCapture.self, from: Data(payload.utf8))
        XCTAssertNil(session.slotLabel)
    }

    func testSlotLabelRoundTrips() throws {
        var session = makeSession()
        session.slotLabel = "A"
        let data = try JSONEncoder().encode(session)
        let decoded = try JSONDecoder().decode(
            SessionCapture.self, from: data)
        XCTAssertEqual(decoded.slotLabel, "A")
    }

    func testSlotLabelOmittedWhenNil() throws {
        let session = makeSession()
        XCTAssertNil(session.slotLabel)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let json = String(decoding: try encoder.encode(session), as: UTF8.self)
        XCTAssertFalse(json.contains("slotLabel"))
    }

    func testSlotLabelIncludedWhenSet() throws {
        var session = makeSession()
        session.slotLabel = "B"
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let json = String(decoding: try encoder.encode(session), as: UTF8.self)
        XCTAssertTrue(json.contains("\"slotLabel\":\"B\""))
    }
}
