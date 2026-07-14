// RecordingModelTests.swift
//
// The P4 coordinator against a temp-dir store, a scripted clock and
// a real ContributionEventBus: arm → first event starts recording,
// stop persists via the recorder's final autosave, replay-marked
// events don't record, cancel drops the take, replay state tracks
// the loaded session.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class RecordingModelTests: XCTestCase {

    private var root: URL!
    private var bus: ContributionEventBus!
    private var now: Double = 0
    private var model: RecordingModel!

    override func setUp() async throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        bus = ContributionEventBus()
        now = 0
        model = RecordingModel(
            bus: bus,
            clockNow: { [weak self] in self?.now ?? 0 },
            store: SessionStore(root: root)
        )
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: root)
    }

    private func padDown(
        at timestamp: Double, isReplay: Bool = false
    ) -> ContributionEvent {
        ContributionEvent(
            source: .launchpad, kind: .padDown(row: 8, col: 1),
            timestamp: timestamp, hostTime: 0, isReplay: isReplay
        )
    }

    func testArmThenFirstEventStartsRecording() {
        model.arm(songBackendId: "song-1", tempoBpm: 100)
        XCTAssertEqual(model.recorder.state, .armed)
        now = 2.0
        bus.publish(padDown(at: 2.0))
        XCTAssertEqual(model.recorder.state, .recording)
        XCTAssertEqual(model.recorder.eventCount, 1)
    }

    func testStopPersistsTake() {
        model.arm(songBackendId: "song-1", tempoBpm: 100)
        bus.publish(padDown(at: 1.0))
        let take = model.stopRecording()
        XCTAssertNotNil(take)
        XCTAssertEqual(model.recorder.state, .idle)
        XCTAssertEqual(model.recordings.count, 1)
        XCTAssertEqual(model.recordings[0].sessionId, take?.sessionId)
        XCTAssertEqual(model.recordings[0].songBackendId, "song-1")
        XCTAssertTrue(model.recordings[0].padMapping.isEmpty)
    }

    func testReplayEventsAreNotCaptured() {
        model.arm(songBackendId: nil, tempoBpm: nil)
        bus.publish(padDown(at: 1.0, isReplay: true))
        XCTAssertEqual(model.recorder.state, .armed)
        XCTAssertEqual(model.recorder.eventCount, 0)
    }

    func testCancelDropsTake() {
        model.arm(songBackendId: "song-1", tempoBpm: nil)
        bus.publish(padDown(at: 1.0))
        model.cancelRecording()
        model.refresh()
        XCTAssertEqual(model.recorder.state, .idle)
        XCTAssertTrue(model.recordings.isEmpty)
    }

    func testTransportPauseAddsGapOnlyWhileRecording() {
        model.arm(songBackendId: nil, tempoBpm: nil)
        // Armed but not yet recording: pause must not start the take.
        model.recorder.noteTransportPause()
        XCTAssertEqual(model.recorder.state, .armed)
        bus.publish(padDown(at: 1.0))
        model.recorder.noteTransportPause()
        let take = model.stopRecording()
        let gaps = take?.events.filter {
            if case .gap = $0.kind { return true }
            return false
        }
        XCTAssertEqual(gaps?.count, 1)
    }

    func testReplayStateTracksSession() {
        model.arm(songBackendId: nil, tempoBpm: nil)
        bus.publish(padDown(at: 1.0))
        guard let take = model.stopRecording() else {
            return XCTFail("expected take")
        }
        model.startReplay(take)
        XCTAssertEqual(model.replayingSessionId, take.sessionId)
        XCTAssertTrue(model.player.isPlaying)
        model.stopReplay()
        XCTAssertNil(model.replayingSessionId)
        XCTAssertFalse(model.player.isPlaying)
    }

    func testDeleteRemovesTakeAndStopsItsReplay() {
        model.arm(songBackendId: nil, tempoBpm: nil)
        bus.publish(padDown(at: 1.0))
        guard let take = model.stopRecording() else {
            return XCTFail("expected take")
        }
        model.startReplay(take)
        model.delete(take)
        XCTAssertNil(model.replayingSessionId)
        XCTAssertTrue(model.recordings.isEmpty)
    }
}
