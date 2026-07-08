// JamInKeyControllerTests.swift
//
// Pins the pure pieces of the Jam in Key controller (redesign
// Phase 7): the degree-pad row derivation (exact mockup labels for
// D minor) and the JamTriggerPlan timing math (strum stagger,
// quantize base offset, velocity/pan spread mirroring
// PadSynth.triggerChord).

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class JamInKeyControllerTests: XCTestCase {

    // MARK: - Degree pads

    func testDegreePadsMatchDMinorMockup() {
        guard let key = MusicalKey.parse("D minor") else {
            return XCTFail("key should parse")
        }
        let pads = JamInKeyController.degreePads(for: key)
        XCTAssertEqual(pads.map(\.noteName),
                       ["D", "E", "F", "G", "A", "Bb", "C"])
        XCTAssertEqual(pads.map(\.romanNumeral),
                       ["i", "ii°", "III", "iv", "v", "VI", "VII"])
        XCTAssertEqual(pads.map(\.symbol),
                       ["Dm", "Edim", "F", "Gm", "Am", "Bb", "C"])
        XCTAssertEqual(pads.map(\.degree), Array(1...7))
    }

    func testDegreePadsHarmonicMinorMajorV() {
        guard let key = MusicalKey.parse("D harmonic minor") else {
            return XCTFail("key should parse")
        }
        let pads = JamInKeyController.degreePads(for: key)
        XCTAssertEqual(pads[4].symbol, "A", "harmonic minor V is major")
        XCTAssertEqual(pads[4].romanNumeral, "V")
    }

    // MARK: - Trigger plan

    func testBlockChordHasEqualOffsets() {
        let plan = JamTriggerPlan.plan(
            midis: [50, 53, 57],
            strumMs: 0,
            quantizeDelaySec: 0,
            sampleRate: 48_000
        )
        XCTAssertEqual(plan.voices.map(\.offsetSamples), [0, 0, 0])
    }

    func testStrumStaggersVoices() {
        let plan = JamTriggerPlan.plan(
            midis: [50, 53, 57],
            strumMs: 15,
            quantizeDelaySec: 0,
            sampleRate: 48_000
        )
        XCTAssertEqual(plan.voices.map(\.offsetSamples), [0, 720, 1440])
    }

    func testQuantizeDelayIsBaseOffset() {
        let plan = JamTriggerPlan.plan(
            midis: [50, 53],
            strumMs: 10,
            quantizeDelaySec: 0.5,
            sampleRate: 48_000
        )
        XCTAssertEqual(plan.voices.map(\.offsetSamples), [24_000, 24_480])
    }

    func testVelocityAndPanSpreadMirrorPadSynth() {
        let plan = JamTriggerPlan.plan(
            midis: [50, 53, 57],
            strumMs: 0,
            quantizeDelaySec: 0,
            sampleRate: 48_000,
            velocity: 100
        )
        // PadSynth.triggerChord: velocity * 0.6 (floored at 20), pan
        // spread ±0.3 across the chord.
        for voice in plan.voices {
            XCTAssertEqual(voice.velocity, 60, accuracy: 0.001)
        }
        XCTAssertEqual(plan.voices.first?.pan ?? 99, -0.3, accuracy: 0.001)
        XCTAssertEqual(plan.voices[1].pan, 0, accuracy: 0.001)
        XCTAssertEqual(plan.voices.last?.pan ?? 99, 0.3, accuracy: 0.001)
    }

    func testSingleNoteCentered() {
        let plan = JamTriggerPlan.plan(
            midis: [50],
            strumMs: 20,
            quantizeDelaySec: 0,
            sampleRate: 48_000
        )
        XCTAssertEqual(plan.voices.count, 1)
        XCTAssertEqual(plan.voices[0].pan, 0)
        XCTAssertEqual(plan.voices[0].offsetSamples, 0)
    }

    func testNegativeInputsClampToZero() {
        let plan = JamTriggerPlan.plan(
            midis: [50, 53],
            strumMs: -5,
            quantizeDelaySec: -1,
            sampleRate: 48_000
        )
        XCTAssertEqual(plan.voices.map(\.offsetSamples), [0, 0])
    }
}
