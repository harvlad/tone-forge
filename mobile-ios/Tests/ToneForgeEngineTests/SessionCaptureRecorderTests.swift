// SessionCaptureRecorderTests.swift
//
// Behavioral contract of the P6 session recorder: state machine,
// replay-skip (playback never re-records itself), transport gap
// markers, stable timestamp sort on stop, and the single-sessionId
// autosave identity.

import XCTest
@testable import ToneForgeEngine

@MainActor
final class SessionCaptureRecorderTests: XCTestCase {

    private var bus: ContributionEventBus!
    private var recorder: SessionCaptureRecorder!
    private var now: Double = 0

    override func setUp() async throws {
        bus = ContributionEventBus()
        now = 0
        recorder = SessionCaptureRecorder(bus: bus) { [weak self] in
            self?.now ?? 0
        }
    }

    private func padDown(
        _ row: Int, _ col: Int, at t: Double, isReplay: Bool = false
    ) -> ContributionEvent {
        ContributionEvent(
            source: .touch, kind: .padDown(row: row, col: col),
            timestamp: t, hostTime: 0, isReplay: isReplay)
    }

    private func armDefault() {
        recorder.arm(
            songBackendId: "song-1", appMode: .sample, tempoBpm: nil,
            padMapping: [
                PadAddress(mode: .sample, pad: PadIndex(11)):
                    .packPad(packId: "p", padIdx: 11)
            ])
    }

    // MARK: - State machine

    func testFirstEventTransitionsArmedToRecording() {
        XCTAssertEqual(recorder.state, .idle)
        armDefault()
        XCTAssertEqual(recorder.state, .armed)
        bus.publish(padDown(1, 1, at: 0.5))
        XCTAssertEqual(recorder.state, .recording)
        XCTAssertEqual(recorder.eventCount, 1)
    }

    func testEventsWhileIdleAreIgnored() {
        bus.publish(padDown(1, 1, at: 0.5))
        XCTAssertEqual(recorder.state, .idle)
        XCTAssertEqual(recorder.eventCount, 0)
        XCTAssertNil(recorder.stop())
    }

    func testStopWithoutEventsReturnsNil() {
        armDefault()
        XCTAssertNil(recorder.stop())
        XCTAssertEqual(recorder.state, .idle)
    }

    func testCancelDiscardsTake() {
        armDefault()
        bus.publish(padDown(1, 1, at: 0.5))
        recorder.cancel()
        XCTAssertEqual(recorder.state, .idle)
        XCTAssertEqual(recorder.eventCount, 0)
        XCTAssertNil(recorder.stop())
    }

    func testArmWhileActiveIsNoOp() {
        armDefault()
        bus.publish(padDown(1, 1, at: 0.5))
        let before = recorder.snapshot()
        recorder.arm(
            songBackendId: "other", appMode: .hybrid, tempoBpm: 90,
            padMapping: [:])
        let after = recorder.snapshot()
        XCTAssertEqual(after.sessionId, before.sessionId)
        XCTAssertEqual(after.songBackendId, "song-1")
        XCTAssertEqual(after.events.count, 1)
    }

    // MARK: - Replay skip

    func testReplayEventsAreNeverRecorded() {
        armDefault()
        bus.publish(padDown(1, 1, at: 0.5, isReplay: true))
        XCTAssertEqual(recorder.state, .armed,
                       "replay must not trip armed → recording")
        bus.publish(padDown(2, 2, at: 1.0))
        bus.publish(padDown(3, 3, at: 1.5, isReplay: true))
        let session = recorder.stop()
        XCTAssertEqual(session?.events.count, 1)
        XCTAssertEqual(session?.events.first?.kind,
                       .padDown(row: 2, col: 2))
    }

    // MARK: - Count-in skip

    func testNegativeTimestampEventsAreNeverRecorded() {
        // Sketch count-in: the lead bar runs negative song time with
        // input suppressed at the executor — a jump-the-gun hit that
        // never sounded must not enter the take nor trip the state.
        armDefault()
        bus.publish(padDown(1, 1, at: -0.75))
        XCTAssertEqual(recorder.state, .armed,
                       "count-in hit must not trip armed → recording")
        XCTAssertEqual(recorder.eventCount, 0)
        bus.publish(padDown(2, 2, at: 0.0))
        let session = recorder.stop()
        XCTAssertEqual(session?.events.count, 1)
        XCTAssertEqual(session?.events.first?.timestamp, 0.0)
    }

    // MARK: - Gap markers

    func testSeekInsertsSignedGapAtPreSeekPosition() {
        armDefault()
        bus.publish(padDown(1, 1, at: 10.0))
        recorder.noteTransportSeek(from: 12.0, to: 4.0)
        bus.publish(padDown(2, 2, at: 5.0))
        let session = recorder.stop()!
        let gaps = session.events.filter {
            if case .gap = $0.kind { return true }
            return false
        }
        XCTAssertEqual(gaps.count, 1)
        XCTAssertEqual(gaps[0].kind, .gap(seconds: -8.0))
        XCTAssertEqual(gaps[0].timestamp, 12.0)
        XCTAssertEqual(gaps[0].source, .future("transport"))
    }

    func testPauseInsertsZeroGap() {
        now = 3.0
        armDefault()
        bus.publish(padDown(1, 1, at: 2.0))
        recorder.noteTransportPause()
        let session = recorder.stop()!
        XCTAssertEqual(session.events.last?.kind, .gap(seconds: 0))
        XCTAssertEqual(session.events.last?.timestamp, 3.0)
    }

    func testGapsWhileArmedAreNotInserted() {
        armDefault()
        recorder.noteTransportPause()
        recorder.noteTransportSeek(from: 0, to: 5)
        XCTAssertEqual(recorder.eventCount, 0)
        XCTAssertNil(recorder.stop(),
                     "gap-only takes must not produce a session")
    }

    func testGapEventsFromBusAreIgnored() {
        // Gaps are recorder-made; one arriving on the bus (e.g. a
        // future misbehaving adapter) must not be captured.
        armDefault()
        bus.publish(ContributionEvent(
            source: .touch, kind: .gap(seconds: 1), timestamp: 0,
            hostTime: 0))
        XCTAssertEqual(recorder.eventCount, 0)
        XCTAssertEqual(recorder.state, .armed)
    }

    // MARK: - Stop semantics

    func testStopSortsEventsAscendingStable() {
        armDefault()
        // Backward seek scenario: later events carry earlier stamps.
        bus.publish(padDown(1, 1, at: 10.0))
        bus.publish(padDown(2, 2, at: 4.0))
        bus.publish(padDown(3, 3, at: 4.0))
        let session = recorder.stop()!
        XCTAssertEqual(session.events.map(\.timestamp), [4.0, 4.0, 10.0])
        XCTAssertEqual(session.events[0].kind, .padDown(row: 2, col: 2),
                       "equal timestamps must keep arrival order")
        XCTAssertEqual(session.events[1].kind, .padDown(row: 3, col: 3))
    }

    func testStopSnapshotsArmTimeContext() {
        armDefault()
        bus.publish(padDown(1, 1, at: 0.5))
        let session = recorder.stop()!
        XCTAssertEqual(session.schemaVersion, 1)
        XCTAssertEqual(session.songBackendId, "song-1")
        XCTAssertEqual(session.appMode, .sample)
        XCTAssertEqual(
            session.padMapping[
                PadAddress(mode: .sample, pad: PadIndex(11))],
            .packPad(packId: "p", padIdx: 11))
    }

    // MARK: - Autosave identity

    func testSnapshotsAndStopShareOneSessionId() {
        armDefault()
        bus.publish(padDown(1, 1, at: 0.5))
        let early = recorder.snapshot()
        bus.publish(padDown(2, 2, at: 1.0))
        var autosaved: SessionCapture?
        recorder.onAutosave = { autosaved = $0 }
        let final = recorder.stop()!
        XCTAssertEqual(early.sessionId, final.sessionId,
                       "autosaves must overwrite the same file")
        XCTAssertEqual(autosaved, final,
                       "stop must fire one final autosave")
        // A fresh arm gets a fresh identity.
        armDefault()
        bus.publish(padDown(1, 1, at: 0.5))
        XCTAssertNotEqual(recorder.stop()?.sessionId, final.sessionId)
    }
}
