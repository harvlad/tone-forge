// DeterministicReverbTests.swift
//
// Contract tests for the FDN reverb that guards the P6 bounce
// bit-identity gate: exact determinism across fresh instances, a
// tail whose length tracks reverbSeconds, and exact silence for
// silent input.

import XCTest
@testable import ToneForgeEngine

final class DeterministicReverbTests: XCTestCase {

    private let sr = 48_000.0

    /// Impulse + short sine burst — enough spectral content to
    /// exercise every delay line and the damping filters.
    private func burst(frames: Int) -> [Float] {
        var x = [Float](repeating: 0, count: frames)
        x[0] = 1.0
        for i in 1..<min(frames, 2_400) {
            x[i] = 0.25 * sinf(2 * .pi * 440 * Float(i) / Float(sr))
        }
        return x
    }

    private func rms(_ x: ArraySlice<Float>) -> Float {
        guard !x.isEmpty else { return 0 }
        var acc: Float = 0
        for v in x { acc += v * v }
        return sqrt(acc / Float(x.count))
    }

    // MARK: - Determinism

    func testDeterministicAcrossFreshInstances() {
        let n = 96_000
        var input = burst(frames: 4_800)
        input.append(contentsOf: [Float](repeating: 0, count: n - input.count))

        let a = DeterministicReverb(sampleRate: sr, reverbSeconds: 2.0)
            .process(left: input, right: input)
        let b = DeterministicReverb(sampleRate: sr, reverbSeconds: 2.0)
            .process(left: input, right: input)

        // Bit-exact, not approximate: memcmp-style equality.
        XCTAssertEqual(a.left, b.left)
        XCTAssertEqual(a.right, b.right)
    }

    // MARK: - Tail behaviour

    func testImpulseResponseHasTailNearReverbSeconds() {
        let t60 = 1.0
        let n = Int(sr * t60 * 1.4)
        var impulse = [Float](repeating: 0, count: n)
        impulse[0] = 1.0

        let reverb = DeterministicReverb(sampleRate: sr, reverbSeconds: t60)
        let out = reverb.process(left: impulse, right: impulse)

        // Early tail reference (skip the pre-delay of the longest
        // line — first echoes land within ~45 ms).
        let earlyStart = Int(0.05 * sr)
        let early = rms(out.left[earlyStart..<(earlyStart + 4_800)])
        XCTAssertGreaterThan(early, 1e-4, "no early reflections")

        // Mid-tail (~0.5·T60) must still be audibly ringing.
        let midStart = Int(0.5 * t60 * sr)
        let mid = rms(out.left[midStart..<(midStart + 4_800)])
        XCTAssertGreaterThan(mid, 1e-5, "tail died before 0.5·T60")

        // Past T60 the tail must have decayed far below the early
        // level (−60 dB target; assert a loose −40 dB).
        let lateStart = Int(1.2 * t60 * sr)
        let late = rms(out.left[lateStart..<n])
        XCTAssertLessThan(late, early * 0.01, "tail did not decay")
    }

    func testLongerSecondsRingLonger() {
        let n = Int(sr * 1.2)
        var impulse = [Float](repeating: 0, count: n)
        impulse[0] = 1.0

        let short = DeterministicReverb(sampleRate: sr, reverbSeconds: 0.5)
            .process(left: impulse, right: impulse)
        let long = DeterministicReverb(sampleRate: sr, reverbSeconds: 2.5)
            .process(left: impulse, right: impulse)

        // Energy near the 1-second mark: the 2.5 s tail must carry
        // clearly more than the 0.5 s tail.
        let window = Int(0.9 * sr)..<Int(1.1 * sr)
        XCTAssertGreaterThan(rms(long.left[window]), rms(short.left[window]) * 3)
    }

    // MARK: - Silence

    func testSilenceInSilenceOut() {
        let zeros = [Float](repeating: 0, count: 24_000)
        let reverb = DeterministicReverb(sampleRate: sr, reverbSeconds: 2.0)
        let out = reverb.process(left: zeros, right: zeros)
        XCTAssertEqual(out.left, zeros)
        XCTAssertEqual(out.right, zeros)
    }
}
