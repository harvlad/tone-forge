// VocoderCarriersTests.swift
//
// Carrier builders are pure and deterministic, so every test is a
// straight input → property check: exact output length, bit-identical
// re-render, correct pitch content (verified with the same
// PSOLAHarmonizer.trackF0 the stem carrier uses internally), smooth
// chord-boundary crossfades, and loop coverage with no silent tiles.

import XCTest
@testable import ToneForgeEngine

final class VocoderCarriersTests: XCTestCase {

    private let sampleRate = 48_000.0

    // MARK: - Helpers

    private func rms(_ x: ArraySlice<Float>) -> Float {
        guard !x.isEmpty else { return 0 }
        let sum = x.reduce(Float(0)) { $0 + $1 * $1 }
        return (sum / Float(x.count)).squareRoot()
    }

    /// Median f0 of confidently voiced frames in `x`, or nil if fewer
    /// than `minVoiced` frames are voiced.
    private func medianVoicedF0(
        _ x: [Float], minVoiced: Int = 5
    ) -> Double? {
        let frames = PSOLAHarmonizer.trackF0(x, sampleRate: sampleRate)
        let voiced = frames
            .filter { $0.f0Hz > 0 && $0.confidence >= 0.6 }
            .map { $0.f0Hz }
            .sorted()
        guard voiced.count >= minVoiced else { return nil }
        return voiced[voiced.count / 2]
    }

    private func midiHz(_ note: Int) -> Double {
        440.0 * pow(2.0, Double(note - 69) / 12.0)
    }

    // MARK: - M1 saw stack

    func testSawStackLengthAndDeterminism() {
        let a = VocoderCarriers.sawStack(
            notes: [48, 55, 60], durationSec: 0.7375, sampleRate: sampleRate
        )
        let b = VocoderCarriers.sawStack(
            notes: [48, 55, 60], durationSec: 0.7375, sampleRate: sampleRate
        )
        XCTAssertEqual(a.count, Int((0.7375 * sampleRate).rounded()))
        XCTAssertEqual(a, b, "same inputs must render bit-identically")
    }

    func testSawStackPitchMatchesNote() throws {
        // Single saw at A3 — autocorrelation should lock hard.
        let note = 57
        let x = VocoderCarriers.sawStack(
            notes: [note], durationSec: 1.0, sampleRate: sampleRate
        )
        let f0 = try XCTUnwrap(medianVoicedF0(x), "saw should be voiced")
        XCTAssertEqual(f0, midiHz(note), accuracy: midiHz(note) * 0.03)
    }

    func testSawStackEmptyNotesFallsBackToDrone() {
        let x = VocoderCarriers.sawStack(
            notes: [], durationSec: 0.5, sampleRate: sampleRate
        )
        XCTAssertGreaterThan(rms(x[...]), 0.05, "drone fallback must sound")
        XCTAssertLessThanOrEqual(x.map { abs($0) }.max() ?? 0, 0.9001)
    }

    func testSawStackSkipsNotesAboveNyquist() {
        // 150 ≈ 27 kHz — above Nyquist at 48 k; only the low note
        // should sound (and it should, alone, still normalize).
        let x = VocoderCarriers.sawStack(
            notes: [48, 150], durationSec: 0.5, sampleRate: sampleRate
        )
        XCTAssertGreaterThan(rms(x[...]), 0.05)
        // All-unplayable → silence, not a crash.
        let silent = VocoderCarriers.sawStack(
            notes: [150], durationSec: 0.5, sampleRate: sampleRate
        )
        XCTAssertEqual(silent.map { abs($0) }.max() ?? 0, 0)
    }

    // MARK: - M2 chord grid

    func testChordGridSegmentsFollowChords() throws {
        // C3 for the first second, G3 for the second.
        let spans = [
            VocoderCarriers.ChordSpan(startSec: 0, midiNotes: [48]),
            VocoderCarriers.ChordSpan(startSec: 1.0, midiNotes: [55]),
        ]
        let x = VocoderCarriers.chordGrid(
            spans: spans, durationSec: 2.0, sampleRate: sampleRate
        )
        XCTAssertEqual(x.count, Int(2.0 * sampleRate))

        let firstHalf = Array(x[Int(0.1 * sampleRate)..<Int(0.8 * sampleRate)])
        let secondHalf = Array(x[Int(1.2 * sampleRate)..<Int(1.9 * sampleRate)])
        let f0First = try XCTUnwrap(medianVoicedF0(firstHalf))
        let f0Second = try XCTUnwrap(medianVoicedF0(secondHalf))
        XCTAssertEqual(f0First, midiHz(48), accuracy: midiHz(48) * 0.03)
        XCTAssertEqual(f0Second, midiHz(55), accuracy: midiHz(55) * 0.03)
    }

    func testChordGridCrossfadeKeepsLevelContinuous() {
        let spans = [
            VocoderCarriers.ChordSpan(startSec: 0, midiNotes: [48, 55]),
            VocoderCarriers.ChordSpan(startSec: 1.0, midiNotes: [50, 57]),
        ]
        let x = VocoderCarriers.chordGrid(
            spans: spans, durationSec: 2.0, sampleRate: sampleRate
        )
        // RMS across the 40 ms boundary window vs a steady-state
        // reference region: no dropout, no doubling.
        let boundary = rms(x[Int(0.98 * sampleRate)..<Int(1.02 * sampleRate)])
        let reference = rms(x[Int(0.5 * sampleRate)..<Int(0.7 * sampleRate)])
        XCTAssertGreaterThan(boundary, reference * 0.5,
                             "crossfade must not drop out")
        XCTAssertLessThan(boundary, reference * 2.0,
                          "crossfade must not double up")
    }

    func testChordGridDeterminismAndEdgeCases() {
        let spans = [
            // Deliberately unsorted + one span past the end.
            VocoderCarriers.ChordSpan(startSec: 0.6, midiNotes: []),
            VocoderCarriers.ChordSpan(startSec: 0.2, midiNotes: [52]),
            VocoderCarriers.ChordSpan(startSec: 9.0, midiNotes: [40]),
        ]
        let a = VocoderCarriers.chordGrid(
            spans: spans, durationSec: 1.0, sampleRate: sampleRate
        )
        let b = VocoderCarriers.chordGrid(
            spans: spans, durationSec: 1.0, sampleRate: sampleRate
        )
        XCTAssertEqual(a.count, Int(sampleRate))
        XCTAssertEqual(a, b)
        // Tail is un-faded (the 9 s span was dropped): the last 50 ms
        // should carry normal level.
        XCTAssertGreaterThan(rms(a[Int(0.95 * sampleRate)...]), 0.05)
        // No spans at all → drone, not silence.
        let drone = VocoderCarriers.chordGrid(
            spans: [], durationSec: 0.5, sampleRate: sampleRate
        )
        XCTAssertGreaterThan(rms(drone[...]), 0.05)
    }

    // MARK: - M3 looped stem

    func testLoopedStemPicksPitchedRegionAndLoops() throws {
        // 0.7 s of seeded noise then 1.3 s of a 220 Hz sine — the
        // window search must land in the sine and the 3 s output
        // should read as (mostly) 220 Hz throughout.
        var rng = SplitMix64(seed: 0xC0FFEE)
        var source = (0..<Int(0.7 * sampleRate)).map { _ in
            Float(rng.nextSymmetricDouble()) * 0.5
        }
        for i in 0..<Int(1.3 * sampleRate) {
            source.append(
                0.5 * sin(Float(i) * Float(2.0 * .pi * 220.0 / sampleRate))
            )
        }

        let out = VocoderCarriers.loopedStem(
            source, sampleRate: sampleRate, durationSec: 3.0
        )
        XCTAssertEqual(out.count, Int(3.0 * sampleRate))

        let f0 = try XCTUnwrap(medianVoicedF0(out))
        XCTAssertEqual(f0, 220.0, accuracy: 220.0 * 0.03)

        // Voicedness should dominate the whole looped output.
        let frames = PSOLAHarmonizer.trackF0(out, sampleRate: sampleRate)
        let voiced = frames.filter { $0.f0Hz > 0 && $0.confidence >= 0.6 }
        XCTAssertGreaterThan(
            Double(voiced.count), 0.7 * Double(frames.count),
            "looped carrier should be pitched nearly everywhere"
        )
    }

    func testLoopedStemDeterminismAndEmptySource() {
        let source = (0..<4_800).map {
            0.4 * sin(Float($0) * Float(2.0 * .pi * 220.0 / sampleRate))
        }
        let a = VocoderCarriers.loopedStem(
            source, sampleRate: sampleRate, durationSec: 0.5
        )
        let b = VocoderCarriers.loopedStem(
            source, sampleRate: sampleRate, durationSec: 0.5
        )
        XCTAssertEqual(a, b)

        let silence = VocoderCarriers.loopedStem(
            [], sampleRate: sampleRate, durationSec: 0.5
        )
        XCTAssertEqual(silence.count, Int(0.5 * sampleRate))
        XCTAssertTrue(silence.allSatisfy { $0 == 0 })
    }

    // MARK: - M5 texture

    func testTextureLoopsShortSourceWithoutGaps() {
        // 0.25 s source tiled to 1 s: every quarter of the output must
        // carry signal (no silent tiles, no dead seams).
        let source = (0..<Int(0.25 * sampleRate)).map {
            0.5 * sin(Float($0) * Float(2.0 * .pi * 330.0 / sampleRate))
        }
        let out = VocoderCarriers.texture(
            source, sampleRate: sampleRate, durationSec: 1.0
        )
        XCTAssertEqual(out.count, Int(sampleRate))
        let quarter = out.count / 4
        for q in 0..<4 {
            XCTAssertGreaterThan(
                rms(out[(q * quarter)..<((q + 1) * quarter)]), 0.05,
                "quarter \(q) of the looped texture must sound"
            )
        }
        // Long-enough source: output is just the truncated head.
        let long = VocoderCarriers.texture(
            out, sampleRate: sampleRate, durationSec: 0.5
        )
        XCTAssertEqual(long.count, Int(0.5 * sampleRate))
    }

    // MARK: - VocoderMode

    func testVocoderModeRawValuesAreFrozen() {
        // Persisted in PadSampleMetadata.vocoderMode — renumbering is
        // a schema break.
        XCTAssertEqual(VocoderMode.classic.rawValue, 1)
        XCTAssertEqual(VocoderMode.song.rawValue, 2)
        XCTAssertEqual(VocoderMode.stem.rawValue, 3)
        XCTAssertEqual(VocoderMode.harmony.rawValue, 4)
        XCTAssertEqual(VocoderMode.texture.rawValue, 5)
        XCTAssertFalse(VocoderMode.harmony.usesSpectralVocoder)
        for mode in VocoderMode.allCases where mode != .harmony {
            XCTAssertTrue(mode.usesSpectralVocoder)
        }
    }
}
