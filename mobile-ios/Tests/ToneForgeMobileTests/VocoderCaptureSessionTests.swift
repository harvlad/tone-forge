// VocoderCaptureSessionTests.swift
//
// Exercises the capture flow's testable seams without opening the
// mic: the preview ring's underrun/mute/warm-up accounting (the P7
// dropout-gate counter), the worker's block pipeline (tap buffers in
// → modulator accumulation + ring writes out, 8 s cap, harmony dry
// preview), and the full-take offline processing switch (vocoder vs
// PSOLA). Live mic + render-thread behavior is device-manual
// (docs/mobile-testing.md).

import XCTest
import AVFoundation
import ToneForgeEngine
@testable import ToneForgeMobile

final class VocoderCaptureSessionTests: XCTestCase {

    private let rate = 48_000.0

    // MARK: - Helpers

    private func makeBuffer(
        _ samples: [Float], channels: AVAudioChannelCount = 1,
        sampleRate: Double = 48_000
    ) -> AVAudioPCMBuffer {
        let format = AVAudioFormat(
            standardFormatWithSampleRate: sampleRate, channels: channels
        )!
        let buffer = AVAudioPCMBuffer(
            pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count)
        )!
        buffer.frameLength = AVAudioFrameCount(samples.count)
        for ch in 0..<Int(channels) {
            samples.withUnsafeBufferPointer { src in
                buffer.floatChannelData![ch].update(
                    from: src.baseAddress!, count: samples.count
                )
            }
        }
        return buffer
    }

    private func sine(_ hz: Double, seconds: Double) -> [Float] {
        (0..<Int(seconds * rate)).map {
            0.4 * sin(Float($0) * Float(2.0 * .pi * hz / rate))
        }
    }

    /// Feed `samples` to a worker in tap-sized chunks and wait for
    /// the queue to go idle.
    private func feed(
        _ worker: VocoderPreviewWorker, samples: [Float], chunk: Int = 1024
    ) {
        var i = 0
        while i < samples.count {
            let end = min(i + chunk, samples.count)
            worker.ingest(makeBuffer(Array(samples[i..<end])))
            i = end
        }
        _ = worker.drain()  // queue.sync barrier
    }

    private func makeWorker(
        program: VocoderProgram, ring: VocoderPreviewRing,
        capFrames: Int = 384_000, onCap: @escaping () -> Void = {}
    ) -> VocoderPreviewWorker {
        VocoderPreviewWorker(
            program: program, ring: ring, sampleRate: rate,
            capFrames: capFrames, onCap: onCap
        )
    }

    private func classicProgram(carrierSec: Double = 2.0) -> VocoderProgram {
        VocoderProgram(
            mode: .classic,
            carrier: VocoderCarriers.sawStack(
                notes: [48], durationSec: carrierSec, sampleRate: rate
            )
        )
    }

    // MARK: - Ring

    func testRingWarmupIsSilentAndNotAnUnderrun() {
        let ring = VocoderPreviewRing(capacity: 4_096)
        ring.begin(muted: false)
        var out = [Float](repeating: 1, count: 256)
        out.withUnsafeMutableBufferPointer {
            ring.read(into: $0.baseAddress!, count: 256)
        }
        XCTAssertTrue(out.allSatisfy { $0 == 0 }, "pre-first-write = silence")
        XCTAssertEqual(ring.underruns, 0, "warm-up must not count")
    }

    func testRingReadsBackWrittenSamplesThenUnderruns() {
        let ring = VocoderPreviewRing(capacity: 4_096)
        ring.begin(muted: false)
        let payload: [Float] = (0..<300).map { Float($0) / 300 }
        ring.write(payload)

        var out = [Float](repeating: -1, count: 256)
        out.withUnsafeMutableBufferPointer {
            ring.read(into: $0.baseAddress!, count: 256)
        }
        XCTAssertEqual(out, Array(payload[0..<256]))
        XCTAssertEqual(ring.underruns, 0)

        // 44 left, ask for 256 → shortage: zero-fill + one underrun.
        out.withUnsafeMutableBufferPointer {
            ring.read(into: $0.baseAddress!, count: 256)
        }
        XCTAssertEqual(Array(out[0..<44]), Array(payload[256..<300]))
        XCTAssertTrue(out[44...].allSatisfy { $0 == 0 })
        XCTAssertEqual(ring.underruns, 1)
    }

    func testRingMutedConsumesAtPaceButEmitsSilence() {
        let ring = VocoderPreviewRing(capacity: 4_096)
        ring.begin(muted: true)
        ring.write([Float](repeating: 0.5, count: 512))

        var out = [Float](repeating: -1, count: 256)
        out.withUnsafeMutableBufferPointer {
            ring.read(into: $0.baseAddress!, count: 256)
        }
        XCTAssertTrue(out.allSatisfy { $0 == 0 }, "muted = silence out")

        // The 256 read must have CONSUMED — after reading the other
        // 256 an unmuted-style shortage would appear on the next read.
        ring.begin(muted: false)
        ring.write([Float](repeating: 0.25, count: 100))
        out.withUnsafeMutableBufferPointer {
            ring.read(into: $0.baseAddress!, count: 256)
        }
        XCTAssertEqual(Array(out[0..<100]),
                       [Float](repeating: 0.25, count: 100))
        XCTAssertEqual(ring.underruns, 1)
    }

    func testRingEndStopsUnderrunCounting() {
        let ring = VocoderPreviewRing(capacity: 1_024)
        ring.begin(muted: false)
        ring.write([1, 2, 3])
        ring.end()
        var out = [Float](repeating: -1, count: 64)
        out.withUnsafeMutableBufferPointer {
            ring.read(into: $0.baseAddress!, count: 64)
        }
        XCTAssertTrue(out.allSatisfy { $0 == 0 })
        XCTAssertEqual(ring.underruns, 0)
    }

    func testRingOverflowDropsNewestWithoutCorruption() {
        let ring = VocoderPreviewRing(capacity: 128)
        ring.begin(muted: false)
        ring.write([Float](repeating: 0.7, count: 500))  // 372 dropped
        var out = [Float](repeating: -1, count: 128)
        out.withUnsafeMutableBufferPointer {
            ring.read(into: $0.baseAddress!, count: 128)
        }
        XCTAssertTrue(out.allSatisfy { $0 == 0.7 })
    }

    // MARK: - Worker

    func testWorkerAccumulatesModulatorAndWritesPreviewBlocks() {
        let ring = VocoderPreviewRing()
        let worker = makeWorker(program: classicProgram(), ring: ring)
        let input = sine(220, seconds: 0.5)  // 24 000 samples

        feed(worker, samples: input)

        // Full dry take retained regardless of preview blocking.
        XCTAssertEqual(worker.drain(), input)

        // 24 000 / 4096 → 5 complete blocks in the ring.
        let expected = 5 * VocoderPreviewWorker.blockLen
        var out = [Float](repeating: 0, count: expected + 64)
        out.withUnsafeMutableBufferPointer {
            ring.read(into: $0.baseAddress!, count: expected + 64)
        }
        // Vocoded saw × sine should be audibly non-silent.
        let rms = (out[0..<expected].reduce(Float(0)) { $0 + $1 * $1 }
            / Float(expected)).squareRoot()
        XCTAssertGreaterThan(rms, 0.001, "preview blocks must carry audio")
        // Exactly `expected` available: the over-read underruns once.
        XCTAssertEqual(ring.underruns, 1)
        XCTAssertTrue(out[expected...].allSatisfy { $0 == 0 })
    }

    func testWorkerHarmonyModePreviewsDry() {
        let ring = VocoderPreviewRing()
        let program = VocoderProgram(mode: .harmony, carrier: [])
        let worker = makeWorker(program: program, ring: ring)
        let input = sine(220, seconds: 0.2)  // 9600 → 2 blocks

        feed(worker, samples: input)

        let expected = 2 * VocoderPreviewWorker.blockLen
        var out = [Float](repeating: 0, count: expected)
        out.withUnsafeMutableBufferPointer {
            ring.read(into: $0.baseAddress!, count: expected)
        }
        XCTAssertEqual(out, Array(input[0..<expected]),
                       "harmony preview = dry passthrough")
    }

    func testWorkerCapsAtLimitAndFiresOnCapOnce() {
        let ring = VocoderPreviewRing()
        var capFires = 0
        let capFrames = 10_000
        let worker = makeWorker(
            program: classicProgram(), ring: ring, capFrames: capFrames,
            onCap: { capFires += 1 }
        )

        feed(worker, samples: sine(220, seconds: 0.5))  // 24 000 > cap

        let raw = worker.drain()
        XCTAssertEqual(raw.count, capFrames, "hard cap mid-buffer")
        XCTAssertEqual(capFires, 1, "onCap fires exactly once")
    }

    func testWorkerMonoizesMultichannelInput() {
        let ring = VocoderPreviewRing()
        let worker = makeWorker(program: classicProgram(), ring: ring)
        // Stereo buffer with identical channels → mono average equals
        // the channel content.
        let samples: [Float] = (0..<2_000).map { Float($0 % 7) / 10 }
        worker.ingest(makeBuffer(samples, channels: 2))
        XCTAssertEqual(worker.drain(), samples)
    }

    // MARK: - Full-take processing

    func testProcessFullClassicVocodesAgainstCarrier() async {
        let program = classicProgram()
        let raw = sine(220, seconds: 1.0)
        let processed = await VocoderCaptureSession.processFull(
            raw: raw, program: program, sampleRate: rate
        )
        XCTAssertEqual(processed.count, raw.count,
                       "vocoder output length == modulator length")
        let rms = (processed.reduce(Float(0)) { $0 + $1 * $1 }
            / Float(processed.count)).squareRoot()
        XCTAssertGreaterThan(rms, 0.001)
        // Deterministic: same take, same program → identical result.
        let again = await VocoderCaptureSession.processFull(
            raw: raw, program: program, sampleRate: rate
        )
        XCTAssertEqual(processed, again)
    }

    func testProcessFullHarmonyRunsPSOLA() async {
        let program = VocoderProgram(
            mode: .harmony,
            carrier: [],
            chordSpans: [.init(startSec: 0, midiNotes: [57, 60, 64])],
            harmonySettings: HarmonySettings(
                addThird: true, addFifth: false, addOctave: false,
                choir: false
            )
        )
        let raw = sine(220, seconds: 1.0)  // A3 — chord tone, voiced
        let processed = await VocoderCaptureSession.processFull(
            raw: raw, program: program, sampleRate: rate
        )
        XCTAssertFalse(processed.isEmpty)
        // Harmonize keeps the dry voice at unity, so the result can't
        // be silence and shouldn't be plain passthrough either (a
        // third voice was added).
        let rms = (processed.reduce(Float(0)) { $0 + $1 * $1 }
            / Float(processed.count)).squareRoot()
        XCTAssertGreaterThan(rms, 0.05)
        XCTAssertNotEqual(Array(processed.prefix(raw.count)), raw)
    }

    func testProcessFullEmptyTakeIsEmpty() async {
        let processed = await VocoderCaptureSession.processFull(
            raw: [], program: classicProgram(), sampleRate: rate
        )
        XCTAssertTrue(processed.isEmpty)
    }
}
