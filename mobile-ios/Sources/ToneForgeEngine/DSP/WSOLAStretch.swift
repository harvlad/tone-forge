// WSOLAStretch.swift
//
// Waveform-similarity overlap-add (WSOLA, Verhelst & Roelands) time
// stretcher: changes duration without changing pitch. Pure offline
// DSP — no AVFoundation, deterministic (no randomness), and
// allocation-conscious (all scratch sized once up front).
//
// Structure:
//   • 30 ms Hann analysis frames, synthesis hop = frame/2 (50%
//     overlap), analysis hop = synthesisHop / factor.
//   • For each output frame the nominal analysis position is refined
//     by a ±7.5 ms similarity search: the candidate segment that best
//     matches the "natural continuation" of the previously chosen
//     segment (previous start + synthesis hop) wins. Similarity is
//     normalised cross-correlation — the dot product (vDSP_dotpr)
//     divided by the candidate's RMS so high-energy segments can't
//     buy alignment they haven't earned. Candidate energies come from
//     a Double prefix-sum of squares (O(1) per candidate, no drift).
//   • Frames are Hann-windowed and overlap-added; the accumulated
//     window sum is divided out at the end, so the interior is an
//     exact partition-of-unity reconstruction and the frame edges
//     don't ring.
//
// At factor 1.0 the natural continuation coincides with the nominal
// candidate, the search returns offset 0 for every frame, and the
// pipeline reduces to windowed identity reconstruction — transparent
// to within float rounding.

import Foundation
import Accelerate

public enum WSOLAStretch {

    // MARK: - Tunables

    /// Analysis/synthesis frame length in seconds (30 ms).
    static let frameSec = 0.030
    /// Similarity search radius in seconds (±7.5 ms).
    static let searchSec = 0.0075
    /// Duration factor is clamped to this range.
    static let factorRange = 0.25...4.0

    // MARK: - Public API

    /// Time-stretch `input` to `factor` × its duration at constant
    /// pitch. `factor` is clamped to 0.25…4.0; the output length is
    /// exactly `round(input.count × factor)` samples. Deterministic:
    /// identical inputs always produce bit-identical output.
    public static func stretch(_ input: [Float], factor: Double, sampleRate: Double) -> [Float] {
        let factor = min(max(factor, factorRange.lowerBound), factorRange.upperBound)
        guard sampleRate > 0, !input.isEmpty else { return [] }
        let outLen = Int((Double(input.count) * factor).rounded())
        guard outLen > 0 else { return [] }

        var frameLen = max(4, Int((frameSec * sampleRate).rounded()))
        frameLen &= ~1  // even, so synthesis hop is exact
        let synHop = frameLen / 2
        let anaHop = Double(synHop) / factor
        let radius = max(0, Int((searchSec * sampleRate).rounded()))

        // Inputs shorter than one frame can't support the frame
        // machinery; degrade to a copy padded/truncated to length.
        guard input.count >= frameLen else {
            var out = [Float](repeating: 0, count: outLen)
            for i in 0..<min(outLen, input.count) { out[i] = input[i] }
            return out
        }

        let window = hannWindow(frameLen)
        let maxStart = input.count - frameLen

        // Double prefix-sum of squares: energy(s…s+frameLen) in O(1).
        var prefixSq = [Double](repeating: 0, count: input.count + 1)
        for i in 0..<input.count {
            let v = Double(input[i])
            prefixSq[i + 1] = prefixSq[i] + v * v
        }

        var out = [Float](repeating: 0, count: outLen + frameLen)
        var windowSum = [Float](repeating: 0, count: outLen + frameLen)
        var windowed = [Float](repeating: 0, count: frameLen)

        var prevStart = -1
        var frameIndex = 0

        input.withUnsafeBufferPointer { inBuf in
            let x = inBuf.baseAddress!
            while frameIndex * synHop < outLen {
                let synPos = frameIndex * synHop
                let nominal = min(max(Int((Double(frameIndex) * anaHop).rounded()), 0), maxStart)

                let start: Int
                if prevStart < 0 {
                    start = nominal
                } else {
                    let templateStart = min(prevStart + synHop, maxStart)
                    start = bestCandidate(
                        x: x,
                        templateStart: templateStart,
                        nominal: nominal,
                        radius: radius,
                        frameLen: frameLen,
                        maxStart: maxStart,
                        prefixSq: prefixSq
                    )
                }

                // Windowed overlap-add of the chosen segment.
                window.withUnsafeBufferPointer { w in
                    windowed.withUnsafeMutableBufferPointer { tmp in
                        vDSP_vmul(x + start, 1, w.baseAddress!, 1,
                                  tmp.baseAddress!, 1, vDSP_Length(frameLen))
                        out.withUnsafeMutableBufferPointer { o in
                            vDSP_vadd(o.baseAddress! + synPos, 1, tmp.baseAddress!, 1,
                                      o.baseAddress! + synPos, 1, vDSP_Length(frameLen))
                        }
                        windowSum.withUnsafeMutableBufferPointer { ws in
                            vDSP_vadd(ws.baseAddress! + synPos, 1, w.baseAddress!, 1,
                                      ws.baseAddress! + synPos, 1, vDSP_Length(frameLen))
                        }
                    }
                }

                prevStart = start
                frameIndex += 1
            }
        }

        // Divide out the accumulated window; leave true zero-weight
        // edge samples (only the very first, where Hann is 0) silent.
        let eps: Float = 1e-6
        for i in 0..<outLen where windowSum[i] > eps {
            out[i] /= windowSum[i]
        }
        out.removeLast(out.count - outLen)
        return out
    }

    // MARK: - Similarity search

    /// Candidate start in [nominal − radius, nominal + radius]
    /// (clamped) maximising normalised cross-correlation with the
    /// natural-continuation template.
    private static func bestCandidate(
        x: UnsafePointer<Float>,
        templateStart: Int,
        nominal: Int,
        radius: Int,
        frameLen: Int,
        maxStart: Int,
        prefixSq: [Double]
    ) -> Int {
        let lo = max(0, nominal - radius)
        let hi = min(maxStart, nominal + radius)
        guard lo < hi else { return min(max(nominal, 0), maxStart) }

        var best = nominal
        var bestScore = -Double.infinity
        for s in lo...hi {
            var dot: Float = 0
            vDSP_dotpr(x + templateStart, 1, x + s, 1, &dot, vDSP_Length(frameLen))
            let energy = prefixSq[s + frameLen] - prefixSq[s]
            let score = Double(dot) / max(energy.squareRoot(), 1e-12)
            if score > bestScore {
                bestScore = score
                best = s
            }
        }
        return best
    }

    // MARK: - Window

    /// Periodic Hann (0.5 − 0.5·cos(2πn/N)) — sums to exactly 1 at
    /// 50% overlap, so the interior reconstruction is a partition of
    /// unity even before the window-sum division.
    static func hannWindow(_ n: Int) -> [Float] {
        var w = [Float](repeating: 0, count: n)
        vDSP_hann_window(&w, vDSP_Length(n), Int32(vDSP_HANN_DENORM))
        return w
    }
}
