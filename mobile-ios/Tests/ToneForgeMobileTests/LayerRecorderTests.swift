// LayerRecorderTests.swift
//
// State-machine + capture semantics for `LayerRecorder`. Uses a
// throw-away temp LayerStore so `stopAndSave` is exercisable.

import XCTest
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class LayerRecorderTests: XCTestCase {

    private var tmpRoot: URL!
    private var store: LayerStore!
    private var recorder: LayerRecorder!

    override func setUp() async throws {
        try await super.setUp()
        tmpRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("recorder-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: tmpRoot, withIntermediateDirectories: true
        )
        store = LayerStore(root: tmpRoot)
        recorder = LayerRecorder(store: store)
    }

    override func tearDown() async throws {
        if let root = tmpRoot { try? FileManager.default.removeItem(at: root) }
        tmpRoot = nil
        store = nil
        recorder = nil
        try await super.tearDown()
    }

    // MARK: - State machine

    func testInitialStateIsIdle() {
        XCTAssertEqual(recorder.state, .idle)
        XCTAssertEqual(recorder.eventCount, 0)
    }

    func testArmTransitionsIdleToArmed() {
        recorder.arm(analysisId: "song-A", activePackId: "starter")
        XCTAssertEqual(recorder.state, .armed)
    }

    func testArmIsNoOpWhileRecording() {
        recorder.arm(analysisId: "song-A", activePackId: nil)
        recorder.append(makeEvent(pad: 0, songTime: 0.1))
        XCTAssertEqual(recorder.state, .recording)
        // Second arm should be ignored.
        recorder.arm(analysisId: "different-song", activePackId: nil)
        // Still recording the original song's session.
        XCTAssertEqual(recorder.state, .recording)
    }

    func testFirstEventFlipsArmedToRecording() {
        recorder.arm(analysisId: "song-A", activePackId: nil)
        XCTAssertEqual(recorder.state, .armed)
        recorder.append(makeEvent(pad: 0, songTime: 0.0))
        XCTAssertEqual(recorder.state, .recording)
        XCTAssertEqual(recorder.eventCount, 1)
    }

    func testAppendWhileIdleDropsEvents() {
        recorder.append(makeEvent(pad: 0, songTime: 0.0))
        XCTAssertEqual(recorder.state, .idle)
        XCTAssertEqual(recorder.eventCount, 0)
    }

    // MARK: - Stop + finalize

    func testStopReturnsTimelineWithCapturedEvents() {
        recorder.arm(analysisId: "song-A", activePackId: "shoegaze-textures")
        recorder.append(makeEvent(pad: 0, songTime: 1.0))
        recorder.append(makeEvent(pad: 1, songTime: 2.0))
        recorder.append(makeEvent(pad: 2, songTime: 3.5))

        let tl = recorder.stop()
        XCTAssertNotNil(tl)
        XCTAssertEqual(tl?.analysisId, "song-A")
        XCTAssertEqual(tl?.activePackId, "shoegaze-textures")
        XCTAssertEqual(tl?.events.count, 3)
        XCTAssertEqual(tl?.durationSec, 3.5)
        XCTAssertEqual(recorder.state, .idle)
    }

    func testStopReturnsNilWhenNoEvents() {
        recorder.arm(analysisId: "song-A", activePackId: nil)
        let tl = recorder.stop()
        XCTAssertNil(tl)
        XCTAssertEqual(recorder.state, .idle)
    }

    func testStopSortsEventsBySongTime() {
        recorder.arm(analysisId: "song-A", activePackId: nil)
        // Out-of-order arrivals.
        recorder.append(makeEvent(pad: 0, songTime: 3.0))
        recorder.append(makeEvent(pad: 1, songTime: 1.0))
        recorder.append(makeEvent(pad: 2, songTime: 2.0))

        let tl = recorder.stop()
        let times = tl?.events.map { $0.songTimeSec }
        XCTAssertEqual(times, [1.0, 2.0, 3.0])
    }

    // MARK: - Cancel

    func testCancelDiscardsBuffer() {
        recorder.arm(analysisId: "song-A", activePackId: nil)
        recorder.append(makeEvent(pad: 0, songTime: 0.5))
        XCTAssertEqual(recorder.eventCount, 1)

        recorder.cancel()
        XCTAssertEqual(recorder.state, .idle)
        XCTAssertEqual(recorder.eventCount, 0)
        // A subsequent stop shouldn't resurrect the cancelled events.
        XCTAssertNil(recorder.stop())
    }

    // MARK: - stopAndSave persistence

    func testStopAndSavePersistsToStore() throws {
        recorder.arm(analysisId: "song-Z", activePackId: nil)
        recorder.append(makeEvent(pad: 0, songTime: 0.25))
        recorder.append(makeEvent(pad: 1, songTime: 0.75))

        let tl = try recorder.stopAndSave()
        XCTAssertNotNil(tl)
        let listed = store.list(analysisId: "song-Z")
        XCTAssertEqual(listed.count, 1)
        XCTAssertEqual(listed.first?.layerId, tl?.layerId)
    }

    func testStopAndSaveWithNothingCapturedIsNoOp() throws {
        recorder.arm(analysisId: "song-Y", activePackId: nil)
        let tl = try recorder.stopAndSave()
        XCTAssertNil(tl)
        XCTAssertEqual(store.list(analysisId: "song-Y").count, 0)
    }

    // MARK: - Helpers

    private func makeEvent(pad: Int, songTime: Double) -> LayerEvent {
        LayerEvent(
            kind: .sampleOn,
            songTimeSec: songTime,
            params: LayerEvent.Params(padIdx: pad, velocity: 1.0)
        )
    }
}
