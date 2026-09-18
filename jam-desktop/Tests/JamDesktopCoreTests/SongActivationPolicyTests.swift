// SongActivationPolicyTests.swift
//
// Pins the per-song reset of the note-synth layer (D-034, web
// 5d1e3bd0 parity): the melody guide never survives a song switch.
// This is the desktop twin of the web launchpad-driver bug where the
// toolbar Melody tool's 'instrument-melody' mode outlived the song it
// was armed for and fired stale synth notes on every pad press.

import XCTest
@testable import JamDesktopCore

final class SongActivationPolicyTests: XCTestCase {

    func testMelodyGuideNeverSurvivesASongSwitch() {
        XCTAssertFalse(
            SongActivationPolicy.melodyGuideEnabledAfterSongLoad(wasEnabled: true))
        XCTAssertFalse(
            SongActivationPolicy.melodyGuideEnabledAfterSongLoad(wasEnabled: false))
    }
}
