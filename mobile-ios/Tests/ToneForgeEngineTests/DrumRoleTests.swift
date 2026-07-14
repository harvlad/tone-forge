// DrumRoleTests.swift
//
// Beat Capture (D-024): freezes DrumRole wire values and the pack-pad
// mapping. These are persisted inside patterns, so drift breaks
// existing saved beats.

import XCTest
@testable import ToneForgeEngine

final class DrumRoleTests: XCTestCase {

    func testRawValuesFrozen() {
        XCTAssertEqual(DrumRole.kick.rawValue, "kick")
        XCTAssertEqual(DrumRole.snare.rawValue, "snare")
        XCTAssertEqual(DrumRole.closedHat.rawValue, "closed_hat")
        XCTAssertEqual(DrumRole.openHat.rawValue, "open_hat")
        XCTAssertEqual(DrumRole.clap.rawValue, "clap")
        XCTAssertEqual(DrumRole.rim.rawValue, "rim")
        XCTAssertEqual(DrumRole.perc.rawValue, "perc")
    }

    func testPadIdxFrozen() {
        XCTAssertEqual(DrumRole.kick.padIdx, 0)
        XCTAssertEqual(DrumRole.snare.padIdx, 1)
        XCTAssertEqual(DrumRole.closedHat.padIdx, 2)
        XCTAssertEqual(DrumRole.openHat.padIdx, 3)
        XCTAssertEqual(DrumRole.clap.padIdx, 4)
        XCTAssertEqual(DrumRole.rim.padIdx, 5)
        XCTAssertEqual(DrumRole.perc.padIdx, 6)
    }

    func testAllCasesCount() {
        XCTAssertEqual(DrumRole.allCases.count, 7)
    }

    func testChopRefResolvesToBeatKit() {
        // beatkit audio is bundled, so roles resolve to the dedicated
        // kit at their frozen padIdx (0-6).
        for role in DrumRole.allCases {
            guard case let .packPad(packId, padIdx) = role.chopRef else {
                return XCTFail("expected packPad for \(role)")
            }
            XCTAssertEqual(packId, "beatkit")
            XCTAssertEqual(padIdx, role.padIdx)
        }
    }

    func testFallbackKeyRoles() {
        // The starter fallback mapping stays intact for the flag-off path.
        XCTAssertEqual(BeatKit.fallbackPadIdx(for: .kick), 4)
        XCTAssertEqual(BeatKit.fallbackPadIdx(for: .snare), 5)
        XCTAssertEqual(BeatKit.fallbackPadIdx(for: .closedHat), 6)
        XCTAssertEqual(BeatKit.fallbackPadIdx(for: .perc), 7)
    }

    func testCodableRoundTrip() throws {
        let data = try JSONEncoder().encode(DrumRole.closedHat)
        let decoded = try JSONDecoder().decode(DrumRole.self, from: data)
        XCTAssertEqual(decoded, .closedHat)
    }
}
