// SampleTriggerModeTests.swift
//
// The 3-way sample trigger mode (Tap | Loop | Latch — web parity with
// kit.js's Tap|Loop|Latch segment) and the two pure pieces of logic it
// drives:
//
//   * ModeCoordinator.jamPadUpAction — the finger-lift contract. Loop is
//     a HOLD-to-play GATE (release IMMEDIATELY on lift), Latch is a TOGGLE
//     (hold; the next tap releases), Tap is a zero-latency one-shot that
//     loops while held → a ringing looping voice releases AT the end of
//     its current loop pass (quick tap = one clean pass, hold = sustain);
//     a non-loop tap plays through (no-op).
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

    // MARK: - padUp release contract

    func testGateModesReleaseImmediatelyOnPadUp() {
        // One-Shot and Follow are momentary gates: a ringing voice stops NOW
        // on finger-lift regardless of loop state. They differ only in start
        // phase, never on release.
        for mode in [SampleTriggerMode.oneShot, .follow] {
            for loops in [true, false] {
                XCTAssertEqual(
                    ModeCoordinator.jamPadUpAction(
                        mode: mode, isRinging: true, padLoops: loops),
                    .immediate,
                    "\(mode) releases immediately when ringing (loops=\(loops))")
            }
        }
    }

    func testLatchModeNeverReleasesOnPadUp() {
        // Toggle: the voice holds through the lift; a second tap (padDown)
        // is what releases it.
        XCTAssertEqual(ModeCoordinator.jamPadUpAction(
            mode: .latch, isRinging: true, padLoops: true), .none)
        XCTAssertEqual(ModeCoordinator.jamPadUpAction(
            mode: .latch, isRinging: false, padLoops: false), .none)
    }

    func testGateModeSilentPadIsNoOp() {
        // Nothing ringing on this pad → padUp does nothing.
        for mode in [SampleTriggerMode.oneShot, .follow] {
            XCTAssertEqual(
                ModeCoordinator.jamPadUpAction(
                    mode: mode, isRinging: false, padLoops: false),
                .none)
        }
    }

    // MARK: - Mode semantics

    func testOnlyLatchRollsTheClock() {
        // loops == rollsClock: only Latch drives the shared clock. One-Shot
        // and Follow are momentary gates.
        XCTAssertFalse(SampleTriggerMode.oneShot.loops)
        XCTAssertFalse(SampleTriggerMode.follow.loops)
        XCTAssertTrue(SampleTriggerMode.latch.loops)
    }

    func testOnlyOneShotStartsFromZero() {
        // One-Shot retriggers from the sample top; Follow/Latch join the
        // shared clock phase.
        XCTAssertTrue(SampleTriggerMode.oneShot.startsFromZero)
        XCTAssertFalse(SampleTriggerMode.follow.startsFromZero)
        XCTAssertFalse(SampleTriggerMode.latch.startsFromZero)
    }

    func testDisplayNamesAndCaseOrder() {
        XCTAssertEqual(SampleTriggerMode.allCases, [.oneShot, .follow, .latch])
        XCTAssertEqual(SampleTriggerMode.oneShot.displayName, "One-Shot")
        XCTAssertEqual(SampleTriggerMode.follow.displayName, "Follow")
        XCTAssertEqual(SampleTriggerMode.latch.displayName, "Latch")
    }

    func testLegacyModeMigration() {
        // Retired tap/loop fold onto Follow; latch stays latch.
        XCTAssertEqual(SampleTriggerMode.migratedFromLegacy("tap"), .follow)
        XCTAssertEqual(SampleTriggerMode.migratedFromLegacy("loop"), .follow)
        XCTAssertEqual(SampleTriggerMode.migratedFromLegacy("latch"), .latch)
        XCTAssertEqual(SampleTriggerMode.migratedFromLegacy("oneShot"), .oneShot)
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
