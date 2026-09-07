// SeamlessLoopTests.swift
//
// Loop-seam crossfade + one-shot edge-fade DSP (the sample sound-quality
// pass). Pure buffer math, no engine — deterministic and CI-safe.

import XCTest
import AVFoundation
@testable import ToneForgeEngine

final class SeamlessLoopTests: XCTestCase {

    /// A mono buffer of `n` frames filled with a constant so edge ramps are
    /// obvious (a flat 1.0 signal fades visibly at the boundaries).
    private func flatBuffer(_ n: Int, value: Float = 1.0, sr: Double = 48_000)
        -> AVAudioPCMBuffer
    {
        let fmt = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 1)!
        let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(n))!
        buf.frameLength = AVAudioFrameCount(n)
        let d = buf.floatChannelData![0]
        for i in 0..<n { d[i] = value }
        return buf
    }

    // MARK: - Edge fades

    func testEdgeFadesRampStartAndTailToZero() {
        let n = 4_800  // 100 ms @ 48k
        let buf = flatBuffer(n)
        SeamlessLoop.applyEdgeFades(buf, attackMs: 3, releaseMs: 5)
        let d = buf.floatChannelData![0]

        // First and last sample are pulled to zero (kills the DC step click).
        XCTAssertEqual(d[0], 0, accuracy: 1e-6)
        XCTAssertEqual(d[n - 1], 0, accuracy: 1e-6)

        // Body is untouched (still full level well past the ramps).
        XCTAssertEqual(d[n / 2], 1.0, accuracy: 1e-6)

        // Ramps are monotonic up from the head and up toward the tail.
        let a = Int(0.003 * 48_000)   // 144 attack frames
        XCTAssertLessThan(d[1], d[a - 1])
        XCTAssertGreaterThan(d[a + 1], d[0])
    }

    func testEdgeFadesNoOpOnTinyBuffer() {
        let buf = flatBuffer(8)  // n <= 8 → guard returns early
        SeamlessLoop.applyEdgeFades(buf)
        let d = buf.floatChannelData![0]
        for i in 0..<8 { XCTAssertEqual(d[i], 1.0, accuracy: 1e-6) }
    }

    func testEdgeFadesRampsNeverOverlap() {
        // Buffer shorter than attack+release: ramps clamp so they don't
        // cross (no negative body, always a sample between them).
        let n = 100
        let buf = flatBuffer(n)
        SeamlessLoop.applyEdgeFades(buf, attackMs: 3, releaseMs: 5)  // asks 384 frames total
        let d = buf.floatChannelData![0]
        for i in 0..<n { XCTAssertGreaterThanOrEqual(d[i], 0) }
        // A middle sample survives at full level.
        XCTAssertEqual(d[n / 2], 1.0, accuracy: 1e-6)
    }

    // MARK: - Crossfade floor

    func testDefaultCrossfadeShortensAndBlends() {
        let n = 48_000  // 1 s
        let buf = flatBuffer(n)
        let out = SeamlessLoop.crossfaded(buf, crossfadeMs: SeamlessLoop.defaultLoopCrossfadeMs)
        // Output is shortened by the crossfade region (n - x), i.e. a real
        // seam was built rather than the raw buffer handed back.
        let x = Int(SeamlessLoop.defaultLoopCrossfadeMs / 1000.0 * 48_000)
        XCTAssertEqual(Int(out.frameLength), n - x)
    }

    func testDefaultCrossfadeMsIsPositive() {
        XCTAssertGreaterThan(SeamlessLoop.defaultLoopCrossfadeMs, 0)
    }

    // MARK: - Exact-length seam (loop-period fix)

    /// Mono sine with an integer `period` (frames). Loop lengths in these
    /// tests are whole multiples of the period, so the ideal grid-locked
    /// loop `s[t mod n]` is phase-perfect and any playback error is the
    /// seam's fault, not the fixture's.
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

    func testExactCrossfadedReturnsExactlyLoopFrames() {
        let n = 48_000
        // Continuation longer than the fade.
        let src = sineBuffer(frames: n + 512, period: 128)
        let out = SeamlessLoop.exactCrossfaded(src, loopFrames: n, crossfadeMs: 12)
        XCTAssertEqual(Int(out.frameLength), n)
        // Continuation SHORTER than the fade: the fade clamps to what's
        // available, the length must still be exact.
        let short = sineBuffer(frames: n + 40, period: 128)
        let out2 = SeamlessLoop.exactCrossfaded(short, loopFrames: n, crossfadeMs: 12)
        XCTAssertEqual(Int(out2.frameLength), n)
    }

    /// The heart of the fix. A hard `.loops` schedule repeats the buffer
    /// with period == frameLength, so the seam bake must not change the
    /// length. The old `crossfaded()` returned n − x frames; its wrap pair
    /// is locally smooth (the trimmed tail flows into the baked head), but
    /// every wrap SKIPS x frames of source phase — the loop runs 8–30 ms
    /// short of the bar grid and drifts. Measure both properties:
    ///  (1) sample-to-sample continuity across the new API's wrap, and
    ///  (2) grid lock — pass 2 of the looped buffer matches the ideal
    ///      `s[t mod n]` the loop-lock grid expects, where the old API's
    ///      error is ~2.0 (half a period of phase skip, by construction).
    func testExactCrossfadedSeamIsContinuousAndGridLocked() {
        let n = 48_000, p = 128           // 375 periods per loop
        let x = 192                        // 4 ms @ 48k = 1.5 periods —
                                           // maximally misaligned with p
        let src = sineBuffer(frames: n + 512, period: p)
        let s = src.floatChannelData![0]
        let out = SeamlessLoop.exactCrossfaded(src, loopFrames: n, crossfadeMs: 4)
        XCTAssertEqual(Int(out.frameLength), n)
        let d = out.floatChannelData![0]

        // (1) Wrap continuity: last frame → first frame steps by no more
        // than a normal sine step (the head was rebuilt from continuation
        // audio that genuinely follows the tail in the source).
        let maxSineStep = Float(2 * Double.pi / Double(p))
        XCTAssertLessThanOrEqual(abs(d[0] - d[n - 1]), maxSineStep * 1.5)

        // (2) Grid lock, new API: pass-2 playback (frames n + j) is
        // out[j]; the grid expects s[(n + j) mod n] = s[j]. Past the
        // blend region the body is copied verbatim, so the error is ~0.
        var errNew: Float = 0
        for j in x..<(x + p) {
            errNew = max(errNew, abs(d[j] - s[j]))
        }

        // Old trimmed API on the same (misaligned) cut: the loop period
        // becomes n − x, so pass-2 frame n + j plays old[(n + j) % (n − x)]
        // = s[j + x] — 1.5 periods of skipped phase ⇒ error ≈ 2·amplitude.
        let region = sineBuffer(frames: n, period: p)
        let old = SeamlessLoop.crossfaded(region, crossfadeMs: 4)
        let oldLen = Int(old.frameLength)
        XCTAssertEqual(oldLen, n - x)  // documents the trimming defect
        let o = old.floatChannelData![0]
        var errOld: Float = 0
        for j in x..<(x + p) {
            let ideal = s[j]
            let actual = o[(n + j) % oldLen]
            errOld = max(errOld, abs(actual - ideal))
        }

        XCTAssertLessThan(errNew, 0.02)
        XCTAssertGreaterThan(errOld, 0.5)
        XCTAssertLessThan(errNew, errOld)
    }

    func testExactCrossfadedFallbackWithoutContinuation() {
        // Region ends at the source's end — no continuation frames. The
        // length must still be exact; the seam falls back to equal-power
        // edge ramps so the wrap meets near zero (dip, not click).
        let n = 9_600
        let src = flatBuffer(n)
        let out = SeamlessLoop.exactCrossfaded(src, loopFrames: n, crossfadeMs: 12)
        XCTAssertEqual(Int(out.frameLength), n)
        let d = out.floatChannelData![0]
        XCTAssertEqual(d[0], 0, accuracy: 1e-5)       // head fades in from 0
        XCTAssertLessThan(abs(d[n - 1]), 0.02)        // tail fades out to ~0
        XCTAssertEqual(d[n / 2], 1.0, accuracy: 1e-6) // body untouched
    }
}
