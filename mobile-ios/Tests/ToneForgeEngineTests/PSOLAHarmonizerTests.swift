// PSOLAHarmonizerTests.swift
//
// Gates the pitch tracker (cent-level accuracy on a sine), the PSOLA
// shifter (a +7 st shift of a harmonic tone must land within ±3 cents
// of the target F0; unvoiced input must pass through), and harmonize
// (deterministic, keeps the dry pitch, choir actually changes the
// render).

import XCTest
import Accelerate
@testable import ToneForgeEngine

final class PSOLAHarmonizerTests: XCTestCase {

    private let fs: Double = 48_000

    // MARK: - Signal helpers

    private func sine(_ freq: Double, count: Int, amplitude: Float = 0.5) -> [Float] {
        (0..<count).map { amplitude * Float(sin(2 * Double.pi * freq * Double($0) / fs)) }
    }

    /// Saw-ish voiced tone: 6 harmonics at 1/h amplitude.
    private func harmonicTone(_ f0: Double, count: Int) -> [Float] {
        var out = [Float](repeating: 0, count: count)
        for h in 1...6 {
            let w = 2 * Double.pi * f0 * Double(h) / fs
            let amp = Float(0.3 / Double(h))
            for n in 0..<count { out[n] += amp * Float(sin(w * Double(n))) }
        }
        return out
    }

    /// Deterministic LCG noise in ±0.5.
    private func whiteNoise(_ count: Int, seed: UInt64) -> [Float] {
        var state = seed
        var out = [Float](repeating: 0, count: count)
        for i in 0..<count {
            state = state &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
            out[i] = Float(state >> 40) / Float(1 << 24) - 0.5
        }
        return out
    }

    private func cents(_ a: Double, _ b: Double) -> Double {
        1_200 * log2(a / b)
    }

    /// Median voiced F0 over frames with centers inside [from, to] sec.
    private func medianF0(
        _ track: [(timeSec: Double, f0Hz: Double, confidence: Double)],
        from: Double, to: Double
    ) -> Double {
        let voiced = track
            .filter { $0.timeSec >= from && $0.timeSec <= to && $0.confidence >= 0.6 }
            .map(\.f0Hz)
            .sorted()
        XCTAssertFalse(voiced.isEmpty, "no voiced frames in [\(from), \(to)]")
        return voiced.isEmpty ? 0 : voiced[voiced.count / 2]
    }

    private func correlation(_ x: [Float], _ y: [Float]) -> Float {
        precondition(x.count == y.count)
        var xy: Float = 0
        var xx: Float = 0
        var yy: Float = 0
        vDSP_dotpr(x, 1, y, 1, &xy, vDSP_Length(x.count))
        vDSP_dotpr(x, 1, x, 1, &xx, vDSP_Length(x.count))
        vDSP_dotpr(y, 1, y, 1, &yy, vDSP_Length(y.count))
        return xy / max(sqrt(xx * yy), 1e-12)
    }

    // MARK: - trackF0

    func testTrackF0OnSine() {
        let input = sine(220, count: 48_000)
        let track = PSOLAHarmonizer.trackF0(input, sampleRate: fs)
        XCTAssertFalse(track.isEmpty)

        let median = medianF0(track, from: 0.1, to: 0.9)
        XCTAssertLessThanOrEqual(abs(cents(median, 220)), 3, "median F0 \(median) Hz")

        let confidences = track
            .filter { $0.timeSec >= 0.1 && $0.timeSec <= 0.9 }
            .map(\.confidence)
            .sorted()
        XCTAssertGreaterThan(confidences[confidences.count / 2], 0.8)
    }

    func testTrackF0OnSilenceIsUnvoiced() {
        let track = PSOLAHarmonizer.trackF0(
            [Float](repeating: 0, count: 24_000), sampleRate: fs
        )
        XCTAssertFalse(track.isEmpty)
        for frame in track {
            XCTAssertEqual(frame.f0Hz, 0)
            XCTAssertEqual(frame.confidence, 0)
        }
    }

    // MARK: - shift

    func testShiftUpFifthHitsTargetF0() {
        let input = harmonicTone(220, count: 48_000)
        let shifted = PSOLAHarmonizer.shift(input, sampleRate: fs, semitones: 7)
        XCTAssertEqual(shifted.count, input.count)
        XCTAssertTrue(shifted.allSatisfy(\.isFinite))

        let target = 220 * pow(2.0, 7.0 / 12.0)  // 329.63 Hz
        let track = PSOLAHarmonizer.trackF0(shifted, sampleRate: fs)
        let median = medianF0(track, from: 0.1, to: 0.85)
        XCTAssertLessThanOrEqual(
            abs(cents(median, target)), 3,
            "shifted F0 \(median) Hz, target \(target) Hz"
        )
    }

    func testUnvoicedInputPassesThrough() {
        let input = whiteNoise(24_000, seed: 0xD1CE)
        let shifted = PSOLAHarmonizer.shift(input, sampleRate: fs, semitones: 7)
        XCTAssertEqual(shifted.count, input.count)
        XCTAssertGreaterThanOrEqual(correlation(input, shifted), 0.95)
    }

    // MARK: - harmonize

    func testHarmonizeAMinorIsDeterministicAndKeepsDryPitch() {
        let input = harmonicTone(220, count: 38_400)  // A3 over Am
        let chordAt: (Double) -> [Int] = { _ in [57, 60, 64] }

        let a = PSOLAHarmonizer.harmonize(
            input, sampleRate: fs, chordAt: chordAt, settings: HarmonySettings()
        )
        let b = PSOLAHarmonizer.harmonize(
            input, sampleRate: fs, chordAt: chordAt, settings: HarmonySettings()
        )
        XCTAssertEqual(a, b, "harmonize must be bit-identical")
        XCTAssertEqual(a.count, input.count)
        XCTAssertTrue(a.allSatisfy(\.isFinite))

        var rms: Float = 0
        vDSP_rmsqv(a, 1, &rms, vDSP_Length(a.count))
        XCTAssertGreaterThan(rms, 0.01, "harmonized output is near-silent")

        // The dry voice is mixed at unity, so the dry F0 must survive.
        let n = 32_768
        let start = (a.count - n) / 2
        let mags = DSPTestSupport.magnitudeSpectrum(Array(a[start..<start + n]))
        let hzPerBin = fs / Double(n)
        func power(_ lo: Double, _ hi: Double) -> Float {
            var p: Float = 0
            for k in mags.indices {
                let f = Double(k) * hzPerBin
                if f >= lo && f <= hi { p += mags[k] * mags[k] }
            }
            return p
        }
        let dry = power(210, 230)
        let gap = power(180, 200)  // no chord tone or harmonic here
        XCTAssertGreaterThan(
            DSPTestSupport.dB(dry.squareRoot(), over: gap.squareRoot()), 12,
            "dry 220 Hz fundamental missing from the mix"
        )
    }

    func testChoirChangesTheRender() {
        let input = harmonicTone(220, count: 38_400)
        let chordAt: (Double) -> [Int] = { _ in [57, 60, 64] }
        let plain = PSOLAHarmonizer.harmonize(
            input, sampleRate: fs, chordAt: chordAt,
            settings: HarmonySettings(choir: false)
        )
        let choir = PSOLAHarmonizer.harmonize(
            input, sampleRate: fs, chordAt: chordAt,
            settings: HarmonySettings(choir: true)
        )
        XCTAssertEqual(plain.count, choir.count)
        XCTAssertTrue(choir.allSatisfy(\.isFinite))
        XCTAssertNotEqual(plain, choir, "choir must alter the render")
    }
}
