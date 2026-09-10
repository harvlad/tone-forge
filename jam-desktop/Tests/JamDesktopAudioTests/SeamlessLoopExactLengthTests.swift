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

    // MARK: - Shared-cycle tiling (unison lock, web c726ba58)

    func testTileToLengthExactMultipleIsCleanRepeat() {
        // Body divides the cycle evenly (2×): every frame equals the body
        // sample at `i % srcFrames`, so both halves are bit-identical and the
        // wrap at the body boundary is continuous (no extra seam).
        let body = 12_000
        let src = sineBuffer(frames: body, period: 100)
        let out = SeamlessLoop.tileToLength(src, targetFrames: body * 2)
        XCTAssertEqual(Int(out.frameLength), body * 2)
        let s = src.floatChannelData![0]
        let d = out.floatChannelData![0]
        for i in [0, 1, body - 1, body, body + 1, body * 2 - 1] {
            XCTAssertEqual(d[i], s[i % body], accuracy: 1e-6)
        }
    }

    func testTileToLengthNonMultipleGivesRightLength() {
        // Body does NOT divide the cycle: length is still exactly the target
        // (guaranteed phase-lock) and content is the modulo-tiled body — the
        // one accepted wrap seam lands at the final cycle boundary.
        let body = 10_000
        let target = 25_123   // not a whole multiple of body
        let src = sineBuffer(frames: body, period: 128)
        let out = SeamlessLoop.tileToLength(src, targetFrames: target)
        XCTAssertEqual(Int(out.frameLength), target)
        let s = src.floatChannelData![0]
        let d = out.floatChannelData![0]
        for i in [body - 1, body, 2 * body - 1, 2 * body, target - 1] {
            XCTAssertEqual(d[i], s[i % body], accuracy: 1e-6)
        }
    }

    func testTileToLengthDegenerateArgsReturnUnchanged() {
        // Target <= body (longest pad already fills the cycle) is a no-op:
        // the same buffer comes back untouched, so it keeps its own period.
        let body = 8_000
        let src = sineBuffer(frames: body, period: 64)
        XCTAssertEqual(Int(SeamlessLoop.tileToLength(src, targetFrames: body).frameLength), body)
        XCTAssertEqual(Int(SeamlessLoop.tileToLength(src, targetFrames: body - 1).frameLength), body)
        XCTAssertEqual(Int(SeamlessLoop.tileToLength(src, targetFrames: 0).frameLength), body)
    }
}
