// GridLayoutTests.swift
//
// Pins SampleModeLayout / HybridModeLayout / EmptyLayout behavior:
// content → visual mapping, provenance colors + badges, hybrid
// note-row coloring/labels, and the note/sample row split.

import XCTest
@testable import ToneForgeEngine

final class GridLayoutTests: XCTestCase {

    // MARK: - SampleModeLayout

    func testSampleLayoutEmptyPadIsOff() {
        let layout = SampleModeLayout(content: [:])
        XCTAssertEqual(layout.visual(at: PadIndex(11)), .off)
        XCTAssertEqual(layout.meaning(at: PadIndex(11)), PadMeaning.none)
    }

    func testSampleLayoutContentMapsToVisual() {
        let layout = SampleModeLayout(content: [
            37: PadContent(label: "Kick", colorHint: 0x2266FF),
        ])
        let v = layout.visual(at: PadIndex(37))
        XCTAssertEqual(v.colorHint, 0x2266FF)
        XCTAssertEqual(v.label, "Kick")
        XCTAssertTrue(v.isBright)
        XCTAssertNil(v.badge)
    }

    func testSampleLayoutProvenanceBadgesAndColors() {
        // Mic = warm orange, vocoded = purple (plan-fixed hints).
        let layout = SampleModeLayout(content: [
            11: PadContent(label: "hey", colorHint: 0xFF8C3A, badge: .mic),
            12: PadContent(label: "voc", colorHint: 0x9B4DFF, badge: .vocoded),
        ])
        XCTAssertEqual(layout.visual(at: PadIndex(11)).badge, .mic)
        XCTAssertEqual(layout.visual(at: PadIndex(11)).colorHint, 0xFF8C3A)
        XCTAssertEqual(layout.visual(at: PadIndex(12)).badge, .vocoded)
        XCTAssertEqual(layout.visual(at: PadIndex(12)).colorHint, 0x9B4DFF)
    }

    func testSampleLayoutLoopFallbackBadge() {
        // Explicit badge wins; loops only badges when nothing else set.
        let layout = SampleModeLayout(content: [
            21: PadContent(colorHint: 0x123456, loops: true),
            22: PadContent(colorHint: 0x123456, badge: .mic, loops: true),
        ])
        XCTAssertEqual(layout.visual(at: PadIndex(21)).badge, .loop)
        XCTAssertEqual(layout.visual(at: PadIndex(22)).badge, .mic)
    }

    func testSampleLayoutInvalidPadIsOff() {
        let layout = SampleModeLayout(content: [90: PadContent(colorHint: 0xFFFFFF)])
        XCTAssertEqual(layout.visual(at: PadIndex(90)), .off)
        XCTAssertEqual(layout.visual(at: PadIndex(0)), .off)
    }

    // MARK: - HybridModeLayout

    private func hybrid(
        chordPCs: Set<Int> = [],
        sampleContent: [Int: PadContent] = [:]
    ) -> HybridModeLayout {
        HybridModeLayout(
            keyLabel: "C major",
            chordPitchClasses: chordPCs,
            sampleContent: sampleContent
        )
    }

    func testHybridRowSplit() {
        for row in 1...4 { XCTAssertTrue(HybridModeLayout.isNoteRow(row)) }
        for row in 5...8 { XCTAssertFalse(HybridModeLayout.isNoteRow(row)) }
    }

    func testHybridNoteRowMeaning() {
        let layout = hybrid()
        // Pad 11 = E2 = MIDI 40, pitch class 4 = degree "iii" in C major.
        guard case .note(let midi, let pc, let label) = layout.meaning(at: PadIndex(11)) else {
            return XCTFail("expected .note meaning")
        }
        XCTAssertEqual(midi, 40)
        XCTAssertEqual(pc, 4)
        XCTAssertEqual(label, "iii")
    }

    func testHybridSampleRowMeaningIsNone() {
        let layout = hybrid(sampleContent: [55: PadContent(colorHint: 0xFF0000)])
        XCTAssertEqual(layout.meaning(at: PadIndex(55)), PadMeaning.none)
    }

    func testHybridSampleRowVisualFromContent() {
        let layout = hybrid(sampleContent: [66: PadContent(label: "Snare", colorHint: 0x00FF00)])
        let v = layout.visual(at: PadIndex(66))
        XCTAssertEqual(v.colorHint, 0x00FF00)
        XCTAssertEqual(v.label, "Snare")
        XCTAssertEqual(layout.visual(at: PadIndex(65)), .off)
    }

    func testHybridChordToneIsBright() {
        // E (pc 4) in chord → pad 11 bright; without chord → dim.
        XCTAssertTrue(hybrid(chordPCs: [4]).visual(at: PadIndex(11)).isBright)
        XCTAssertFalse(hybrid().visual(at: PadIndex(11)).isBright)
    }

    func testHybridRootPadColored() {
        // Pad 21 = MIDI 45 = A... find a C pad: MIDI 48 = C3 at row 2
        // col 4 (40 + 5 + 3 = 48, pc 0 = key root of C major).
        let layout = hybrid()
        let v = layout.visual(at: PadIndex(24))
        XCTAssertEqual(
            v.colorHint,
            HybridModeLayout.hint(Palette.openJamRoot)
        )
    }

    func testHybridDegreeLabelOnVisual() {
        let layout = hybrid()
        // Root pad (C) labels with its scale degree "I".
        XCTAssertEqual(layout.visual(at: PadIndex(24)).label, "I")
    }

    func testHybridNilKeyStillPlayable() {
        // Song-less hybrid (synthetic grid): notes still map, colors
        // degrade to the chromatic dim.
        let layout = HybridModeLayout(keyLabel: nil, chordPitchClasses: [], sampleContent: [:])
        guard case .note(let midi, _, let label) = layout.meaning(at: PadIndex(11)) else {
            return XCTFail("expected .note meaning")
        }
        XCTAssertEqual(midi, 40)
        XCTAssertNil(label)
    }

    // MARK: - EmptyLayout

    func testEmptyLayoutIsInert() {
        let layout = EmptyLayout()
        XCTAssertEqual(layout.meaning(at: PadIndex(44)), PadMeaning.none)
        XCTAssertEqual(layout.visual(at: PadIndex(44)), .off)
    }

    // MARK: - hint conversion

    func testPadColorToHint() {
        XCTAssertEqual(HybridModeLayout.hint(PadColor(0xFF, 0x8C, 0x3A)), 0xFF8C3A)
        XCTAssertEqual(HybridModeLayout.hint(.off), 0x000000)
    }
}
