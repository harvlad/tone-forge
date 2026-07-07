// VocoderCoordinatorTests.swift
//
// Coordinator-side vocoder program building on a headless AppState
// (no boot, no song, no pack): every mode must degrade to an audible
// deterministic carrier — the "always sounds" guarantee — and the
// chord-symbol → pitch-class expansion is checked directly. The save
// flow itself (RecordingProcessor → classify → PadSampleStore) is the
// same path PadSampleStoreTests + the device checklist cover.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class VocoderCoordinatorTests: XCTestCase {

    private var expectedFrames: Int {
        Int(VocoderCaptureSession.maxDurationSec
            * AudioEngine.canonicalSampleRate)
    }

    private func rms(_ x: [Float]) -> Float {
        guard !x.isEmpty else { return 0 }
        return (x.reduce(Float(0)) { $0 + $1 * $1 }
            / Float(x.count)).squareRoot()
    }

    func testClassicProgramDronesWithoutChord() async {
        let app = AppState()
        let program = await app.modeCoordinator.vocoderProgram(for: .classic)
        XCTAssertEqual(program.mode, .classic)
        XCTAssertEqual(program.carrier.count, expectedFrames,
                       "carrier must cover the full 8 s cap")
        XCTAssertGreaterThan(rms(program.carrier), 0.05,
                             "no chord → drone, never silence")
    }

    func testSongAndStemProgramsDegradeToAudibleCarrier() async {
        // Headless: no bundle, no stems on disk. Both song-derived
        // modes must fall through to the drone chord grid.
        let app = AppState()
        for mode in [VocoderMode.song, .stem] {
            let program = await app.modeCoordinator.vocoderProgram(for: mode)
            XCTAssertEqual(program.mode, mode)
            XCTAssertEqual(program.carrier.count, expectedFrames)
            XCTAssertGreaterThan(rms(program.carrier), 0.05,
                                 "\(mode) without a song must drone")
        }
    }

    func testTextureProgramDronesWithoutPackAudio() async {
        let app = AppState()  // never booted → no pack buffers resident
        let program = await app.modeCoordinator.vocoderProgram(for: .texture)
        XCTAssertEqual(program.carrier.count, expectedFrames)
        XCTAssertGreaterThan(rms(program.carrier), 0.05)
    }

    func testHarmonyProgramHasNoSpectralCarrier() async {
        let app = AppState()
        let program = await app.modeCoordinator.vocoderProgram(for: .harmony)
        XCTAssertEqual(program.mode, .harmony)
        XCTAssertTrue(program.carrier.isEmpty,
                      "harmony is PSOLA-only — no spectral carrier")
        XCTAssertTrue(program.chordSpans.isEmpty,
                      "no song + no sounding chord → nearest-tone fallback")
    }

    func testPitchClassExpansionMatchesTriads() {
        XCTAssertEqual(ModeCoordinator.pitchClasses(for: "C"), [0, 4, 7])
        XCTAssertEqual(ModeCoordinator.pitchClasses(for: "Am"), [9, 0, 4])
        XCTAssertEqual(ModeCoordinator.pitchClasses(for: "G7"), [7, 11, 2, 5])
        XCTAssertEqual(ModeCoordinator.pitchClasses(for: ""), [])
    }
}
