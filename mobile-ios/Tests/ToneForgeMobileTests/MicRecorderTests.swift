// MicRecorderTests.swift
//
// Pure-logic coverage for the mic capture path: CaptureBox cap
// exactness + mono-izing, and the one-shot AVAudioConverter resample.
// No AVAudioEngine / real mic involved — those paths are exercised on
// device (docs/mobile-testing.md).

import XCTest
import AVFoundation
import ToneForgeEngine
@testable import ToneForgeMobile

final class MicRecorderTests: XCTestCase {

    // MARK: - Helpers

    /// Deinterleaved Float32 PCM buffer with per-channel fill closures.
    private func makeBuffer(
        channels: [[Float]], sampleRate: Double = 48_000
    ) -> AVAudioPCMBuffer {
        let format = AVAudioFormat(
            standardFormatWithSampleRate: sampleRate,
            channels: AVAudioChannelCount(channels.count)
        )!
        let frames = channels[0].count
        let buffer = AVAudioPCMBuffer(
            pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)
        )!
        buffer.frameLength = AVAudioFrameCount(frames)
        for (ch, samples) in channels.enumerated() {
            samples.withUnsafeBufferPointer { src in
                buffer.floatChannelData![ch].update(
                    from: src.baseAddress!, count: frames
                )
            }
        }
        return buffer
    }

    private func tone(
        _ hz: Double, seconds: Double, sampleRate: Double
    ) -> [Float] {
        (0..<Int(seconds * sampleRate)).map { i in
            0.5 * Float(sin(2 * .pi * hz * Double(i) / sampleRate))
        }
    }

    /// Dominant frequency via naive DFT peak over a coarse grid —
    /// good enough to confirm pitch survives resampling.
    private func dominantHz(
        _ x: [Float], sampleRate: Double, candidates: [Double]
    ) -> Double {
        var best = (hz: 0.0, mag: -1.0)
        for hz in candidates {
            var re = 0.0, im = 0.0
            let w = 2 * Double.pi * hz / sampleRate
            for (i, s) in x.enumerated() {
                re += Double(s) * cos(w * Double(i))
                im += Double(s) * sin(w * Double(i))
            }
            let mag = re * re + im * im
            if mag > best.mag { best = (hz, mag) }
        }
        return best.hz
    }

    // MARK: - CaptureBox

    func testAppendAccumulatesUnderCap() {
        let box = CaptureBox(capFrames: 1000)
        let hitCap = box.append(makeBuffer(channels: [[Float](repeating: 0.25, count: 400)]))
        XCTAssertFalse(hitCap)
        let (samples, rate) = box.drain()
        XCTAssertEqual(samples.count, 400)
        XCTAssertEqual(rate, 48_000)
        XCTAssertEqual(samples[0], 0.25)
    }

    func testCapTruncatesMidBufferExactly() {
        let box = CaptureBox(capFrames: 1000)
        _ = box.append(makeBuffer(channels: [[Float](repeating: 0.1, count: 700)]))
        // This buffer crosses the cap: only 300 of its 700 frames fit.
        let hitCap = box.append(makeBuffer(channels: [[Float](repeating: 0.2, count: 700)]))
        XCTAssertTrue(hitCap)
        let (samples, _) = box.drain()
        XCTAssertEqual(samples.count, 1000)
        XCTAssertEqual(samples[699], 0.1)
        XCTAssertEqual(samples[700], 0.2)
        XCTAssertEqual(samples[999], 0.2)
    }

    func testCapReturnsTrueExactlyOnceAndIgnoresLaterAppends() {
        let box = CaptureBox(capFrames: 100)
        XCTAssertTrue(box.append(makeBuffer(channels: [[Float](repeating: 0.1, count: 150)])))
        // Post-cap appends are dropped and never re-signal.
        XCTAssertFalse(box.append(makeBuffer(channels: [[Float](repeating: 0.9, count: 50)])))
        let (samples, _) = box.drain()
        XCTAssertEqual(samples.count, 100)
        XCTAssertEqual(samples.last, 0.1)
    }

    func testStereoIsAveragedToMono() {
        let box = CaptureBox(capFrames: 1000)
        let left = [Float](repeating: 0.8, count: 10)
        let right = [Float](repeating: 0.2, count: 10)
        _ = box.append(makeBuffer(channels: [left, right]))
        let (samples, _) = box.drain()
        XCTAssertEqual(samples.count, 10)
        for s in samples {
            XCTAssertEqual(s, 0.5, accuracy: 1e-6)
        }
    }

    func testDrainOnEmptyBoxIsEmptyWithZeroRate() {
        let box = CaptureBox(capFrames: 100)
        let (samples, rate) = box.drain()
        XCTAssertTrue(samples.isEmpty)
        XCTAssertEqual(rate, 0)
    }

    // MARK: - Resample

    func testResampleIdentityAtEqualRates() {
        let x = tone(440, seconds: 0.1, sampleRate: 48_000)
        let y = MicRecorder.resample(x, from: 48_000, to: 48_000)
        XCTAssertEqual(y, x)
    }

    func testResampleEmptyInputIsEmpty() {
        XCTAssertTrue(MicRecorder.resample([], from: 44_100, to: 48_000).isEmpty)
    }

    func testResample44100To48000CountRatio() {
        let x = tone(440, seconds: 0.5, sampleRate: 44_100)
        let y = MicRecorder.resample(x, from: 44_100, to: 48_000)
        let expected = Double(x.count) * 48_000 / 44_100
        XCTAssertEqual(Double(y.count), expected, accuracy: expected * 0.01)
    }

    func testResamplePreservesToneFrequency() {
        let x = tone(440, seconds: 0.25, sampleRate: 44_100)
        let y = MicRecorder.resample(x, from: 44_100, to: 48_000)
        let hz = dominantHz(
            y, sampleRate: 48_000,
            candidates: [220, 330, 404, 440, 479, 660, 880]
        )
        XCTAssertEqual(hz, 440)
        // Amplitude survives (no gain surprises from the converter).
        let peak = y.map(abs).max() ?? 0
        XCTAssertEqual(peak, 0.5, accuracy: 0.05)
    }

    func testResampleDownsamplePreservesToneFrequency() {
        let x = tone(1000, seconds: 0.25, sampleRate: 48_000)
        let y = MicRecorder.resample(x, from: 48_000, to: 44_100)
        let hz = dominantHz(
            y, sampleRate: 44_100,
            candidates: [500, 919, 1000, 1088, 2000]
        )
        XCTAssertEqual(hz, 1000)
    }
}
