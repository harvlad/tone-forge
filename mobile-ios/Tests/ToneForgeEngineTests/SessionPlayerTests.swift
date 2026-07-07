// SessionPlayerTests.swift
//
// Replay contract: events re-fire through the bus with
// `isReplay: true` exactly when the transport passes their
// timestamp, gaps are never re-published, seek never retro-fires,
// and a recorder listening on the same bus ignores the replay (the
// no-self-re-record loop closure).

import XCTest
@testable import ToneForgeEngine

@MainActor
final class SessionPlayerTests: XCTestCase {

    private var bus: ContributionEventBus!
    private var player: SessionPlayer!
    private var now: Double = 0
    private var received: [ContributionEvent] = []

    override func setUp() async throws {
        bus = ContributionEventBus()
        now = 0
        received = []
        player = SessionPlayer(bus: bus) { [weak self] in
            self?.now ?? 0
        }
        bus.subscribe { [weak self] event in
            self?.received.append(event)
        }
    }

    private func makeSession(
        _ stamps: [Double], gapAt: Double? = nil
    ) -> SessionCapture {
        var events = stamps.enumerated().map { idx, t in
            ContributionEvent(
                source: .touch,
                kind: .padDown(row: 1, col: 1 + (idx % 8)),
                timestamp: t, hostTime: 0)
        }
        if let gapAt {
            events.append(ContributionEvent(
                source: .future("transport"), kind: .gap(seconds: 0),
                timestamp: gapAt, hostTime: 0))
            events.sort { $0.timestamp < $1.timestamp }
        }
        return SessionCapture(
            sessionId: UUID(), songBackendId: nil, appMode: .sample,
            capturedAt: Date(), tempoBpm: nil, events: events,
            padMapping: [:])
    }

    func testFiresEventsUpToNowWithReplayFlag() {
        player.load(makeSession([0.0, 0.5, 1.0, 2.0]))
        now = 1.0
        player.tickForTests()
        XCTAssertEqual(received.count, 3)
        XCTAssertTrue(received.allSatisfy(\.isReplay))
        // The rest arrives when time reaches it — and never twice.
        now = 2.0
        player.tickForTests()
        XCTAssertEqual(received.count, 4)
        player.tickForTests()
        XCTAssertEqual(received.count, 4)
    }

    func testGapsAreNeverRepublished() {
        player.load(makeSession([0.0, 1.0], gapAt: 0.5))
        now = 2.0
        player.tickForTests()
        XCTAssertEqual(received.count, 2)
        XCTAssertFalse(received.contains {
            if case .gap = $0.kind { return true }
            return false
        })
    }

    func testLoadStartsCursorAtCurrentTransportPosition() {
        now = 1.5
        player.load(makeSession([0.0, 1.0, 2.0]))
        now = 2.5
        player.tickForTests()
        XCTAssertEqual(received.count, 1,
                       "events behind the load position never fire")
        XCTAssertEqual(received[0].timestamp, 2.0)
    }

    func testSeekSkipsWithoutRetroFiring() {
        player.load(makeSession([0.0, 1.0, 2.0, 3.0]))
        player.seek(to: 2.5)
        now = 3.5
        player.tickForTests()
        XCTAssertEqual(received.map(\.timestamp), [3.0])
        // Seek backward re-fires from there.
        player.seek(to: 0.5)
        player.tickForTests()
        XCTAssertEqual(received.map(\.timestamp), [3.0, 1.0, 2.0, 3.0])
    }

    func testClearStopsAndDropsSession() {
        player.load(makeSession([0.0]))
        player.clear()
        XCTAssertNil(player.session)
        now = 1.0
        player.tickForTests()
        XCTAssertTrue(received.isEmpty)
    }

    func testRecorderOnSameBusIgnoresReplay() {
        let recorder = SessionCaptureRecorder(bus: bus) { [weak self] in
            self?.now ?? 0
        }
        recorder.arm(
            songBackendId: nil, appMode: .sample, tempoBpm: nil,
            padMapping: [:])
        player.load(makeSession([0.0, 1.0]))
        now = 2.0
        player.tickForTests()
        XCTAssertEqual(received.count, 2, "replay reached the bus")
        XCTAssertEqual(recorder.eventCount, 0,
                       "replay must never re-record itself")
        XCTAssertEqual(recorder.state, .armed)
    }
}
