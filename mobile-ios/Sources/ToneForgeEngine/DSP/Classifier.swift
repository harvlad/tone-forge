// Classifier.swift
//
// Assigns a SampleClass to a conditioned mic capture so the grid can
// badge pads and pick sensible defaults (one-shot vs loop hint).
//
// D-018: heuristic only for now. The seam is the SampleClassifying
// protocol — everything downstream (recording flow, pad sheet, store)
// talks to the protocol, so a trained model drops in without touching
// callers.
// FUTURE: CoreMLClassifier adopting SampleClassifying, loading a
// compiled .mlmodelc trained per docs/classifier-training.md.
//
// The heuristic: hand-rolled features (26 mel-band energies →
// spectral centroid; spectral flux variability; zero-crossing rate;
// envelope attack/sustain; autocorrelation pitchedness; duration;
// onset count) feeding a small decision tree. Confidences are
// calibrated by hand against the synthetic suite + bench recordings —
// they express "how far inside the decision region", not probability.

import Foundation
import Accelerate

/// The classifier seam (D-018). Implementations must be pure and
/// deterministic; `confidence` is 0…1.
public protocol SampleClassifying: Sendable {
    func classify(samples: [Float], sampleRate: Double) -> (SampleClass, Double)
}

public struct HeuristicClassifier: SampleClassifying {

    public init() {}

    public func classify(samples: [Float], sampleRate: Double) -> (SampleClass, Double) {
        guard samples.count >= 1024, sampleRate > 0 else { return (.unknown, 0) }
        let f = Self.features(samples, sampleRate: sampleRate)
        return Self.decide(f)
    }

    // MARK: - Features

    struct Features {
        var durationSec: Double
        /// Seconds from first sample to 90% of the envelope peak.
        var attackSec: Double
        /// Mean RMS of the final third over the peak RMS window —
        /// ≈1 for held material, ≈0 for decaying hits.
        var sustainRatio: Float
        /// Mean zero-crossing rate (crossings per sample, 0…1).
        var zcr: Float
        /// Strongest normalised autocorrelation peak in the 60 Hz–
        /// 1 kHz lag range — ≈1 strongly pitched, ≈0 noise.
        var pitchedness: Float
        /// Mel-weighted spectral centroid in Hz.
        var centroidHz: Float
        /// Spectral-flux coefficient of variation (std/mean) —
        /// speech and phrases move; drones and tones don't.
        var fluxVariability: Float
        /// RecordingProcessor onset count.
        var onsetCount: Int
    }

    static let melBandCount = 26
    private static let fftSize = 1024
    private static let hopSize = 256

    static func features(_ x: [Float], sampleRate: Double) -> Features {
        let durationSec = Double(x.count) / sampleRate

        // Envelope: 10 ms RMS windows.
        let win = max(1, Int(0.010 * sampleRate))
        var rms: [Float] = []
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
        var peakRMS: Float = 0
        vDSP_maxv(rms, 1, &peakRMS, vDSP_Length(rms.count))
        let peakIdx = rms.firstIndex(of: peakRMS) ?? 0

        var attackSec = 0.0
        if peakRMS > 0 {
            let attackIdx = rms.prefix(peakIdx + 1)
                .firstIndex(where: { $0 >= 0.9 * peakRMS }) ?? peakIdx
            attackSec = Double(attackIdx) * 0.010
        }

        let lastThird = rms.suffix(max(1, rms.count / 3))
        let sustainRatio = peakRMS > 0
            ? (lastThird.reduce(0, +) / Float(lastThird.count)) / peakRMS
            : 0

        // Zero-crossing rate.
        var crossings = 0
        for j in 1..<x.count where (x[j - 1] < 0) != (x[j] < 0) {
            crossings += 1
        }
        let zcr = Float(crossings) / Float(x.count)

        let pitchedness = Self.pitchedness(x, sampleRate: sampleRate)
        let (centroidHz, fluxVariability) = Self.spectralFeatures(x, sampleRate: sampleRate)
        let onsets = RecordingProcessor.transients(x, sampleRate: sampleRate)

        return Features(
            durationSec: durationSec,
            attackSec: attackSec,
            sustainRatio: sustainRatio,
            zcr: zcr,
            pitchedness: pitchedness,
            centroidHz: centroidHz,
            fluxVariability: fluxVariability,
            onsetCount: onsets.count
        )
    }

    /// Normalised autocorrelation peak over up to three 2048-sample
    /// frames spread across the capture (average — one voiced frame
    /// in a noisy capture shouldn't flip the verdict).
    static func pitchedness(_ x: [Float], sampleRate: Double) -> Float {
        let frame = 2048
        guard x.count >= frame else { return 0 }
        let minLag = max(2, Int(sampleRate / 1000))  // ≤1 kHz
        let maxLag = min(frame / 2, Int(sampleRate / 60))  // ≥60 Hz
        guard minLag < maxLag else { return 0 }

        let positions: [Int] = {
            if x.count >= frame * 3 {
                return [0, (x.count - frame) / 2, x.count - frame]
            }
            return [(x.count - frame) / 2]
        }()

        var total: Float = 0
        for pos in positions {
            let seg = Array(x[pos..<(pos + frame)])
            var energy: Float = 0
            vDSP_svesq(seg, 1, &energy, vDSP_Length(frame))
            guard energy > 1e-9 else { continue }
            var best: Float = 0
            for lag in minLag...maxLag {
                var r: Float = 0
                seg.withUnsafeBufferPointer { buf in
                    vDSP_dotpr(
                        buf.baseAddress!, 1, buf.baseAddress! + lag, 1,
                        &r, vDSP_Length(frame - lag)
                    )
                }
                best = max(best, r / energy)
            }
            total += best
        }
        return total / Float(positions.count)
    }

    /// (mel-weighted centroid Hz, flux coefficient-of-variation) from
    /// a 1024/256 Hann STFT with 26 triangular mel bands.
    static func spectralFeatures(_ x: [Float], sampleRate: Double) -> (Float, Float) {
        guard x.count >= fftSize,
              let setup = vDSP_DFT_zop_CreateSetup(nil, vDSP_Length(fftSize), .FORWARD)
        else { return (0, 0) }
        defer { vDSP_DFT_DestroySetup(setup) }

        var window = [Float](repeating: 0, count: fftSize)
        vDSP_hann_window(&window, vDSP_Length(fftSize), Int32(vDSP_HANN_NORM))

        let bins = fftSize / 2
        let binHz = Float(sampleRate) / Float(fftSize)
        let (bandOfBin, bandCenterHz) = melLayout(binHz: binHz, bins: bins)

        let frameCount = (x.count - fftSize) / hopSize + 1
        var bandMean = [Float](repeating: 0, count: melBandCount)
        var flux = [Float](repeating: 0, count: frameCount)
        var prevMag = [Float](repeating: 0, count: bins)

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
            var f: Float = 0
            for b in 0..<bins {
                let m = sqrt(outReal[b] * outReal[b] + outImag[b] * outImag[b])
                mag[b] = m
                f += max(0, m - prevMag[b])
                bandMean[bandOfBin[b]] += m
            }
            flux[k] = f
            swap(&prevMag, &mag)
        }

        var weighted: Float = 0
        var totalEnergy: Float = 0
        for b in 0..<melBandCount {
            weighted += bandMean[b] * bandCenterHz[b]
            totalEnergy += bandMean[b]
        }
        let centroid = totalEnergy > 1e-9 ? weighted / totalEnergy : 0

        // Flux CoV, skipping frame 0 (its flux is just "signal
        // appeared", not spectral movement).
        let body = frameCount > 1 ? Array(flux[1...]) : flux
        var mean: Float = 0
        vDSP_meanv(body, 1, &mean, vDSP_Length(body.count))
        var variability: Float = 0
        if mean > 1e-9 {
            var diff = [Float](repeating: 0, count: body.count)
            var negMean = -mean
            vDSP_vsadd(body, 1, &negMean, &diff, 1, vDSP_Length(body.count))
            var sumsq: Float = 0
            vDSP_svesq(diff, 1, &sumsq, vDSP_Length(body.count))
            variability = sqrt(sumsq / Float(body.count)) / mean
        }
        return (centroid, variability)
    }

    /// Fraction of spectral power below `cutoffHz` (0…1) over a
    /// 1024/256 Hann STFT. A strong low-band cue for kick vs the rest
    /// of a drum kit (chest thumps / table booms concentrate here).
    static func lowBandRatio(_ x: [Float], sampleRate: Double, cutoffHz: Float = 150) -> Float {
        guard x.count >= fftSize,
              let setup = vDSP_DFT_zop_CreateSetup(nil, vDSP_Length(fftSize), .FORWARD)
        else { return 0 }
        defer { vDSP_DFT_DestroySetup(setup) }

        var window = [Float](repeating: 0, count: fftSize)
        vDSP_hann_window(&window, vDSP_Length(fftSize), Int32(vDSP_HANN_NORM))

        let bins = fftSize / 2
        let binHz = Float(sampleRate) / Float(fftSize)
        let cutoffBin = max(1, min(bins, Int((cutoffHz / binHz).rounded())))
        let frameCount = (x.count - fftSize) / hopSize + 1

        var frame = [Float](repeating: 0, count: fftSize)
        let zeros = [Float](repeating: 0, count: fftSize)
        var outReal = [Float](repeating: 0, count: fftSize)
        var outImag = [Float](repeating: 0, count: fftSize)

        var low: Float = 0
        var total: Float = 0
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
                let power = outReal[b] * outReal[b] + outImag[b] * outImag[b]
                total += power
                if b < cutoffBin { low += power }
            }
        }
        return total > 1e-12 ? low / total : 0
    }

    /// Assign every FFT bin to one of 26 mel bands (80 Hz–12 kHz
    /// centers, HTK mel). Bins outside land in the edge bands.
    static func melLayout(binHz: Float, bins: Int) -> (bandOfBin: [Int], bandCenterHz: [Float]) {
        func mel(_ hz: Float) -> Float { 2595 * log10(1 + hz / 700) }
        func hz(_ mel: Float) -> Float { 700 * (pow(10, mel / 2595) - 1) }

        let melLo = mel(80)
        let melHi = mel(12_000)
        var edges = [Float](repeating: 0, count: melBandCount + 1)
        for b in 0...melBandCount {
            edges[b] = hz(melLo + (melHi - melLo) * Float(b) / Float(melBandCount))
        }
        var centers = [Float](repeating: 0, count: melBandCount)
        for b in 0..<melBandCount {
            centers[b] = (edges[b] + edges[b + 1]) / 2
        }
        var bandOfBin = [Int](repeating: 0, count: bins)
        for i in 0..<bins {
            let f = Float(i) * binHz
            let band = edges.dropLast().lastIndex(where: { f >= $0 }) ?? 0
            bandOfBin[i] = min(band, melBandCount - 1)
        }
        return (bandOfBin, centers)
    }

    // MARK: - Decision tree

    static func decide(_ f: Features) -> (SampleClass, Double) {
        // Unpitched branch.
        if f.pitchedness < 0.35 {
            if f.attackSec <= 0.03 && f.sustainRatio < 0.25 && f.durationSec < 1.5 {
                // Hard hit, dies away fast.
                let confidence = f.zcr > 0.05 ? 0.85 : 0.7
                return (.percussion, confidence)
            }
            if f.durationSec >= 2.0 && f.sustainRatio > 0.4 && f.fluxVariability < 1.0 {
                // Long, held, spectrally static noise — a pad/drone.
                return (.texture, 0.65)
            }
            if f.onsetCount >= 3 {
                // Several unpitched hits — a beatboxed/tapped phrase.
                return (.phrase, 0.5)
            }
            return (.unknown, 0.3)
        }

        // Pitched branch.
        if f.durationSec >= 1.5 && f.sustainRatio > 0.5 && f.onsetCount <= 1
            && f.fluxVariability < 1.2 {
            return (.sustainedNote, 0.8)
        }
        if f.durationSec < 1.2 && f.fluxVariability > 1.6 {
            // Short + big spectral movement (formant transitions).
            // Checked BEFORE onset count: plosives inside a single
            // word register as onsets but don't make it a phrase.
            return (.speechWord, 0.55)
        }
        if f.onsetCount >= 3 {
            // Multiple distinct pitched onsets — sung/played phrase.
            return (.phrase, 0.65)
        }
        if f.durationSec < 1.2 {
            // Single short spectrally-steady pitched event.
            return (.vocalChop, 0.6)
        }
        return (.phrase, 0.45)
    }
}
