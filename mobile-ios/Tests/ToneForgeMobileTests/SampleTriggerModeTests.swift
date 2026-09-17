// SampleTriggerModeTests.swift
//
// The 3-way sample trigger mode (Tap | Loop | Latch — web parity with
// kit.js's Tap|Loop|Latch segment) and the two pure pieces of logic it
// drives:
//
//   * ModeCoordinator.jamPadUpReleases — the finger-lift contract. Loop
//     is a HOLD-to-play GATE (release on lift), Latch is a TOGGLE (hold;
//     the next tap releases), Tap plays one-shots through and only
//     releases a stale ringing non-loop voice (a leftover latched voice).
//   * ModeCoordinator.overflowTypeLabel / looksLikeChordSymbol — the
//     friendly TYPE line on 64-grid overflow pads (chord stab → "Chord",
//     section loop → its Title-cased stem).
//
// Both are `nonisolated static` pure functions, so no audio graph or
// MainActor hop is needed — this is a fast, hermetic contract test.

import XCTest
@testable import ToneForgeMobile
import ToneForgeEngine

final class SampleTriggerModeTests: XCTestCase {

    // MARK: - padUp release contract (kit.js padUp parity)

    func testLoopModeAlwaysReleasesOnPadUp() {
        // HOLD-to-play gate: finger-lift stops the loop regardless of
        // ring/loop state — the whole point of the new Loop mode.
        XCTAssertTrue(ModeCoordinator.jamPadUpReleases(
            mode: .loop, isRinging: true, padLoops: true))
        XCTAssertTrue(ModeCoordinator.jamPadUpReleases(
            mode: .loop, isRinging: false, padLoops: false))
        XCTAssertTrue(ModeCoordinator.jamPadUpReleases(
            mode: .loop, isRinging: true, padLoops: false))
    }

    func testLatchModeNeverReleasesOnPadUp() {
        // Toggle: the voice holds through the lift; a second tap (padDown)
        // is what releases it. This is the behavior Loop must NOT share.
        XCTAssertFalse(ModeCoordinator.jamPadUpReleases(
            mode: .latch, isRinging: true, padLoops: true))
        XCTAssertFalse(ModeCoordinator.jamPadUpReleases(
            mode: .latch, isRinging: false, padLoops: false))
    }

    func testTapModeReleasesOnlyStaleRingingNonLoopVoice() {
        // Nothing ringing → one-shot already played through, nothing to do.
        XCTAssertFalse(ModeCoordinator.jamPadUpReleases(
            mode: .tap, isRinging: false, padLoops: false))
        // A radial Loop override outranks the mode (web parity) → survives.
        XCTAssertFalse(ModeCoordinator.jamPadUpReleases(
            mode: .tap, isRinging: true, padLoops: true))
        // A stale latched voice still ringing with no loop claim → released.
        XCTAssertTrue(ModeCoordinator.jamPadUpReleases(
            mode: .tap, isRinging: true, padLoops: false))
    }

    // MARK: - Mode semantics

    func testLoopAndLatchLoopButTapDoesNot() {
        XCTAssertFalse(SampleTriggerMode.tap.loops)
        XCTAssertTrue(SampleTriggerMode.loop.loops)
        XCTAssertTrue(SampleTriggerMode.latch.loops)
    }

    func testDisplayNamesAndCaseOrder() {
        XCTAssertEqual(SampleTriggerMode.allCases, [.tap, .loop, .latch])
        XCTAssertEqual(SampleTriggerMode.tap.displayName, "Tap")
        XCTAssertEqual(SampleTriggerMode.loop.displayName, "Loop")
        XCTAssertEqual(SampleTriggerMode.latch.displayName, "Latch")
    }

    // MARK: - Overflow pad TYPE label (FIX 2)

    func testChordSymbolNamesLabelAsChord() {
        // A chord-symbol name reads "Chord" whatever the stem.
        for name in ["D", "G", "Em7", "F#m7b5", "Cmaj7", "A/E", "Bb"] {
            XCTAssertTrue(
                ModeCoordinator.looksLikeChordSymbol(name),
                "\(name) should read as a chord symbol")
        }
    }

    func testSectionNamesDoNotLabelAsChord() {
        for name in ["Intro", "Verse", "Chorus", "Bridge", "Outro",
                     "Breakdown", "Drop", "Hook", "Fill"] {
            XCTAssertFalse(
                ModeCoordinator.looksLikeChordSymbol(name),
                "\(name) is a section, not a chord symbol")
        }
    }

    func testOverflowTypeLabelChordVsStem() {
        // Vocals-stem section loop → the Title-cased stem.
        XCTAssertEqual(
            ModeCoordinator.overflowTypeLabel(
                stem: "vocals", family: .vocals, name: "Intro"),
            "Vocals")
        // Chord stab (name looks like a chord) → "Chord" even on a raw stem.
        XCTAssertEqual(
            ModeCoordinator.overflowTypeLabel(
                stem: "other", family: .mixed, name: "Em7"),
            "Chord")
        // Pads-family stab → "Chord" via the family signal alone.
        XCTAssertEqual(
            ModeCoordinator.overflowTypeLabel(
                stem: "other", family: .pads, name: "Stab 3"),
            "Chord")
    }

    func testTitleCasedStem() {
        XCTAssertEqual(ModeCoordinator.titleCasedStem("vocals"), "Vocals")
        XCTAssertEqual(ModeCoordinator.titleCasedStem("OTHER"), "Other")
        XCTAssertEqual(ModeCoordinator.titleCasedStem(""), "Loop")
    }
}
