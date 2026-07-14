// StemMixModelTests.swift
//
// Mute/solo/gain matrix semantics — must match the mobile StemPlayer
// and web mixer: mute wins, any-solo silences non-soloed stems,
// gains clamp 0…1, and every effective-gain change signals the
// audio layer exactly once.

import XCTest
@testable import JamDesktopCore

@MainActor
final class StemMixModelTests: XCTestCase {

    private var mix: StemMixModel!

    override func setUp() async throws {
        mix = StemMixModel()
        mix.load(roles: ["drums", "bass", "vocals", "other"])
    }

    func testDefaultsAreUnityUnmutedUnsoloed() {
        XCTAssertEqual(mix.stems.map(\.role), ["drums", "bass", "vocals", "other"])
        for role in ["drums", "bass", "vocals", "other"] {
            XCTAssertEqual(mix.effectiveGain(for: role), 1.0)
        }
    }

    func testMuteSilencesStem() {
        mix.setMuted(true, for: "vocals")
        XCTAssertEqual(mix.effectiveGain(for: "vocals"), 0)
        XCTAssertEqual(mix.effectiveGain(for: "drums"), 1.0)
    }

    func testSoloSilencesEverythingElse() {
        mix.setGain(0.7, for: "bass")
        mix.setSoloed(true, for: "bass")
        XCTAssertEqual(mix.effectiveGain(for: "bass"), 0.7)
        XCTAssertEqual(mix.effectiveGain(for: "drums"), 0)
        XCTAssertEqual(mix.effectiveGain(for: "vocals"), 0)
    }

    func testMuteWinsOverSolo() {
        mix.setSoloed(true, for: "drums")
        mix.setMuted(true, for: "drums")
        XCTAssertEqual(mix.effectiveGain(for: "drums"), 0)
    }

    func testMultipleSolosAllAudible() {
        mix.setSoloed(true, for: "drums")
        mix.setSoloed(true, for: "bass")
        XCTAssertEqual(mix.effectiveGain(for: "drums"), 1.0)
        XCTAssertEqual(mix.effectiveGain(for: "bass"), 1.0)
        XCTAssertEqual(mix.effectiveGain(for: "vocals"), 0)
    }

    func testGainClampsToUnitRange() {
        mix.setGain(1.8, for: "drums")
        XCTAssertEqual(mix.effectiveGain(for: "drums"), 1.0)
        mix.setGain(-0.2, for: "drums")
        XCTAssertEqual(mix.effectiveGain(for: "drums"), 0)
    }

    func testUnknownRoleIsSilent() {
        XCTAssertEqual(mix.effectiveGain(for: "theremin"), 0)
    }

    func testChangeSignalFiresOncePerRealChange() {
        var count = 0
        mix.onMixChanged = { count += 1 }
        mix.setGain(0.5, for: "drums")     // 1
        mix.setGain(0.5, for: "drums")     // no-op — same value
        mix.toggleMute(for: "bass")        // 2
        mix.setMuted(true, for: "bass")    // no-op — already muted
        mix.toggleSolo(for: "vocals")      // 3
        mix.songGain = 0.9                 // 4
        mix.songGain = 0.9                 // no-op
        mix.setGain(0.5, for: "theremin")  // no-op — unknown role
        XCTAssertEqual(count, 4)
    }
}
