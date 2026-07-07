// DeterministicReverb.swift
//
// Small pure-Swift feedback-delay-network reverb built for the P6
// offline bounce, where the output must be BIT-IDENTICAL across
// repeat renders of the same session. AVAudioUnitReverb measurably
// is not (see SessionBounceRenderer header): the same offline graph
// fed the same input produces different sample bytes run-to-run, so
// the bounce needs a reverb whose every state transition is plain,
// ordered Float arithmetic with no RNG and no hidden AU state.
//
// Structure (all constants fixed at init, nothing random):
//
//   in L+R ──0.5──> 2 series Schroeder allpasses (input diffusion)
//                       │
//                       v
//              4-line FDN, mutually-prime delay lengths,
//              4×4 Hadamard/2 feedback matrix (orthonormal),
//              one-pole lowpass damping per line,
//              per-line decay gain g_i = 10^(−3·L_i / (T60·fs))
//                       │
//        out L = 0.6·(y0 + y2),  out R = 0.6·(y1 + y3)
//
// The decay-gain formula makes the loop lose 60 dB in `reverbSeconds`
// (the classic Jot T60 relation); the damping lowpass shortens the
// high-frequency tail a little further, which reads as "natural"
// rather than metallic. Delay lengths are primes near 30–43 ms at
// 48 kHz and scale linearly with the sample rate so the character is
// rate-independent.
//
// Output is 100 % wet — the caller owns dry/wet mixing (mirrors the
// live graph where the AU runs at wetDryMix = 100 behind a wet
// mixer). Silence in produces exact silence out (every path is a
// multiply-accumulate of zeros).
//
// Determinism contract: same (sampleRate, reverbSeconds, input) →
// same output bytes, on any run, forever. Enforced by
// DeterministicReverbTests.

import Foundation

public final class DeterministicReverb {

    // MARK: - Fixed design constants (at 48 kHz; scaled by rate)

    /// FDN delay lengths in samples at 48 kHz. Mutually prime so the
    /// lines' echo patterns never phase-lock into a flutter.
    private static let baseDelaySamples = [1433, 1601, 1867, 2053]

    /// Input-diffusion allpass lengths at 48 kHz (primes) and the
    /// shared allpass coefficient.
    private static let baseAllpassSamples = [421, 977]
    private static let allpassG: Float = 0.5

    /// One-pole damping coefficient (lp += c·(x − lp)). Higher = less
    /// HF damping. 0.55 keeps tails warm without sounding muffled.
    private static let dampingCoef: Float = 0.55

    /// Output tap gain (two lines summed per channel).
    private static let outputGain: Float = 0.6

    // MARK: - Instance state

    public let sampleRate: Double
    /// Broadband T60 of the tail, seconds. Clamped to ≥ 0.05 at init.
    public let reverbSeconds: Double

    // Allpass buffers + indices.
    private var apBuf: [[Float]]
    private var apIdx: [Int]

    // FDN delay buffers + write indices + per-line decay gain +
    // damping filter state.
    private var lineBuf: [[Float]]
    private var lineIdx: [Int]
    private let lineGain: [Float]
    private var damp: [Float]

    public init(sampleRate: Double = 48_000, reverbSeconds: Double = 2.0) {
        self.sampleRate = sampleRate
        let t60 = max(0.05, reverbSeconds)
        self.reverbSeconds = t60

        // Scale the 48 kHz reference lengths to the actual rate.
        let scale = sampleRate / 48_000
        func scaled(_ n: Int) -> Int { max(1, Int((Double(n) * scale).rounded())) }

        self.apBuf = Self.baseAllpassSamples.map {
            [Float](repeating: 0, count: scaled($0))
        }
        self.apIdx = [Int](repeating: 0, count: Self.baseAllpassSamples.count)

        let lengths = Self.baseDelaySamples.map(scaled)
        self.lineBuf = lengths.map { [Float](repeating: 0, count: $0) }
        self.lineIdx = [Int](repeating: 0, count: lengths.count)
        // Jot decay relation: each trip through line i loses exactly
        // enough level that 60 dB is gone after t60 seconds.
        self.lineGain = lengths.map {
            Float(pow(10.0, -3.0 * Double($0) / (t60 * sampleRate)))
        }
        self.damp = [Float](repeating: 0, count: lengths.count)
    }

    /// Zero all internal state (buffers, indices, damping filters).
    public func reset() {
        for i in apBuf.indices {
            for j in apBuf[i].indices { apBuf[i][j] = 0 }
            apIdx[i] = 0
        }
        for i in lineBuf.indices {
            for j in lineBuf[i].indices { lineBuf[i][j] = 0 }
            lineIdx[i] = 0
            damp[i] = 0
        }
    }

    // MARK: - Render

    /// Process `frames` samples, OVERWRITING the output pointers with
    /// 100 % wet signal. Input and output may not alias.
    public func render(
        inputLeft: UnsafePointer<Float>,
        inputRight: UnsafePointer<Float>,
        outputLeft: UnsafeMutablePointer<Float>,
        outputRight: UnsafeMutablePointer<Float>,
        frames: Int
    ) {
        guard frames > 0 else { return }

        let g = Self.allpassG
        let dampC = Self.dampingCoef
        let outG = Self.outputGain
        let g0 = lineGain[0], g1 = lineGain[1]
        let g2 = lineGain[2], g3 = lineGain[3]

        for i in 0..<frames {
            // Mono fold-down into the diffusion chain.
            var x = 0.5 * (inputLeft[i] + inputRight[i])

            // Two series Schroeder allpasses:
            //   y = d − g·x ; buf ← x + g·y
            for a in 0..<apBuf.count {
                let idx = apIdx[a]
                let d = apBuf[a][idx]
                let y = d - g * x
                apBuf[a][idx] = x + g * y
                apIdx[a] = idx + 1 == apBuf[a].count ? 0 : idx + 1
                x = y
            }

            // Read the four delay-line tails (write index = read
            // index for a full-length delay).
            let y0 = lineBuf[0][lineIdx[0]]
            let y1 = lineBuf[1][lineIdx[1]]
            let y2 = lineBuf[2][lineIdx[2]]
            let y3 = lineBuf[3][lineIdx[3]]

            // Stereo taps before the feedback update. The (0,2)/(1,3)
            // split decorrelates channels without extra delays.
            outputLeft[i] = outG * (y0 + y2)
            outputRight[i] = outG * (y1 + y3)

            // Hadamard/2 feedback mix (orthonormal — preserves loop
            // energy so decay is governed by lineGain alone).
            let f0 = 0.5 * (y0 + y1 + y2 + y3)
            let f1 = 0.5 * (y0 - y1 + y2 - y3)
            let f2 = 0.5 * (y0 + y1 - y2 - y3)
            let f3 = 0.5 * (y0 - y1 - y2 + y3)

            // Damping lowpass, decay gain, input injection, write.
            damp[0] += dampC * (f0 - damp[0])
            damp[1] += dampC * (f1 - damp[1])
            damp[2] += dampC * (f2 - damp[2])
            damp[3] += dampC * (f3 - damp[3])

            lineBuf[0][lineIdx[0]] = x + damp[0] * g0
            lineBuf[1][lineIdx[1]] = x + damp[1] * g1
            lineBuf[2][lineIdx[2]] = x + damp[2] * g2
            lineBuf[3][lineIdx[3]] = x + damp[3] * g3

            lineIdx[0] = lineIdx[0] + 1 == lineBuf[0].count ? 0 : lineIdx[0] + 1
            lineIdx[1] = lineIdx[1] + 1 == lineBuf[1].count ? 0 : lineIdx[1] + 1
            lineIdx[2] = lineIdx[2] + 1 == lineBuf[2].count ? 0 : lineIdx[2] + 1
            lineIdx[3] = lineIdx[3] + 1 == lineBuf[3].count ? 0 : lineIdx[3] + 1
        }
    }

    /// Array convenience: returns the wet signal for equal-length
    /// stereo input arrays. Test-friendly wrapper over `render`.
    public func process(left: [Float], right: [Float]) -> (left: [Float], right: [Float]) {
        precondition(left.count == right.count, "channel length mismatch")
        let n = left.count
        var outL = [Float](repeating: 0, count: n)
        var outR = [Float](repeating: 0, count: n)
        guard n > 0 else { return (outL, outR) }
        left.withUnsafeBufferPointer { lp in
            right.withUnsafeBufferPointer { rp in
                outL.withUnsafeMutableBufferPointer { olp in
                    outR.withUnsafeMutableBufferPointer { orp in
                        render(
                            inputLeft: lp.baseAddress!,
                            inputRight: rp.baseAddress!,
                            outputLeft: olp.baseAddress!,
                            outputRight: orp.baseAddress!,
                            frames: n
                        )
                    }
                }
            }
        }
        return (outL, outR)
    }
}
