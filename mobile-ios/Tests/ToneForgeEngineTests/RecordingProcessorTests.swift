// RecordingProcessorTests.swift
//
// Pins the mic-capture conditioning pipeline: silence trim, DC
// removal, −1 dBFS normalise, zero-cross-safe boundaries, and
// spectral-flux onset detection — all on synthetic material with
// known ground truth. Deterministic (no RNG anywhere in the unit).

import XCTest
import Accelerate
@testable import ToneForgeEngine

final class RecordingProcessorTests: XCTestCase {

    private let sr: Double = 48_000

    // MARK: - Synthesis helpers

    private func silence(_ seconds: Double) -> [Float] {
        [Float](repeating: 0, count: Int(seconds * sr))
    }

    private func tone(
        _ seconds: Double, hz: Double, amplitude: Float = 0.5
    ) -> [Float] {
        let n = Int(seconds * sr)
        return (0..<n).map { i in
            amplitude * Float(sin(2 * .pi * hz * Double(i) / sr))
        }
    }

    /// Exponentially decaying burst — a clap/click stand-in with a
    /// hard attack the flux detector must find.
    private func burst(
        _ seconds: Double, hz: Double = 1_500, amplitude: Float = 0.9
    ) -> [Float] {
        let n = Int(seconds * sr)
        return (0..<n).map { i in
            let t = Double(i) / sr
            let env = Float(exp(-t / 0.03))
            return amplitude * env * Float(sin(2 * .pi * hz * t))
        }
    }

    // MARK: - Degenerate inputs

    func testEmptyInputReturnsEmptyOutput() {
        let out = RecordingProcessor.process([], sampleRate: sr)
        XCTAssertTrue(out.samples.isEmpty)
        XCTAssertTrue(out.transientOffsets.isEmpty)
    }

    func testPureSilenceReturnsEmptyOutput() {
        let out = RecordingProcessor.process(silence(1.0), sampleRate: sr)
        XCTAssertTrue(out.samples.isEmpty)
        XCTAssertTrue(out.transientOffsets.isEmpty)
    }

    // MARK: - Trim

    func testLeadingAndTrailingSilenceTrimmed() {
        let signal = tone(0.5, hz: 440)
        let input = silence(0.8) + signal + silence(0.6)
        let out = RecordingProcessor.process(input, sampleRate: sr)

        // Kept length ≈ signal length; boundary snap + window
        // granularity allow a little slack either side.
        let tolerance = Int(0.030 * sr)
        XCTAssertEqual(out.samples.count, signal.count, accuracy: tolerance)
        XCTAssertGreaterThan(out.samples.count, 0)
    }

    func testQuietRoomToneAroundLoudSignalTrimmed() {
        // Room tone at −52 dB around a −6 dB tone: the adaptive
        // threshold (8% of p95) must sit above the room tone.
        let room = tone(0.5, hz: 120, amplitude: 0.0025)
        let signal = tone(0.4, hz: 440, amplitude: 0.5)
        let input = room + signal + room
        let out = RecordingProcessor.process(input, sampleRate: sr)

        let tolerance = Int(0.030 * sr)
        XCTAssertEqual(out.samples.count, signal.count, accuracy: tolerance)
    }

    // MARK: - DC block

    func testDCOffsetRemoved() {
        let input = tone(0.5, hz: 440, amplitude: 0.4).map { $0 + 0.3 }
        let out = RecordingProcessor.process(input, sampleRate: sr)
        XCTAssertFalse(out.samples.isEmpty)

        var mean: Float = 0
        vDSP_meanv(out.samples, 1, &mean, vDSP_Length(out.samples.count))
        XCTAssertEqual(mean, 0, accuracy: 0.01)
    }

    // MARK: - Normalise

    func testPeakNormalisedToMinusOneDBFS() {
        let input = silence(0.1) + tone(0.5, hz: 440, amplitude: 0.2) + silence(0.1)
        let out = RecordingProcessor.process(input, sampleRate: sr)

        var peak: Float = 0
        vDSP_maxmgv(out.samples, 1, &peak, vDSP_Length(out.samples.count))
        // −1 dBFS = 0.8913; allow the DC blocker's tiny transient.
        XCTAssertEqual(peak, 0.8913, accuracy: 0.01)
    }

    func testNormaliseAlsoAttenuatesHotInput() {
        let input = tone(0.3, hz: 440, amplitude: 0.99)
        let out = RecordingProcessor.process(input, sampleRate: sr)

        var peak: Float = 0
        vDSP_maxmgv(out.samples, 1, &peak, vDSP_Length(out.samples.count))
        XCTAssertLessThanOrEqual(peak, 0.90)
        XCTAssertEqual(peak, 0.8913, accuracy: 0.01)
    }

    // MARK: - Zero-cross boundaries

    func testBoundariesStartNearZero() {
        let input = silence(0.3) + tone(0.5, hz: 440) + silence(0.3)
        let out = RecordingProcessor.process(input, sampleRate: sr)
        XCTAssertFalse(out.samples.isEmpty)

        // First and last samples should be near a zero crossing —
        // well below the normalised peak.
        XCTAssertLessThan(abs(out.samples.first!), 0.1)
        XCTAssertLessThan(abs(out.samples.last!), 0.1)
    }

    // MARK: - Transients

    func testTwoClapsDetectedAtKnownOffsets() {
        // Two bursts 0.5 s apart inside a quiet bed.
        let bed = tone(1.2, hz: 200, amplitude: 0.02)
        var input = bed
        let clapA = burst(0.15)
        let clapB = burst(0.15)
        let posA = Int(0.2 * sr)
        let posB = Int(0.7 * sr)
        for (i, s) in clapA.enumerated() { input[posA + i] += s }
        for (i, s) in clapB.enumerated() { input[posB + i] += s }

        let out = RecordingProcessor.process(input, sampleRate: sr)
        XCTAssertEqual(out.transientOffsets.count, 2, "\(out.transientOffsets)")

        // The trim keeps (almost) the whole bed, so offsets should
        // land near the clap positions minus the trimmed lead-in.
        // Locate the claps in the OUTPUT by allowing generous slack:
        // one STFT frame (1024) + trim slack (~1 window).
        let slack = Int(0.030 * sr) + RecordingProcessor.fftSize
        let gap = out.transientOffsets[1] - out.transientOffsets[0]
        XCTAssertEqual(gap, posB - posA, accuracy: slack)
    }

    func testSteadyToneHasNoSpuriousOnsetStorm() {
        let out = RecordingProcessor.process(tone(1.0, hz: 440), sampleRate: sr)
        // The attack edge itself may register; a steady tone must not
        // produce a stream of onsets.
        XCTAssertLessThanOrEqual(out.transientOffsets.count, 1)
    }

    func testTransientOffsetsAscendingAndInRange() {
        var input = tone(1.0, hz: 300, amplitude: 0.05)
        for k in 0..<4 {
            let pos = Int((0.1 + 0.22 * Double(k)) * sr)
            for (i, s) in burst(0.1).enumerated() { input[pos + i] += s }
        }
        let out = RecordingProcessor.process(input, sampleRate: sr)

        XCTAssertEqual(out.transientOffsets, out.transientOffsets.sorted())
        for offset in out.transientOffsets {
            XCTAssertGreaterThanOrEqual(offset, 0)
            XCTAssertLessThan(offset, out.samples.count)
        }
        XCTAssertGreaterThanOrEqual(out.transientOffsets.count, 3)
    }

    func testDeterministic() {
        let input = silence(0.2) + burst(0.3) + tone(0.4, hz: 440) + silence(0.2)
        let a = RecordingProcessor.process(input, sampleRate: sr)
        let b = RecordingProcessor.process(input, sampleRate: sr)
        XCTAssertEqual(a, b)
    }
}

/// XCTAssertEqual(_:_:accuracy:) for Int.
private func XCTAssertEqual(
    _ a: Int, _ b: Int, accuracy: Int,
    file: StaticString = #filePath, line: UInt = #line
) {
    XCTAssertLessThanOrEqual(
        abs(a - b), accuracy,
        "\(a) is not within \(accuracy) of \(b)",
        file: file, line: line
    )
}
