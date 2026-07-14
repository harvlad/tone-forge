// ReplayExecutorTests.swift
//
// Replay-only bus handling: isReplay pad events resolve against the
// provided grid and fire trigger/release; live (non-replay) events,
// unassigned pads and non-pad kinds all no-op.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class ReplayExecutorTests: XCTestCase {

    private var bus: ContributionEventBus!
    private var executor: ReplayExecutor!
    private var triggered: [(PadAssignment, Float)] = []
    private var released: [PadAssignment] = []

    private let assignment = PadAssignment(
        chop: Chop(
            idx: 0, startSec: 0, endSec: 1, durationSec: 1,
            kind: "chord", chordSymbol: nil, colorHint: nil
        ),
        stem: "other"
    )

    override func setUp() async throws {
        bus = ContributionEventBus()
        executor = ReplayExecutor(bus: bus)
        triggered = []
        released = []
        // Only the top-left pad is assigned.
        executor.assignmentProvider = { [assignment] pad in
            pad == LaunchpadPad(row: 0, col: 0) ? assignment : nil
        }
        executor.onTrigger = { [weak self] a, v in
            self?.triggered.append((a, v))
        }
        executor.onRelease = { [weak self] a in
            self?.released.append(a)
        }
    }

    private func event(
        _ kind: ContributionEvent.Kind,
        velocity: Double = 1.0,
        isReplay: Bool = true
    ) -> ContributionEvent {
        ContributionEvent(
            source: .launchpad, kind: kind, timestamp: 1,
            hostTime: 0, velocity: velocity, isReplay: isReplay
        )
    }

    func testReplayPadDownTriggersMappedAssignment() {
        // Top-left pad = event (row 8, col 1).
        bus.publish(event(.padDown(row: 8, col: 1), velocity: 0.5))
        XCTAssertEqual(triggered.count, 1)
        XCTAssertEqual(triggered[0].0, assignment)
        XCTAssertEqual(triggered[0].1, 0.5)
    }

    func testLiveEventsIgnored() {
        bus.publish(event(.padDown(row: 8, col: 1), isReplay: false))
        XCTAssertTrue(triggered.isEmpty)
    }

    func testUnassignedPadNoOps() {
        bus.publish(event(.padDown(row: 1, col: 8)))
        XCTAssertTrue(triggered.isEmpty)
    }

    func testReplayPadUpReleases() {
        bus.publish(event(.padUp(row: 8, col: 1)))
        XCTAssertEqual(released, [assignment])
    }

    func testGapAndMidiKindsIgnored() {
        bus.publish(event(.gap(seconds: 2)))
        bus.publish(event(.midiNote(note: 60, velocity: 100, on: true)))
        XCTAssertTrue(triggered.isEmpty)
        XCTAssertTrue(released.isEmpty)
    }

    func testVelocityClampedToUnitRange() {
        bus.publish(event(.padDown(row: 8, col: 1), velocity: 1.7))
        XCTAssertEqual(triggered[0].1, 1.0)
    }
}
