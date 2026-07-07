// ContributionEventCodableTests.swift
//
// Freezes the ContributionEvent v1 wire shape. SessionCapture (P6)
// serialises these verbatim into session JSON, so any change to the
// encoded keys/values below is a BREAKING schema change and must be
// additive-only (new `type` strings, decodeIfPresent fields).

import XCTest
@testable import ToneForgeEngine

final class ContributionEventCodableTests: XCTestCase {

    private func encodeSortedJSON(_ event: ContributionEvent) throws -> String {
        let enc = JSONEncoder()
        enc.outputFormatting = [.sortedKeys]
        return String(data: try enc.encode(event), encoding: .utf8)!
    }

    private func decode(_ json: String) throws -> ContributionEvent {
        try JSONDecoder().decode(ContributionEvent.self, from: Data(json.utf8))
    }

    // MARK: - Frozen v1 fixtures (DO NOT EDIT the expected strings)

    func testFrozenPadDownTouch() throws {
        let event = ContributionEvent(
            source: .touch,
            kind: .padDown(row: 1, col: 8),
            timestamp: 12.5,
            hostTime: 123456789,
            velocity: 1.0,
            isReplay: false
        )
        let json = try encodeSortedJSON(event)
        XCTAssertEqual(json, #"{"hostTime":123456789,"isReplay":false,"kind":{"col":8,"row":1,"type":"padDown"},"source":{"type":"touch"},"timestamp":12.5,"velocity":1}"#)
        XCTAssertEqual(try decode(json), event)
    }

    func testFrozenPadUpLaunchpad() throws {
        let event = ContributionEvent(
            source: .launchpad,
            kind: .padUp(row: 8, col: 1),
            timestamp: 0,
            hostTime: 0,
            velocity: 0.5,
            isReplay: true
        )
        let json = try encodeSortedJSON(event)
        XCTAssertEqual(json, #"{"hostTime":0,"isReplay":true,"kind":{"col":1,"row":8,"type":"padUp"},"source":{"type":"launchpad"},"timestamp":0,"velocity":0.5}"#)
        XCTAssertEqual(try decode(json), event)
    }

    func testFrozenMidiNote() throws {
        let event = ContributionEvent(
            source: .midiKeyboard,
            kind: .midiNote(note: 60, velocity: 100, on: true),
            timestamp: 3.25,
            hostTime: 42,
            velocity: 0.7874015748031497,
            isReplay: false
        )
        let json = try encodeSortedJSON(event)
        XCTAssertEqual(json, #"{"hostTime":42,"isReplay":false,"kind":{"note":60,"on":true,"type":"midiNote","velocity":100},"source":{"type":"midiKeyboard"},"timestamp":3.25,"velocity":0.7874015748031497}"#)
        XCTAssertEqual(try decode(json), event)
    }

    func testFrozenGap() throws {
        let event = ContributionEvent(
            source: .touch,
            kind: .gap(seconds: 2.5),
            timestamp: 10,
            hostTime: 0
        )
        let json = try encodeSortedJSON(event)
        XCTAssertEqual(json, #"{"hostTime":0,"isReplay":false,"kind":{"seconds":2.5,"type":"gap"},"source":{"type":"touch"},"timestamp":10,"velocity":1}"#)
        XCTAssertEqual(try decode(json), event)
    }

    func testFrozenFutureSource() throws {
        let event = ContributionEvent(
            source: .future("theremin"),
            kind: .padDown(row: 4, col: 4),
            timestamp: 1,
            hostTime: 1
        )
        let json = try encodeSortedJSON(event)
        XCTAssertEqual(json, #"{"hostTime":1,"isReplay":false,"kind":{"col":4,"row":4,"type":"padDown"},"source":{"name":"theremin","type":"future"},"timestamp":1,"velocity":1}"#)
        XCTAssertEqual(try decode(json), event)
    }

    // MARK: - Round trips across all sources

    func testAllSourcesRoundTrip() throws {
        let sources: [ContributionEvent.Source] = [
            .touch, .launchpad, .push, .midiKeyboard, .plugin, .future("x"),
        ]
        for source in sources {
            let event = ContributionEvent(
                source: source,
                kind: .padDown(row: 3, col: 3),
                timestamp: 5,
                hostTime: 99
            )
            let data = try JSONEncoder().encode(event)
            let back = try JSONDecoder().decode(ContributionEvent.self, from: data)
            XCTAssertEqual(back, event)
        }
    }

    // MARK: - Forward / backward compatibility

    func testUnknownSourceDegradesToFuture() throws {
        let json = #"{"hostTime":0,"isReplay":false,"kind":{"col":1,"row":1,"type":"padDown"},"source":{"type":"neuralink"},"timestamp":0,"velocity":1}"#
        let event = try decode(json)
        XCTAssertEqual(event.source, .future("neuralink"))
    }

    func testMissingOptionalFieldsGetDefaults() throws {
        // Older/foreign payloads may omit hostTime / velocity / isReplay.
        let json = #"{"kind":{"col":2,"row":7,"type":"padDown"},"source":{"type":"touch"},"timestamp":1.5}"#
        let event = try decode(json)
        XCTAssertEqual(event.hostTime, 0)
        XCTAssertEqual(event.velocity, 1.0)
        XCTAssertFalse(event.isReplay)
        XCTAssertEqual(event.kind, .padDown(row: 7, col: 2))
    }

    func testUnknownKindFailsDecode() {
        // Unlike Source, an unknown Kind can't be executed or replayed
        // meaningfully — decode must fail loudly so SessionStore can
        // surface a version-mismatch error instead of silently
        // dropping notes.
        let json = #"{"kind":{"type":"hologram"},"source":{"type":"touch"},"timestamp":0}"#
        XCTAssertThrowsError(try decode(json))
    }
}
