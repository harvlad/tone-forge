// PadColorResolutionTests.swift
//
// The ONE pad-tile color source (ModeCoordinator.padColorHint) that the
// Launchpad tiles (SamplePadGrid4x4 via the layout) and the Sequence
// Builder both key off, so a pad and its sequence-builder twin can't
// drift (D-039). Pins the priority hex → category → family, and that a
// pad carrying a musical category resolves to the launchpad CATEGORY
// color, never the coarse 8-bucket family color that collapsed every
// melodic stem into one pink `.stabs` block.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

final class PadColorResolutionTests: XCTestCase {

    private func pad(
        family: SampleFamily,
        colorHint: String? = nil,
        category: String? = nil
    ) -> SamplePad {
        SamplePad(
            padIdx: 0, name: "Pad", family: family,
            colorHint: colorHint, category: category)
    }

    /// An auto-kit LEAD pad carries a per-category hex from the backend
    /// (`kit_builder._CATEGORY_HEX`). It must resolve to that category
    /// color — NOT the coarse `.stabs` family pink the old Sequence
    /// Builder used.
    func testExplicitCategoryHexWinsOverFamily() {
        let lead = pad(family: .stabs, colorHint: "#F97316", category: "LEAD")
        XCTAssertEqual(ModeCoordinator.padColorHint(for: lead), 0xF97316)
        XCTAssertNotEqual(
            ModeCoordinator.padColorHint(for: lead),
            ModeCoordinator.familyColor(.stabs))
    }

    /// A pad tagged with a category but NO explicit hex still colors by
    /// category (drums red, bass green, …), not by family.
    func testCategoryFallbackBeatsFamily() {
        let bass = pad(family: .stabs, category: "BASS")
        XCTAssertEqual(ModeCoordinator.padColorHint(for: bass), 0x22C55E)
        XCTAssertNotEqual(
            ModeCoordinator.padColorHint(for: bass),
            ModeCoordinator.familyColor(.stabs))

        let drums = pad(family: .mixed, category: "DRUMS")
        XCTAssertEqual(ModeCoordinator.padColorHint(for: drums), 0xEF4444)
    }

    /// No hint, no category → the coarse sound-family palette (raw
    /// song-DNA pads keep their family tint).
    func testFamilyFallbackWhenNoHintOrCategory() {
        let raw = pad(family: .bass)
        XCTAssertEqual(
            ModeCoordinator.padColorHint(for: raw),
            ModeCoordinator.familyColor(.bass))
    }

    /// The launchpad-layout tile and the Sequence Builder tile go
    /// through the SAME resolver, so their colors are bit-identical for
    /// the same pad — the whole point of the shared source.
    func testTwinPadsShareOneColor() {
        for cat in ["DRUMS", "BASS", "CHORDS", "LEAD", "VOCAL", "SYNTH"] {
            let p = pad(family: .mixed, colorHint: nil, category: cat)
            // Same call the ContributeSurface grid tiles and the
            // SequenceBuilderSheet `.pads` case both make.
            let launchpad = ModeCoordinator.padColorHint(for: p)
            let builder = ModeCoordinator.padColorHint(for: p)
            XCTAssertEqual(launchpad, builder)
            XCTAssertNotNil(ModeCoordinator.categoryColor(cat))
        }
    }
}
