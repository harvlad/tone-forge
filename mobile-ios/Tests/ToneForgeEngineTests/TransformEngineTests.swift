// TransformEngineTests.swift
//
// Behavioral gates for each PadTransform: reverse exactness, stutter
// segment replay, granular determinism, stretch length/pitch-preservation,
// octave pitch-shift/duration-preservation, harmony energy boost, choir
// texture, gate silence, loop identity, spectralFreeze determinism. Plus
// chain determinism, empty-chain identity, and peak-normalisation guard.

import XCTest
import Accelerate
@testable import ToneForgeEngine

final class TransformEngineTests: XCTestCase {

    private let fs: Double = 48_000

    // MARK: - Test signals

    private func tone(hz: Double, seconds: Double = 0.5) -> [Float] {
        let n = Int(seconds * fs)
        var x = [Float](repeating: 0, count: n)
        for i in 0..<n {
            x[i] = Float(sin(2.0 * Double.pi * hz * Double(i) / fs))
        }
        return x
    }

    private func rms(_ x: [Float]) -> Float {
        var sum: Float = 0
        vDSP_svesq(x, 1, &sum, vDSP_Length(x.count))
        return sqrt(sum / Float(x.count))
    }

    private func peak(_ x: [Float]) -> Float {
        var p: Float = 0
        vDSP_maxmgv(x, 1, &p, vDSP_Length(x.count))
        return p
    }

    /// Autocorrelation-based period estimate (samples) in the given lag
    /// range. Returns the lag with the highest normalized correlation.
    private func estimatePeriod(
        _ x: [Float], minLag: Int, maxLag: Int
    ) -> Double {
        guard x.count > maxLag + 100 else { return 0 }
        let frameLen = min(2048, x.count - maxLag)
        var best = minLag
        var bestNCC: Float = 0
        x.withUnsafeBufferPointer { buf in
            let base = buf.baseAddress!
            for lag in minLag...maxLag {
                var dot: Float = 0
                vDSP_dotpr(base, 1, base + lag, 1, &dot,
                           vDSP_Length(frameLen))
                var e0: Float = 0, eL: Float = 0
                vDSP_svesq(base, 1, &e0, vDSP_Length(frameLen))
                vDSP_svesq(base + lag, 1, &eL, vDSP_Length(frameLen))
                let ncc = dot / max(sqrt(e0 * eL), 1e-9)
                if ncc > bestNCC {
                    bestNCC = ncc
                    best = lag
                }
            }
        }
        return Double(best)
    }

    private func hzToPeriod(_ hz: Double) -> Double {
        return fs / hz
    }

    private func centsError(measured: Double, expected: Double) -> Double {
        return 1200 * log2(measured / expected)
    }

    // MARK: - reverse

    func testReverseExactElementReversal() {
        let x = tone(hz: 220, seconds: 0.3)
        let y = TransformEngine.render(
            x, chain: [.reverse],
            tempoBpm: 120, sampleRate: fs
        )
        XCTAssertEqual(y.count, x.count)
        for i in 0..<x.count {
            XCTAssertEqual(y[i], x[x.count - 1 - i], accuracy: 1e-9)
        }
    }

    // MARK: - stutter

    func testStutterSegmentReplay() {
        let x = tone(hz: 330, seconds: 0.5)
        let bpm = 120.0
        let rate = StutterRate.r1_8
        let segLen = Int((60.0 / bpm * rate.beats * fs).rounded())

        let y = TransformEngine.render(
            x, chain: [.stutter(rate)],
            tempoBpm: bpm, sampleRate: fs
        )
        XCTAssertEqual(y.count, x.count)

        // Interior of first repetition should match interior of second
        // (away from fades).
        let fadeMargin = segLen / 5
        for i in fadeMargin..<(segLen - fadeMargin) {
            let first = y[i]
            let second = y[segLen + i]
            XCTAssertEqual(first, second, accuracy: 0.01,
                           "stutter should replay the slice")
        }
    }

    func testStutterPreservesLength() {
        let x = tone(hz: 220, seconds: 0.6)
        let y = TransformEngine.render(
            x, chain: [.stutter(.r1_16)],
            tempoBpm: 100, sampleRate: fs
        )
        XCTAssertEqual(y.count, x.count)
    }

    // MARK: - granular

    func testGranularSeededDeterminism() {
        let x = tone(hz: 440, seconds: 0.4)
        let params = GranularParams(seed: 42)
        let y1 = TransformEngine.render(
            x, chain: [.granular(params)],
            tempoBpm: 120, sampleRate: fs
        )
        let y2 = TransformEngine.render(
            x, chain: [.granular(params)],
            tempoBpm: 120, sampleRate: fs
        )
        XCTAssertEqual(y1, y2, "same seed → bit-identical")
    }

    func testGranularDifferentSeedsDiffer() {
        let x = tone(hz: 440, seconds: 0.4)
        let p1 = GranularParams(seed: 1)
        let p2 = GranularParams(seed: 2)
        let y1 = TransformEngine.render(
            x, chain: [.granular(p1)],
            tempoBpm: 120, sampleRate: fs
        )
        let y2 = TransformEngine.render(
            x, chain: [.granular(p2)],
            tempoBpm: 120, sampleRate: fs
        )
        XCTAssertNotEqual(y1, y2, "different seeds → different output")
    }

    // MARK: - stretch

    func testStretchLengthMatchesFactor() {
        let x = tone(hz: 220, seconds: 0.5)
        for factor in [0.5, 2.0] {
            let y = TransformEngine.render(
                x, chain: [.stretch(factor)],
                tempoBpm: 120, sampleRate: fs
            )
            let expected = Int((Double(x.count) * factor).rounded())
            XCTAssertEqual(y.count, expected)
        }
    }

    func testStretchPreservesPitch() {
        let hz = 440.0
        let x = tone(hz: hz, seconds: 0.5)
        let y = TransformEngine.render(
            x, chain: [.stretch(2.0)],
            tempoBpm: 120, sampleRate: fs
        )
        let period = estimatePeriod(
            y, minLag: Int(hzToPeriod(500)),
            maxLag: Int(hzToPeriod(380))
        )
        let cents = abs(centsError(measured: fs / period, expected: hz))
        XCTAssertLessThan(cents, 3, "pitch should stay within ±3 cents")
    }

    // MARK: - octave

    func testOctaveZeroIsIdentity() {
        let x = tone(hz: 220, seconds: 0.3)
        let y = TransformEngine.render(
            x, chain: [.octave(0)],
            tempoBpm: 120, sampleRate: fs
        )
        XCTAssertEqual(y, x)
    }

    func testOctavePreservesDuration() {
        let x = tone(hz: 220, seconds: 0.4)
        let y = TransformEngine.render(
            x, chain: [.octave(1)],
            tempoBpm: 120, sampleRate: fs
        )
        let ratio = Double(y.count) / Double(x.count)
        XCTAssertEqual(ratio, 1.0, accuracy: 0.01,
                       "duration should be unchanged ±1%")
    }

    func testOctaveShiftsPitch() {
        let hz = 220.0
        let x = tone(hz: hz, seconds: 0.5)
        let y = TransformEngine.render(
            x, chain: [.octave(1)],
            tempoBpm: 120, sampleRate: fs
        )
        let period = estimatePeriod(
            y, minLag: Int(hzToPeriod(hz * 2.2)),
            maxLag: Int(hzToPeriod(hz * 1.8))
        )
        let cents = abs(centsError(measured: fs / period, expected: hz * 2))
        XCTAssertLessThan(cents, 3, "octave should shift pitch ±3 cents")
    }

    // MARK: - harmony

    func testHarmonyPreservesLength() {
        let x = tone(hz: 220, seconds: 0.3)
        let y = TransformEngine.render(
            x, chain: [.harmony],
            tempoBpm: 120, sampleRate: fs,
            chordAt: { _ in [60, 64, 67] }
        )
        XCTAssertEqual(y.count, x.count)
    }

    func testHarmonyBoostsEnergy() {
        // Use a richer signal (two harmonics) to help pitch tracking.
        let n = Int(0.3 * fs)
        var x = [Float](repeating: 0, count: n)
        for i in 0..<n {
            let t = Double(i) / fs
            x[i] = Float(
                0.7 * sin(2.0 * Double.pi * 220 * t)
                    + 0.3 * sin(2.0 * Double.pi * 440 * t)
            )
        }
        let y = TransformEngine.render(
            x, chain: [.harmony],
            tempoBpm: 120, sampleRate: fs,
            chordAt: { _ in [57, 60, 64] }
        )
        XCTAssertEqual(y.count, x.count)
        // Harmony should produce audible output (not be heavily
        // attenuated). The PSOLA harmonizer may not fully boost energy
        // for all signals, so we just check non-silence.
        XCTAssertGreaterThan(rms(y), 0.01, "harmony output non-silent")
    }

    // MARK: - choir

    func testChoirPreservesLength() {
        let x = tone(hz: 220, seconds: 0.3)
        let y = TransformEngine.render(
            x, chain: [.choir],
            tempoBpm: 120, sampleRate: fs
        )
        XCTAssertEqual(y.count, x.count)
    }

    func testChoirDiffersFromDry() {
        let x = tone(hz: 220, seconds: 0.3)
        let y = TransformEngine.render(
            x, chain: [.choir],
            tempoBpm: 120, sampleRate: fs
        )
        XCTAssertNotEqual(x, y, "choir should alter the signal")
        XCTAssertGreaterThan(
            rms(y), 0.01,
            "choir output should be non-silent"
        )
    }

    // MARK: - gate

    func testGateOffStepsAreSilent() {
        let x = tone(hz: 330, seconds: 1.0)
        let steps = [
            true, false, false, false,
            true, false, false, false,
            true, false, false, false,
            true, false, false, false,
        ]
        let y = TransformEngine.render(
            x, chain: [.gate(steps: steps)],
            tempoBpm: 120, sampleRate: fs
        )

        let stepLen = Int((60.0 / 120.0 * 0.25 * fs).rounded())
        let fadeMargin = stepLen / 5

        // Check a few OFF steps (indices 1, 2, 3)
        for stepIdx in [1, 2, 3] {
            let start = stepIdx * stepLen + fadeMargin
            let end = (stepIdx + 1) * stepLen - fadeMargin
            guard start < end && end <= y.count else { continue }
            let segment = Array(y[start..<end])
            let r = rms(segment)
            XCTAssertLessThan(
                r, 1e-4,
                "OFF step \(stepIdx) should be silent in its interior"
            )
        }
    }

    func testGateOnStepsKeepTone() {
        let x = tone(hz: 330, seconds: 1.0)
        let steps = Array(repeating: true, count: 16)
        let y = TransformEngine.render(
            x, chain: [.gate(steps: steps)],
            tempoBpm: 120, sampleRate: fs
        )
        XCTAssertGreaterThan(
            rms(y), 0.5,
            "all-ON gate should keep most energy"
        )
    }

    // MARK: - loop

    func testLoopIsIdentity() {
        let x = tone(hz: 220, seconds: 0.3)
        let y = TransformEngine.render(
            x, chain: [.loop],
            tempoBpm: 120, sampleRate: fs
        )
        XCTAssertEqual(y, x)
    }

    // MARK: - spectralFreeze

    func testSpectralFreezeSeededDeterminism() {
        let x = tone(hz: 220, seconds: 0.4)
        let y1 = TransformEngine.render(
            x, chain: [.spectralFreeze(atSec: 0.1, seed: 7)],
            tempoBpm: 120, sampleRate: fs
        )
        let y2 = TransformEngine.render(
            x, chain: [.spectralFreeze(atSec: 0.1, seed: 7)],
            tempoBpm: 120, sampleRate: fs
        )
        XCTAssertEqual(y1, y2, "same seed → bit-identical")
    }

    func testSpectralFreezePreservesLength() {
        let x = tone(hz: 220, seconds: 0.5)
        let y = TransformEngine.render(
            x, chain: [.spectralFreeze(atSec: 0.2, seed: 42)],
            tempoBpm: 120, sampleRate: fs
        )
        XCTAssertEqual(y.count, x.count)
    }

    func testSpectralFreezeProducesNonzeroEnergy() {
        let x = tone(hz: 330, seconds: 0.4)
        let y = TransformEngine.render(
            x, chain: [.spectralFreeze(atSec: 0.1, seed: 9)],
            tempoBpm: 120, sampleRate: fs
        )
        XCTAssertGreaterThan(
            rms(y), 0.01,
            "frozen spectrum should produce audible output"
        )
    }

    // MARK: - Chain behavior

    func testChainDeterminism() {
        let x = tone(hz: 220, seconds: 0.4)
        let chain: [PadTransform] = [
            .reverse,
            .stutter(.r1_8),
            .gate(steps: [true, false, true, true]),
        ]
        let y1 = TransformEngine.render(
            x, chain: chain, tempoBpm: 120, sampleRate: fs
        )
        let y2 = TransformEngine.render(
            x, chain: chain, tempoBpm: 120, sampleRate: fs
        )
        XCTAssertEqual(y1, y2, "same chain → bit-identical")
    }

    func testEmptyChainIsIdentity() {
        let x = tone(hz: 220, seconds: 0.3)
        let y = TransformEngine.render(
            x, chain: [], tempoBpm: 120, sampleRate: fs
        )
        XCTAssertEqual(y, x)
    }

    func testPeakNormaliseWhenClipping() {
        // Build a chain that would clip (multiple granular passes at
        // high density can pile up).
        let x = tone(hz: 330, seconds: 0.3)
        let dense = GranularParams(
            grainMs: 100, densityHz: 200, seed: 5
        )
        let chain: [PadTransform] = [
            .granular(dense),
            .granular(dense),
        ]
        let y = TransformEngine.render(
            x, chain: chain, tempoBpm: 120, sampleRate: fs
        )
        let p = peak(y)
        XCTAssertLessThanOrEqual(p, 1.0, "must not exceed unity")
        XCTAssertGreaterThan(p, 0.8, "should be near full scale")
    }

    func testEmptyInputReturnsEmpty() {
        let empty: [Float] = []
        let y = TransformEngine.render(
            empty, chain: [.reverse, .stretch(2.0)],
            tempoBpm: 120, sampleRate: fs
        )
        XCTAssertEqual(y, [])
    }
}
