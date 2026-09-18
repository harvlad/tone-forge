// LaunchpadControlMappingTests.swift
//
// Pins the D-036 function-button assignment table (the approved map,
// desktop-landed). The table is a hardware contract: a CC must mean
// the SAME function on every platform, so any edit here is a parity
// decision, not a refactor.

import XCTest
@testable import ToneForgeEngine

final class LaunchpadControlMappingTests: XCTestCase {

    typealias LP = LaunchpadProMK3Protocol
    typealias Map = LaunchpadControlMapping

    // MARK: - The approved assignment, by CC

    private func function(forCC cc: UInt8) -> Map.Function? {
        LP.controlButton(forCC: cc).flatMap { Map.function(for: $0) }
    }

    func testTransportPair() {
        XCTAssertEqual(function(forCC: 20), .playPause)
        XCTAssertEqual(function(forCC: 10), .globalStop)
    }

    func testTriggerModeTriplet() {
        XCTAssertEqual(function(forCC: 30), .selectMode(.oneShot))
        XCTAssertEqual(function(forCC: 40), .selectMode(.follow))
        XCTAssertEqual(function(forCC: 50), .selectMode(.latch))
    }

    func testLoopLockOnClear() {
        XCTAssertEqual(function(forCC: 60), .loopLockToggle)
    }

    func testGridSizeArrows() {
        XCTAssertEqual(function(forCC: 91), .gridSize(16))
        XCTAssertEqual(function(forCC: 92), .gridSize(64))
    }

    func testSessionAndChord() {
        XCTAssertEqual(function(forCC: 93), .sequencerPanelToggle)
        XCTAssertEqual(function(forCC: 95), .instantGroove)
    }

    func testTrackControlRow() {
        XCTAssertEqual(function(forCC: 1), .recordToggle)
        XCTAssertEqual(function(forCC: 8), .stopAllPads)
        // CC 2–7 → layer columns 0–5 (drums…texture).
        for cc: UInt8 in 2...7 {
            XCTAssertEqual(function(forCC: cc), .layerToggle(Int(cc) - 2))
        }
    }

    func testPatternSelectRow() {
        for cc: UInt8 in 101...108 {
            XCTAssertEqual(function(forCC: cc), .patternSelect(Int(cc) - 101))
        }
    }

    func testSceneColumn() {
        // Top scene arrow = sequencer play/stop; the seven below are
        // section blocks 0…6 top→bottom (song order).
        XCTAssertEqual(function(forCC: 89), .sequencerPlayStop)
        XCTAssertEqual(function(forCC: 79), .sectionJump(0))
        XCTAssertEqual(function(forCC: 69), .sectionJump(1))
        XCTAssertEqual(function(forCC: 59), .sectionJump(2))
        XCTAssertEqual(function(forCC: 49), .sectionJump(3))
        XCTAssertEqual(function(forCC: 39), .sectionJump(4))
        XCTAssertEqual(function(forCC: 29), .sectionJump(5))
        XCTAssertEqual(function(forCC: 19), .sectionJump(6))
    }

    func testReservedButtonsStayUnmapped() {
        // Shift + logo reserved; ▲▼ (80/70), Note (94), Custom (96),
        // Sequencer (97), Projects (98) free for future tiers.
        for cc: UInt8 in [90, 99, 80, 70, 94, 96, 97, 98] {
            XCTAssertNil(function(forCC: cc), "CC \(cc) must stay unmapped")
        }
    }

    // MARK: - CC inverse

    func testCCInverseRoundTrips() {
        // Every decodable CC round-trips through cc(for:) — the LED
        // frame is keyed by CC, so the inverse must agree with the
        // protocol's decoder.
        for cc: UInt8 in 0...127 {
            guard let button = LP.controlButton(forCC: cc) else { continue }
            XCTAssertEqual(Map.cc(for: button), Int(cc))
        }
    }

}
