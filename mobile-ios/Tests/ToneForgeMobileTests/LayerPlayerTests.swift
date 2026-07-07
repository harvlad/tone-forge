// LayerPlayerTests.swift
//
// Deterministic cursor + dispatch tests for `LayerPlayer`. We avoid
// the timer-driven tick loop by owning a mock clock and by invoking
// state transitions directly — `seek(to:)` advances the cursor and
// firing `start()` after `addLayer` under a mocked clock is sufficient
// to prove ordering + skipping semantics.

import XCTest
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class LayerPlayerTests: XCTestCase {

    private var currentSongTime: Double = 0

    private var samplesOn: [Int] = []
    private var samplesOff: [Int] = []
    private var notesOn: [(Int, Double)] = []
    private var notesOff: [Int] = []

    private var player: LayerPlayer!

    override func setUp() async throws {
        try await super.setUp()
        currentSongTime = 0
        samplesOn = []
        samplesOff = []
        notesOn = []
        notesOff = []
        player = LayerPlayer(
            clockNow: { [weak self] in self?.currentSongTime ?? 0 },
            onSampleOn: { [weak self] pad, _ in self?.samplesOn.append(pad) },
            onSampleOff: { [weak self] pad, _ in self?.samplesOff.append(pad) },
            onNoteOn: { [weak self] midi, vel in self?.notesOn.append((midi, vel)) },
            onNoteOff: { [weak self] midi in self?.notesOff.append(midi) }
        )
    }

    override func tearDown() async throws {
        player = nil
        try await super.tearDown()
    }

    // MARK: - Cursor initialization

    func testAddLayerCursorSkipsPastEvents() async {
        currentSongTime = 5.0
        let tl = makeTimeline(events: [
            (.sampleOn, 1.0, 0),
            (.sampleOn, 3.0, 1),
            (.sampleOn, 7.0, 2),
        ])
        player.addLayer(tl)
        // Manually trigger the tick — nothing should fire yet, only
        // event at t=7 remains ahead of the clock at 5.
        callTick()
        XCTAssertEqual(samplesOn, [])
        currentSongTime = 8.0
        callTick()
        XCTAssertEqual(samplesOn, [2])
    }

    // MARK: - Dispatch semantics

    func testDispatchFiresEventsInOrderAsClockAdvances() async {
        let tl = makeTimeline(events: [
            (.sampleOn, 0.5, 0),
            (.sampleOn, 1.0, 1),
            (.sampleOn, 2.0, 2),
        ])
        player.addLayer(tl)
        currentSongTime = 0.6
        callTick()
        XCTAssertEqual(samplesOn, [0])
        currentSongTime = 1.5
        callTick()
        XCTAssertEqual(samplesOn, [0, 1])
        currentSongTime = 5.0
        callTick()
        XCTAssertEqual(samplesOn, [0, 1, 2])
    }

    func testSeekRewindsCursor() async {
        let tl = makeTimeline(events: [
            (.sampleOn, 1.0, 10),
            (.sampleOn, 2.0, 20),
            (.sampleOn, 3.0, 30),
        ])
        player.addLayer(tl)
        currentSongTime = 2.5
        callTick()
        XCTAssertEqual(samplesOn, [10, 20])

        // Seek back to 0. Cursor rewinds; events at t≥0 fire again.
        currentSongTime = 0
        player.seek(to: 0)
        callTick()
        XCTAssertEqual(samplesOn, [10, 20])   // nothing fires yet
        currentSongTime = 1.5
        callTick()
        XCTAssertEqual(samplesOn, [10, 20, 10])
    }

    // MARK: - Kind fan-out

    func testDispatchRoutesEachKindToRightCallback() async {
        let events: [LayerEvent] = [
            LayerEvent(kind: .sampleOn, songTimeSec: 0.1,
                       params: .init(padIdx: 3, velocity: 1.0)),
            LayerEvent(kind: .noteOn, songTimeSec: 0.2,
                       params: .init(midiNote: 60, velocity: 0.8)),
            LayerEvent(kind: .sampleOff, songTimeSec: 0.3,
                       params: .init(padIdx: 3)),
            LayerEvent(kind: .noteOff, songTimeSec: 0.4,
                       params: .init(midiNote: 60)),
        ]
        let tl = LayerTimeline(
            layerId: UUID().uuidString,
            analysisId: "song-A",
            name: "T",
            createdAtEpoch: 0,
            durationSec: 0.4,
            events: events,
            activePackId: nil
        )
        player.addLayer(tl)
        currentSongTime = 0.5
        callTick()
        XCTAssertEqual(samplesOn, [3])
        XCTAssertEqual(samplesOff, [3])
        XCTAssertEqual(notesOn.map { $0.0 }, [60])
        XCTAssertEqual(notesOn.first?.1 ?? 0, 0.8, accuracy: 1e-9)
        XCTAssertEqual(notesOff, [60])
    }

    // MARK: - packId routing (multi-pack replay)

    func testSampleCallbacksReceivePackIdOverride() async {
        var received: [(Int, String?)] = []
        var receivedOff: [(Int, String?)] = []
        let p = LayerPlayer(
            clockNow: { [weak self] in self?.currentSongTime ?? 0 },
            onSampleOn: { pad, packId in received.append((pad, packId)) },
            onSampleOff: { pad, packId in receivedOff.append((pad, packId)) },
            onNoteOn: { _, _ in },
            onNoteOff: { _ in }
        )
        let events: [LayerEvent] = [
            // Explicit override — recorded on a non-active carousel page.
            LayerEvent(kind: .sampleOn, songTimeSec: 0.1,
                       params: .init(padIdx: 1, velocity: 1.0,
                                     packIdOverride: "packA")),
            // No override — pre-multi-pack event, falls back to the
            // timeline's activePackId.
            LayerEvent(kind: .sampleOn, songTimeSec: 0.2,
                       params: .init(padIdx: 2, velocity: 1.0)),
            LayerEvent(kind: .sampleOff, songTimeSec: 0.3,
                       params: .init(padIdx: 1, packIdOverride: "packA")),
            LayerEvent(kind: .sampleOff, songTimeSec: 0.4,
                       params: .init(padIdx: 2)),
        ]
        let tl = LayerTimeline(
            layerId: UUID().uuidString,
            analysisId: "song-A",
            name: "T",
            createdAtEpoch: 0,
            durationSec: 0.4,
            events: events,
            activePackId: "basePack"
        )
        p.addLayer(tl)
        currentSongTime = 0.5
        p.tickForTests()

        XCTAssertEqual(received.map { $0.0 }, [1, 2])
        XCTAssertEqual(received.map { $0.1 }, ["packA", "basePack"])
        XCTAssertEqual(receivedOff.map { $0.0 }, [1, 2])
        XCTAssertEqual(receivedOff.map { $0.1 }, ["packA", "basePack"])
    }

    func testNilOverrideAndNilActivePackIdYieldsNil() async {
        var received: [String?] = []
        let p = LayerPlayer(
            clockNow: { [weak self] in self?.currentSongTime ?? 0 },
            onSampleOn: { _, packId in received.append(packId) },
            onSampleOff: { _, _ in },
            onNoteOn: { _, _ in },
            onNoteOff: { _ in }
        )
        // Legacy timeline: no pack info anywhere — the callback gets
        // nil and the scheduler resolves against its active pack.
        let tl = makeTimeline(events: [(.sampleOn, 0.1, 0)])
        p.addLayer(tl)
        currentSongTime = 0.5
        p.tickForTests()
        XCTAssertEqual(received, [nil])
    }

    // MARK: - Layer registration

    func testRemoveLayerStopsDispatch() async {
        let tl = makeTimeline(events: [(.sampleOn, 1.0, 7)])
        player.addLayer(tl)
        player.removeLayer(layerId: tl.layerId)
        XCTAssertEqual(player.activeLayerIds.count, 0)
        currentSongTime = 5.0
        callTick()
        XCTAssertEqual(samplesOn, [])
    }

    func testClearDropsEverything() async {
        let a = makeTimeline(events: [(.sampleOn, 1.0, 1)])
        let b = makeTimeline(events: [(.sampleOn, 1.0, 2)])
        player.addLayer(a)
        player.addLayer(b)
        XCTAssertEqual(player.activeLayerIds.count, 2)
        player.clear()
        XCTAssertEqual(player.activeLayerIds.count, 0)
    }

    // MARK: - Helpers

    /// Directly kick the internal tick by seeking(now) → no-op advance
    /// + then advancing the cursor via one tick equivalent. Because
    /// `tick` is private, we re-add via public API to advance: use
    /// `seek(to:)` then rely on `addLayer` cursor logic. Simpler: call
    /// `start()` briefly and then `stop()` — but Timer isn't reliable
    /// in tests. Cleanest: use `player.performTick()` via a small
    /// test-only hook. Absent that, invoke seek(to:) semantics.
    ///
    /// Implementation note: `LayerPlayer.tick` is private, so we
    /// simulate its externally-observable effect by calling
    /// `player.seek(to: currentSongTime)` which rewinds cursors to
    /// the first event ≥ currentSongTime — NOT what we want.
    ///
    /// Actual approach: temporarily expose `tickForTests()` on the
    /// player via an extension in tests, or accept that this test
    /// uses the public API. Since we can't reach private members
    /// without `@testable`, we already imported `@testable` — see
    /// helper below that calls `.perform(...)`.
    private func callTick() {
        player.tickForTests()
    }

    private func makeTimeline(
        events: [(LayerEvent.Kind, Double, Int)]
    ) -> LayerTimeline {
        let evs = events.map { kind, t, pad -> LayerEvent in
            LayerEvent(
                kind: kind, songTimeSec: t,
                params: .init(padIdx: pad, velocity: 1.0)
            )
        }
        return LayerTimeline(
            layerId: UUID().uuidString,
            analysisId: "song-A",
            name: "T",
            createdAtEpoch: 0,
            durationSec: events.last?.1 ?? 0,
            events: evs,
            activePackId: nil
        )
    }
}
