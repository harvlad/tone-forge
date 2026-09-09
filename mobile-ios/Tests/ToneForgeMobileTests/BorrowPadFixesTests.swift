// BorrowPadFixesTests.swift
//
// Regression guards for two Remix/Borrow pad fixes:
//   1. The shared contribution reverb defaults DRY. It used to default to
//      wetGain 0.3 / 2 s, which washed every sample pad (kit/Re-Drum/Borrow)
//      and made clean stems sound "underwater". Reverb is opt-in from Settings.
//   2. ModeCoordinator.hexColorHint parses an explicit "#RRGGBB" so Borrow's
//      per-source colours (blue this song / amber donor) win over the family
//      palette; named/garbage colours return nil so normal packs keep family.

import XCTest
@testable import ToneForgeMobile

final class BorrowPadFixesTests: XCTestCase {

    func testPadReverbDefaultsDry() {
        let p = AudioEngine.ReverbParams()
        XCTAssertEqual(p.wetGain, 0.0, "pad reverb must default dry — a wet "
            + "default washes every Remix pad (the underwater bug)")
        XCTAssertEqual(p.dryGain, 1.0, "dry signal unattenuated by default")
    }

    func testHexColorHintParsesExplicitHex() {
        XCTAssertEqual(ModeCoordinator.hexColorHint("#3B82F6"), 0x3B82F6)  // this song
        XCTAssertEqual(ModeCoordinator.hexColorHint("#F59E0B"), 0xF59E0B)  // donor
        XCTAssertEqual(ModeCoordinator.hexColorHint("3b82f6"), 0x3B82F6)   // no '#', lower
        XCTAssertEqual(ModeCoordinator.hexColorHint("  #F59E0B  "), 0xF59E0B) // trimmed
    }

    func testHexColorHintNilForNamedOrGarbage() {
        // Named colours / junk fall through to the family palette, so normal
        // packs are unaffected — only borrow's explicit hex overrides.
        XCTAssertNil(ModeCoordinator.hexColorHint("purple"))
        XCTAssertNil(ModeCoordinator.hexColorHint("#12"))
        XCTAssertNil(ModeCoordinator.hexColorHint("#GGGGGG"))
        XCTAssertNil(ModeCoordinator.hexColorHint(""))
        XCTAssertNil(ModeCoordinator.hexColorHint(nil))
    }
}
