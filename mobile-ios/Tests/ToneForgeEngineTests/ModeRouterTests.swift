// ModeRouterTests.swift
//
// Pins the ModeRouter routing table: sample mode (all pads samples),
// hybrid mode (rows 1–4 notes / 5–8 samples), midiNote passthrough,
// gap markers, unimplemented modes, and invalid coordinates.

import XCTest
@testable import ToneForgeEngine

final class ModeRouterTests: XCTestCase {

    private func event(
        _ kind: ContributionEvent.Kind,
        velocity: Double = 1.0
    ) -> ContributionEvent {
        ContributionEvent(
            source: .touch,
            kind: kind,
            timestamp: 0,
            hostTime: 0,
            velocity: velocity
        )
    }

    private let emptySampleLayout = SampleModeLayout(content: [:])

    private func hybridLayout(chordPCs: Set<Int> = []) -> HybridModeLayout {
        HybridModeLayout(
            keyLabel: "C major",
            chordPitchClasses: chordPCs,
            sampleContent: [:]
        )
    }

    // MARK: - Sample mode

    func testSamplePadDownTriggersRawValue() {
        let action = ModeRouter.resolve(
            event(.padDown(row: 3, col: 7)),
            mode: .sample,
            layout: emptySampleLayout
        )
        XCTAssertEqual(action, .triggerSample(padIdx: 37))
    }

    func testSamplePadUpReleases() {
        let action = ModeRouter.resolve(
            event(.padUp(row: 8, col: 8)),
            mode: .sample,
            layout: emptySampleLayout
        )
        XCTAssertEqual(action, .releaseSample(padIdx: 88))
    }

    func testSampleCorners() {
        // Bottom-left = 11, top-right = 88 (PadIndex convention).
        XCTAssertEqual(
            ModeRouter.resolve(event(.padDown(row: 1, col: 1)), mode: .sample, layout: emptySampleLayout),
            .triggerSample(padIdx: 11)
        )
        XCTAssertEqual(
            ModeRouter.resolve(event(.padDown(row: 8, col: 8)), mode: .sample, layout: emptySampleLayout),
            .triggerSample(padIdx: 88)
        )
    }

    func testInvalidCoordinatesResolveNone() {
        for kind: ContributionEvent.Kind in [
            .padDown(row: 0, col: 1), .padDown(row: 9, col: 1),
            .padDown(row: 1, col: 0), .padDown(row: 1, col: 9),
            .padUp(row: 0, col: 0), .padUp(row: 12, col: 3),
        ] {
            XCTAssertEqual(
                ModeRouter.resolve(event(kind), mode: .sample, layout: emptySampleLayout),
                .none
            )
        }
    }

    // MARK: - Hybrid mode

    func testHybridSampleRowsTrigger() {
        for row in 5...8 {
            let action = ModeRouter.resolve(
                event(.padDown(row: row, col: 2)),
                mode: .hybrid,
                layout: hybridLayout()
            )
            XCTAssertEqual(action, .triggerSample(padIdx: row * 10 + 2))
        }
    }

    func testHybridNoteRowsPlaySynth() {
        // Pad 11 in OpenJamGrid = E2 = MIDI 40.
        let action = ModeRouter.resolve(
            event(.padDown(row: 1, col: 1), velocity: 0.8),
            mode: .hybrid,
            layout: hybridLayout()
        )
        XCTAssertEqual(action, .synthNoteOn(midi: 40, velocity: 0.8, isChordTone: false))
    }

    func testHybridNoteRowMidiMapping() {
        // +1 semitone per column, +5 per row from E2 at (1,1).
        let action = ModeRouter.resolve(
            event(.padDown(row: 4, col: 3)),
            mode: .hybrid,
            layout: hybridLayout()
        )
        XCTAssertEqual(action, .synthNoteOn(midi: 40 + 3 * 5 + 2, velocity: 1.0, isChordTone: false))
    }

    func testHybridChordToneFlag() {
        // E2 (pad 11) has pitch class 4. With E in the current chord
        // the pad renders bright and the action carries the flag.
        let action = ModeRouter.resolve(
            event(.padDown(row: 1, col: 1)),
            mode: .hybrid,
            layout: hybridLayout(chordPCs: [4])
        )
        XCTAssertEqual(action, .synthNoteOn(midi: 40, velocity: 1.0, isChordTone: true))
    }

    func testHybridNoteRowPadUpIsNoteOff() {
        let action = ModeRouter.resolve(
            event(.padUp(row: 2, col: 1)),
            mode: .hybrid,
            layout: hybridLayout()
        )
        XCTAssertEqual(action, .synthNoteOff(midi: 45))
    }

    func testHybridSampleRowPadUpReleases() {
        let action = ModeRouter.resolve(
            event(.padUp(row: 6, col: 4)),
            mode: .hybrid,
            layout: hybridLayout()
        )
        XCTAssertEqual(action, .releaseSample(padIdx: 64))
    }

    // MARK: - midiNote passthrough

    func testMidiNoteOnMapsToSynth() {
        let action = ModeRouter.resolve(
            event(.midiNote(note: 60, velocity: 127, on: true)),
            mode: .sample,
            layout: emptySampleLayout
        )
        XCTAssertEqual(action, .synthNoteOn(midi: 60, velocity: 1.0, isChordTone: false))
    }

    func testMidiNoteOffAndVelocityZeroMapToNoteOff() {
        XCTAssertEqual(
            ModeRouter.resolve(
                event(.midiNote(note: 60, velocity: 0, on: true)),
                mode: .hybrid, layout: hybridLayout()
            ),
            .synthNoteOff(midi: 60)
        )
        XCTAssertEqual(
            ModeRouter.resolve(
                event(.midiNote(note: 60, velocity: 64, on: false)),
                mode: .hybrid, layout: hybridLayout()
            ),
            .synthNoteOff(midi: 60)
        )
    }

    func testMidiNoteOutOfRangeResolvesNone() {
        XCTAssertEqual(
            ModeRouter.resolve(
                event(.midiNote(note: 128, velocity: 100, on: true)),
                mode: .sample, layout: emptySampleLayout
            ),
            .none
        )
    }

    // MARK: - Gaps + unimplemented modes

    func testGapResolvesNone() {
        XCTAssertEqual(
            ModeRouter.resolve(event(.gap(seconds: 1.0)), mode: .sample, layout: emptySampleLayout),
            .none
        )
    }

    func testUnimplementedModesResolveNone() {
        for mode in AppMode.allCases where !mode.isImplemented {
            XCTAssertEqual(
                ModeRouter.resolve(
                    event(.padDown(row: 1, col: 1)),
                    mode: mode,
                    layout: EmptyLayout()
                ),
                .none,
                "mode \(mode) should be inert"
            )
        }
    }

    func testImplementedModeSet() {
        XCTAssertEqual(
            AppMode.allCases.filter(\.isImplemented),
            [.sample, .hybrid, .jamInKey]
        )
    }

    // MARK: - Jam in Key mode

    private func jamLayout(octaveShift: Int = 0) -> JamInKeyLayout {
        JamInKeyLayout(
            key: MusicalKey.parse("D minor"),
            octaveShift: octaveShift
        )
    }

    func testJamPadDownPlaysPadSynth() {
        // Pad 11 in OpenJamGrid = E2 = MIDI 40.
        let action = ModeRouter.resolve(
            event(.padDown(row: 1, col: 1), velocity: 0.8),
            mode: .jamInKey,
            layout: jamLayout()
        )
        XCTAssertEqual(action, .padSynthNote(midi: 40, velocity: 0.8))
    }

    func testJamOctaveShiftMovesMidi() {
        let action = ModeRouter.resolve(
            event(.padDown(row: 1, col: 1)),
            mode: .jamInKey,
            layout: jamLayout(octaveShift: 1)
        )
        XCTAssertEqual(action, .padSynthNote(midi: 52, velocity: 1.0))
    }

    func testJamPadUpResolvesNone() {
        // PadSynth voices auto-release; there is nothing to stop.
        let action = ModeRouter.resolve(
            event(.padUp(row: 1, col: 1)),
            mode: .jamInKey,
            layout: jamLayout()
        )
        XCTAssertEqual(action, .none)
    }
}
