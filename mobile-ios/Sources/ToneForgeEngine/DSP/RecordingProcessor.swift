// RecordingProcessor.swift
//
// One-shot conditioning for raw mic captures (P3): turns "phone on a
// table, hit record, clap twice" into a pad-ready sample. Pure and
// deterministic — same input, same output — so processed samples can
// be regenerated and bounces stay reproducible.
//
// Pipeline (order matters):
//   1. 20 Hz one-pole DC block — mic capsules and cheap ADCs drift;
//      DC would bias every RMS window and the normalise stage.
//   2. RMS silence trim — 10 ms windows; threshold adapts to the
//      recording: max(p95 × 0.08, p20 of non-zero windows), i.e.
//      "well below the loud stuff, but above the room tone".
//   3. Zero-cross alignment — snap each trim boundary to the nearest
//      sign change within ±5 ms so the sample never starts or ends
//      mid-swing (clicks on trigger).
//   4. Peak normalise to −1 dBFS — pads should sit at a predictable
//      level under the chop-bus gain.
//   5. Spectral-flux transient detection (1024/256 STFT, Hann,
//      median-based threshold) — offsets feed the classifier's
//      envelope features and future slice-to-pads UI.

import Foundation
import Accelerate

public enum RecordingProcessor {

    public struct Output: Equatable, Sendable {
        /// Conditioned audio (trimmed, DC-blocked, normalised).
        public let samples: [Float]
        /// Transient onsets as sample offsets INTO `samples`,
        /// ascending. Always includes at least the strongest onset
        /// for non-empty output when any flux frame clears threshold.
        public let transientOffsets: [Int]
    }

    /// Tunables — fixed constants, listed once for the tests' benefit.
    static let dcBlockHz: Double = 20
    static let rmsWindowSec: Double = 0.010
    static let zeroCrossSearchSec: Double = 0.005
    static let normalizePeak: Float = 0.8912509  // −1 dBFS
    static let fftSize = 1024
    static let hopSize = 256
    /// A flux frame must exceed median × this to count as an onset.
    static let fluxMedianFactor: Float = 1.6
    /// …AND this fraction of the strongest flux frame (default).
    /// Without the peak-relative floor, steady material (jitter flux
    /// ≈ 1% of its attack peak) turns every jitter bump into an
    /// "onset". Beat capture overrides this down to
    /// `BeatOnsetExtractor.fluxPeakFraction` — a military-drum ghost
    /// note also sits at ~1–2% of the accent's flux (25:1 observed
    /// live), and the extractor's percussive gate cleans up anything
    /// spurious; the sample-classification path has no such gate, so
    /// it keeps the stricter default.
    static let fluxPeakFraction: Float = 0.06
    /// Minimum spacing between reported onsets.
    static let minOnsetSpacingSec: Double = 0.050

    /// Condition a raw mono capture. Returns empty output when the
    /// input is empty or contains no signal above the trim threshold.
    public static func process(_ input: [Float], sampleRate: Double) -> Output {
        guard !input.isEmpty, sampleRate > 0 else {
            return Output(samples: [], transientOffsets: [])
        }

        let dcBlocked = dcBlock(input, sampleRate: sampleRate)

        guard var (start, end) = trimBounds(dcBlocked, sampleRate: sampleRate) else {
            return Output(samples: [], transientOffsets: [])
        }
        start = nearestZeroCross(in: dcBlocked, around: start, sampleRate: sampleRate)
        end = nearestZeroCross(in: dcBlocked, around: end, sampleRate: sampleRate)
        guard end > start else { return Output(samples: [], transientOffsets: []) }

        var samples = Array(dcBlocked[start..<end])
        normalize(&samples)

        let onsets = transients(samples, sampleRate: sampleRate)
        return Output(samples: samples, transientOffsets: onsets)
    }

    // MARK: - Stage 1: DC block

    /// One-pole high-pass: y[n] = x[n] − x[n−1] + R·y[n−1] with R set
    /// for a ~20 Hz corner. Removes capsule drift without touching
    /// audible lows (R ≈ 0.9974 at 48 k).
    static func dcBlock(_ x: [Float], sampleRate: Double) -> [Float] {
        let r = Float(1 - 2 * Double.pi * dcBlockHz / sampleRate)
        var out = [Float](repeating: 0, count: x.count)
        var prevX: Float = 0
        var prevY: Float = 0
        for i in 0..<x.count {
            let y = x[i] - prevX + r * prevY
            out[i] = y
            prevX = x[i]
            prevY = y
        }
        return out
    }

    // MARK: - Stage 2: RMS trim

    /// First/last sample (window-granular) that clears the adaptive
    /// RMS threshold; nil when nothing does (pure silence/room tone).
    static func trimBounds(_ x: [Float], sampleRate: Double) -> (Int, Int)? {
        let win = max(1, Int(rmsWindowSec * sampleRate))
        var rms: [Float] = []
        rms.reserveCapacity(x.count / win + 1)
        var i = 0
        while i < x.count {
            let n = min(win, x.count - i)
            var value: Float = 0
            x.withUnsafeBufferPointer { buf in
                vDSP_rmsqv(buf.baseAddress! + i, 1, &value, vDSP_Length(n))
            }
            rms.append(value)
            i += n
        }

        let nonZero = rms.filter { $0 > 1e-6 }.sorted()
        guard !nonZero.isEmpty else { return nil }
        let p95 = nonZero[min(nonZero.count - 1, Int(Double(nonZero.count) * 0.95))]
        let p20 = nonZero[min(nonZero.count - 1, Int(Double(nonZero.count) * 0.20))]
        let threshold = max(p95 * 0.08, p20)

        guard let firstWin = rms.firstIndex(where: { $0 >= threshold }),
              let lastWin = rms.lastIndex(where: { $0 >= threshold })
        else { return nil }

        let start = firstWin * win
        let end = min(x.count, (lastWin + 1) * win)
        return start < end ? (start, end) : nil
    }

    // MARK: - Stage 3: zero-cross alignment

    /// Nearest sign change to `index` within ±5 ms; `index` itself
    /// when none is found (heavily asymmetric signals).
    static func nearestZeroCross(in x: [Float], around index: Int, sampleRate: Double) -> Int {
        let radius = max(1, Int(zeroCrossSearchSec * sampleRate))
        let lo = max(1, index - radius)
        let hi = min(x.count - 1, index + radius)
        guard lo <= hi else { return index }

        var best: Int?
        for i in lo...hi where (x[i - 1] < 0) != (x[i] < 0) || x[i] == 0 {
            if best == nil || abs(i - index) < abs(best! - index) { best = i }
        }
        return best ?? min(max(index, 0), x.count)
    }

    // MARK: - Stage 4: normalise

    /// Scale so |peak| = −1 dBFS. Skipped for near-silence (no point
    /// amplifying room tone by 60 dB).
    static func normalize(_ x: inout [Float]) {
        var peak: Float = 0
        vDSP_maxmgv(x, 1, &peak, vDSP_Length(x.count))
        guard peak > 1e-4 else { return }
        var scale = normalizePeak / peak
        vDSP_vsmul(x, 1, &scale, &x, 1, vDSP_Length(x.count))
    }

    // MARK: - Stage 5: spectral-flux transients

    /// Onset offsets (samples) via positive spectral flux on a
    /// 1024/256 Hann STFT. A frame is an onset when its flux exceeds
    /// median × 2 AND is a local maximum; onsets closer than 50 ms
    /// to a stronger one are suppressed.
    static func transients(
        _ x: [Float],
        sampleRate: Double,
        peakFraction: Float = fluxPeakFraction
    ) -> [Int] {
        guard x.count >= fftSize else {
            // Too short for STFT — a trimmed capture starts at its
            // own attack by construction.
            return x.isEmpty ? [] : [0]
        }

        guard let setup = vDSP_DFT_zop_CreateSetup(
            nil, vDSP_Length(fftSize), .FORWARD
        ) else { return [0] }
        defer { vDSP_DFT_DestroySetup(setup) }

        var window = [Float](repeating: 0, count: fftSize)
        vDSP_hann_window(&window, vDSP_Length(fftSize), Int32(vDSP_HANN_NORM))

        let bins = fftSize / 2
        let frameCount = (x.count - fftSize) / hopSize + 1
        var prevMag = [Float](repeating: 0, count: bins)
        var flux = [Float](repeating: 0, count: frameCount)

        var frame = [Float](repeating: 0, count: fftSize)
        let zeros = [Float](repeating: 0, count: fftSize)
        var outReal = [Float](repeating: 0, count: fftSize)
        var outImag = [Float](repeating: 0, count: fftSize)
        var mag = [Float](repeating: 0, count: bins)

        for k in 0..<frameCount {
            let offset = k * hopSize
            x.withUnsafeBufferPointer { buf in
                vDSP_vmul(
                    buf.baseAddress! + offset, 1, window, 1,
                    &frame, 1, vDSP_Length(fftSize)
                )
            }
            vDSP_DFT_Execute(setup, frame, zeros, &outReal, &outImag)
            for b in 0..<bins {
                mag[b] = sqrt(outReal[b] * outReal[b] + outImag[b] * outImag[b])
            }
            var f: Float = 0
            for b in 0..<bins {
                f += max(0, mag[b] - prevMag[b])
            }
            flux[k] = f
            swap(&prevMag, &mag)
        }

        let sortedFlux = flux.sorted()
        let median = sortedFlux[sortedFlux.count / 2]
        var maxFlux: Float = 0
        vDSP_maxv(flux, 1, &maxFlux, vDSP_Length(frameCount))
        let threshold = max(median * fluxMedianFactor, maxFlux * peakFraction, 1e-6)

        // Local maxima above threshold, strongest-first suppression.
        var candidates: [(frame: Int, flux: Float)] = []
        for k in 0..<frameCount where flux[k] >= threshold {
            let prev = k > 0 ? flux[k - 1] : 0
            let next = k + 1 < frameCount ? flux[k + 1] : 0
            if flux[k] >= prev && flux[k] >= next {
                candidates.append((k, flux[k]))
            }
        }
        let minSpacing = Int(minOnsetSpacingSec * sampleRate)
        var accepted: [Int] = []
        for c in candidates.sorted(by: { $0.flux > $1.flux }) {
            let offset = c.frame * hopSize
            if accepted.allSatisfy({ abs($0 - offset) >= minSpacing }) {
                accepted.append(offset)
            }
        }
        return accepted.sorted()
    }
}
