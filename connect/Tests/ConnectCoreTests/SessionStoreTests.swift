//
// SessionStoreTests.swift
//
// Audio-Ownership Pivot, post-pivot follow-up. SessionStore is the
// in-Connect consumer of the v2 `session_data` and `transport_state`
// frames; these tests pin the wire-dict → struct decoding contract
// so a future producer-side change in JAM that drifts the field
// names breaks here, before the GUI's chord HUD ever sees a stale
// snapshot.
//
// We exercise the public API (ingest + queries) rather than the
// private decode helpers, because the helpers are deliberately
// `private static` — the wire contract belongs to the public
// ingest path.
//

import XCTest
@testable import ConnectCore

final class SessionStoreTests: XCTestCase {

    // MARK: - session_data ingest

    func testIngestFullSessionDataSnapshotPopulatesAllFields() {
        let store = SessionStore()
        let dict: [String: Any] = [
            "session_id": "abc123",
            "song":       ["id": "song_42", "title": "Wish You Were Here"],
            "bpm":        120.0,
            "key":        ["root": 7, "scale": "Major"],
            "chord_progression": [
                ["symbol": "G:maj",  "start_s": 0.0,  "end_s": 4.0],
                ["symbol": "D:maj",  "start_s": 4.0,  "end_s": 8.0],
                ["symbol": "Em:min", "start_s": 8.0,  "end_s": 12.0]
            ],
            "section_markers": [
                ["name": "intro", "start_s": 0.0,  "end_s": 8.0],
                ["name": "verse", "start_s": 8.0,  "end_s": 24.0]
            ],
            "loop_markers": ["start_s": 0.0, "end_s": 30.0]
        ]
        store.ingestSessionData(dict)
        XCTAssertEqual(store.sessionId, "abc123")
        XCTAssertEqual(store.song,      SessionStore.SongInfo(id: "song_42", title: "Wish You Were Here"))
        XCTAssertEqual(store.bpm ?? .nan, 120.0, accuracy: 0.0001)
        XCTAssertEqual(store.key,       SessionStore.KeyInfo(root: 7, scale: "Major"))
        XCTAssertEqual(store.chordProgression.count, 3)
        XCTAssertEqual(store.chordProgression[0].symbol, "G:maj")
        XCTAssertEqual(store.sectionMarkers.count, 2)
        XCTAssertEqual(store.sectionMarkers[1].name, "verse")
        XCTAssertEqual(store.loopMarkers, SessionStore.LoopMarkers(startSec: 0.0, endSec: 30.0))
    }

    /// JAM rebuilds session_data from scratch on every push, so the
    /// contract is "missing field clears the cache" — not "missing
    /// field preserves the previous value". This test guards
    /// against accidental partial-update semantics creeping in.
    func testIngestSessionDataWithMissingFieldsClearsPreviousValues() {
        let store = SessionStore()
        store.ingestSessionData([
            "session_id": "abc",
            "song":       ["id": "s1", "title": "T1"],
            "bpm":        100.0,
            "chord_progression": [["symbol": "C:maj", "start_s": 0.0, "end_s": 1.0]]
        ])
        XCTAssertNotNil(store.song)
        XCTAssertNotNil(store.bpm)
        XCTAssertFalse(store.chordProgression.isEmpty)

        // Empty push: just a session_id, nothing else.
        store.ingestSessionData(["session_id": "abc"])
        XCTAssertNil(store.song)
        XCTAssertNil(store.bpm)
        XCTAssertNil(store.key)
        XCTAssertTrue(store.chordProgression.isEmpty)
        XCTAssertTrue(store.sectionMarkers.isEmpty)
        XCTAssertNil(store.loopMarkers)
    }

    func testIngestSessionDataDropsMalformedChordEntries() {
        let store = SessionStore()
        store.ingestSessionData([
            "chord_progression": [
                ["symbol": "C:maj", "start_s": 0.0,  "end_s": 4.0],   // good
                ["start_s": 4.0, "end_s": 8.0],                       // missing symbol
                ["symbol": "G:maj", "start_s": 8.0],                  // missing end_s
                ["symbol": "D:maj", "start_s": 12.0, "end_s": 8.0],   // inverted
                ["symbol": "A:min", "start_s": 16.0, "end_s": 20.0]   // good
            ]
        ])
        XCTAssertEqual(store.chordProgression.map { $0.symbol }, ["C:maj", "A:min"])
    }

    func testIngestSessionDataRejectsOutOfRangeKeyRoot() {
        let store = SessionStore()
        store.ingestSessionData(["key": ["root": 99, "scale": "Major"]])
        XCTAssertNil(store.key)
    }

    func testIngestSessionDataAcceptsIntegerBpm() {
        let store = SessionStore()
        // JSONSerialization sometimes decodes whole numbers as Int.
        // The store should accept either Int or Double for bpm.
        store.ingestSessionData(["bpm": 95])
        XCTAssertEqual(store.bpm ?? .nan, 95.0, accuracy: 0.0001)
    }

    // MARK: - transport_state ingest

    func testIngestTransportReplacesState() {
        let store = SessionStore()
        XCTAssertEqual(store.transport, SessionStore.Transport.stopped)
        store.ingestTransport(playing: true, positionSec: 12.5)
        XCTAssertEqual(store.transport, SessionStore.Transport(isPlaying: true, positionSec: 12.5))
        store.ingestTransport(playing: false, positionSec: 0.0)
        XCTAssertEqual(store.transport, SessionStore.Transport(isPlaying: false, positionSec: 0.0))
    }

    /// A negative position or NaN must not propagate into UI math.
    /// Floor at zero; treat non-finite as zero.
    func testIngestTransportClampsNegativeAndNonFinitePosition() {
        let store = SessionStore()
        store.ingestTransport(playing: true, positionSec: -3.0)
        XCTAssertEqual(store.transport.positionSec, 0.0, accuracy: 0.0001)
        store.ingestTransport(playing: true, positionSec: .nan)
        XCTAssertEqual(store.transport.positionSec, 0.0, accuracy: 0.0001)
        store.ingestTransport(playing: true, positionSec: .infinity)
        XCTAssertEqual(store.transport.positionSec, 0.0, accuracy: 0.0001)
    }

    // MARK: - Queries

    func testChordAtReturnsActiveChordOrNil() {
        let store = SessionStore()
        store.ingestSessionData([
            "chord_progression": [
                ["symbol": "C:maj", "start_s": 0.0, "end_s": 4.0],
                ["symbol": "G:maj", "start_s": 4.0, "end_s": 8.0]
            ]
        ])
        XCTAssertEqual(store.chord(at: 0.0)?.symbol, "C:maj")
        XCTAssertEqual(store.chord(at: 3.99)?.symbol, "C:maj")
        // Half-open boundary: 4.0 belongs to G:maj, not C:maj.
        XCTAssertEqual(store.chord(at: 4.0)?.symbol, "G:maj")
        XCTAssertEqual(store.chord(at: 7.5)?.symbol, "G:maj")
        // Beyond the last chord = nil.
        XCTAssertNil(store.chord(at: 100.0))
    }

    func testSectionAtReturnsActiveSectionOrNil() {
        let store = SessionStore()
        store.ingestSessionData([
            "section_markers": [
                ["name": "intro", "start_s": 0.0, "end_s": 8.0],
                ["name": "verse", "start_s": 8.0, "end_s": 24.0]
            ]
        ])
        XCTAssertEqual(store.section(at: 0.0)?.name,  "intro")
        XCTAssertEqual(store.section(at: 8.0)?.name,  "verse")
        XCTAssertEqual(store.section(at: 23.9)?.name, "verse")
        XCTAssertNil(store.section(at: 24.0))
    }

    // MARK: - reset + onChange

    func testResetClearsEverything() {
        let store = SessionStore()
        store.ingestSessionData([
            "session_id": "abc",
            "song":       ["id": "s1", "title": "T1"],
            "bpm":        120.0
        ])
        store.ingestTransport(playing: true, positionSec: 12.0)
        XCTAssertNotNil(store.song)
        XCTAssertEqual(store.transport.isPlaying, true)
        store.reset()
        XCTAssertNil(store.sessionId)
        XCTAssertNil(store.song)
        XCTAssertNil(store.bpm)
        XCTAssertTrue(store.chordProgression.isEmpty)
        XCTAssertEqual(store.transport, SessionStore.Transport.stopped)
    }

    /// onChange must fire on every successful ingest AND on reset.
    /// The callback is dispatched async on the main queue, so we
    /// drain the queue with an XCTestExpectation rather than
    /// asserting synchronously.
    func testOnChangeFiresAfterEveryIngest() {
        let store = SessionStore()
        let exp = expectation(description: "onChange fires three times (sessionData + transport + reset)")
        exp.expectedFulfillmentCount = 3
        store.onChange = { exp.fulfill() }
        store.ingestSessionData(["session_id": "abc"])
        store.ingestTransport(playing: true, positionSec: 1.0)
        store.reset()
        waitForExpectations(timeout: 1.0)
    }
}
