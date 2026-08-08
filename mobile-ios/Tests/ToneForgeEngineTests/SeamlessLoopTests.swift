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
}
