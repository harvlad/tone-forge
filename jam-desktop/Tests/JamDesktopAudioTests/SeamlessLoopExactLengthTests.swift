// SeamlessLoopExactLengthTests.swift
//
// Desktop-side guard for the exact-length loop seam ChopPlayer bakes via
// ToneForgeEngine.SeamlessLoop.exactCrossfaded. A hard `.loops` schedule
// repeats the buffer with period == frameLength, so the seam bake must
// never change the length: the old `crossfaded()` trimmed the fade off
// the buffer, making every held loop run 8–30 ms short of the bar-snapped
// region and drift against the loop-lock grid (the bug the jamn Kit
// plugin fixed with its runtime dual-read). Pure DSP — no engine.

import XCTest
import AVFoundation
import ToneForgeEngine

final class SeamlessLoopExactLengthTests: XCTestCase {

    /// Mono sine with an integer `period` (frames); loop lengths are whole
    /// multiples of the period so the ideal grid loop is phase-perfect.
    private func sineBuffer(frames: Int, period: Int, sr: Double = 48_000)
        -> AVAudioPCMBuffer
    {
        let fmt = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 1)!
        let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(frames))!
        buf.frameLength = AVAudioFrameCount(frames)
        let d = buf.floatChannelData![0]
        for i in 0..<frames {
            d[i] = Float(sin(2 * Double.pi * Double(i) / Double(period)))
        }
        return buf
    }

    func testExactCrossfadedPreservesBarSnappedLength() {
        let n = 48_000
        let src = sineBuffer(frames: n + 512, period: 128)
        let out = SeamlessLoop.exactCrossfaded(src, loopFrames: n, crossfadeMs: 15)
        // Loop period == bar-snapped region, to the frame — no trim.
        XCTAssertEqual(Int(out.frameLength), n)
    }

    func testWrapIsContinuousWithContinuationAudio() {
        let n = 48_000, p = 128
        let src = sineBuffer(frames: n + 512, period: p)
        let out = SeamlessLoop.exactCrossfaded(src, loopFrames: n, crossfadeMs: 15)
        let d = out.floatChannelData![0]
        // Tail → head wrap steps by no more than a normal sine step: the
        // head was rebuilt from continuation audio that genuinely follows
        // the tail in the source.
        let maxSineStep = Float(2 * Double.pi / Double(p))
        XCTAssertLessThanOrEqual(abs(d[0] - d[n - 1]), maxSineStep * 1.5)
        // Body past the blend is untouched — steady state == original.
        let s = src.floatChannelData![0]
        let x = Int(15.0 / 1000.0 * 48_000)
        for j in [x, n / 2, n - 1] {
            XCTAssertEqual(d[j], s[j], accuracy: 1e-6)
        }
    }

    func testFallbackWithoutContinuationKeepsLength() {
        // Chop flush with the stem file's end: no continuation to read.
        // Exact length still holds; the seam falls back to edge ramps.
        let n = 9_600
        let src = sineBuffer(frames: n, period: 128)
        let out = SeamlessLoop.exactCrossfaded(src, loopFrames: n, crossfadeMs: 15)
        XCTAssertEqual(Int(out.frameLength), n)
    }
}
