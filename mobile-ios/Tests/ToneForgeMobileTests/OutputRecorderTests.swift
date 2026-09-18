// OutputRecorderTests.swift
//
// Pure-logic coverage for the session-output recorder: the stereo peak
// meter, the AAC settings derived from the tap format, and the one
// engine-state transition that's safe to assert off-device — start()
// must refuse (returning false, staying idle) when the engine isn't
// running. The live-tap write path is exercised on device (there's no
// running AVAudioEngine in the unit environment).

import XCTest
import AVFoundation
@testable import ToneForgeMobile

@MainActor
final class OutputRecorderTests: XCTestCase {

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
                    from: src.baseAddress!, count: frames)
            }
        }
        return buffer
    }

    // MARK: - Peak meter

    func testPeakIsMaxAbsAcrossBothChannels() {
        // Unlike the mic recorder (channel 0 only), the output tap must
        // report the louder side — here channel 1's 0.9.
        let buffer = makeBuffer(channels: [[0.1, -0.2, 0.3], [0.4, -0.9, 0.1]])
        XCTAssertEqual(OutputRecorder.peak(of: buffer), 0.9, accuracy: 1e-6)
    }

    func testPeakClampsAboveUnity() {
        let buffer = makeBuffer(channels: [[1.8, -0.2], [0.3, -2.4]])
        XCTAssertEqual(OutputRecorder.peak(of: buffer), 1.0)
    }

    func testPeakOfEmptyBufferIsZero() {
        let format = AVAudioFormat(
            standardFormatWithSampleRate: 48_000, channels: 2)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 8)!
        buffer.frameLength = 0
        XCTAssertEqual(OutputRecorder.peak(of: buffer), 0)
    }

    // MARK: - AAC settings

    func testAacSettingsMirrorTapFormat() {
        let format = AVAudioFormat(
            standardFormatWithSampleRate: 48_000, channels: 2)!
        let settings = OutputRecorder.aacSettings(for: format)
        // Rate + channels come from the tap so the file's processing
        // format matches the buffers — mismatched writes throw.
        XCTAssertEqual(settings[AVSampleRateKey] as? Double, 48_000)
        XCTAssertEqual(settings[AVNumberOfChannelsKey] as? AVAudioChannelCount, 2)
        XCTAssertEqual(settings[AVFormatIDKey] as? AudioFormatID,
                       kAudioFormatMPEG4AAC)
    }

    // MARK: - State transition (no live engine)

    func testStartRefusesWhenEngineNotRunning() {
        // A freshly-built engine is idle (isRunning == false); there's
        // nothing to capture, so start() is a no-op that reports failure
        // and leaves the recorder idle.
        let recorder = OutputRecorder(engine: AVAudioEngine(), tapNode: { nil })
        XCTAssertFalse(recorder.start())
        XCTAssertEqual(recorder.state, .idle)
    }

    func testStopWhenIdleReturnsNil() {
        let recorder = OutputRecorder(engine: AVAudioEngine(), tapNode: { nil })
        XCTAssertNil(recorder.stop())
        XCTAssertEqual(recorder.state, .idle)
    }
}
