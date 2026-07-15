// LiveBeatFeatures.swift
//
// Micro-feature vector for real-time drum classification in Live Beat
// mode. Extracted from a 128-sample window (~2.6ms @ 48kHz) to minimize
// latency while capturing enough spectral information to distinguish
// kick (low), snare (mid+bright), hat (high+noisy), clap (mid+wide).
//
// These 4 features are a stripped-down version of OnsetFeatures,
// optimized for sub-millisecond extraction on the main actor.

import Accelerate
import Foundation

/// Minimal feature vector for real-time drum classification.
public struct LiveBeatFeatures: Sendable, Codable, Equatable {
    /// Normalized spectral centroid (0-1, mapped to 0-8kHz).
    /// High = bright (hats), low = dark (kick).
    public let centroidNorm: Float

    /// Zero-crossing rate (0-1). High = noisy (hats, snare).
    public let zcr: Float

    /// Low-band energy ratio (energy <500Hz / total).
    /// High = kick, low = hat.
    public let lowRatio: Float

    /// Crest factor (peak / RMS). High = sharp attack (perc),
    /// low = sustained.
    public let crestFactor: Float

    public init(
        centroidNorm: Float,
        zcr: Float,
        lowRatio: Float,
        crestFactor: Float
    ) {
        self.centroidNorm = centroidNorm
        self.zcr = zcr
        self.lowRatio = lowRatio
        self.crestFactor = crestFactor
    }

    /// Euclidean distance to another feature vector.
    public func distance(to other: LiveBeatFeatures) -> Float {
        let dc = centroidNorm - other.centroidNorm
        let dz = zcr - other.zcr
        let dl = lowRatio - other.lowRatio
        let df = (crestFactor - other.crestFactor) / 10  // normalize crest range
        return sqrt(dc * dc + dz * dz + dl * dl + df * df)
    }

    /// Weighted distance using per-feature variance (Mahalanobis-lite).
    public func distance(to other: LiveBeatFeatures, variance: LiveBeatFeatures) -> Float {
        let dc = (centroidNorm - other.centroidNorm) / max(variance.centroidNorm, 0.01)
        let dz = (zcr - other.zcr) / max(variance.zcr, 0.01)
        let dl = (lowRatio - other.lowRatio) / max(variance.lowRatio, 0.01)
        let df = (crestFactor - other.crestFactor) / max(variance.crestFactor, 0.1)
        return sqrt(dc * dc + dz * dz + dl * dl + df * df)
    }
}

// MARK: - Fast Extraction

extension LiveBeatFeatures {
    /// FFT size for micro-feature extraction.
    /// 128 samples = 64 frequency bins = 375 Hz resolution @ 48kHz.
    public static let windowSize = 128

    /// Extract features from a short audio window.
    /// - Parameters:
    ///   - samples: Audio samples (should be ~128 samples).
    ///   - sampleRate: Sample rate (typically 48000).
    /// - Returns: Extracted features, or nil if input too short.
    public static func extract(
        from samples: UnsafePointer<Float>,
        count: Int,
        sampleRate: Double
    ) -> LiveBeatFeatures? {
        guard count >= 8 else { return nil }

        let n = min(count, windowSize)

        // Zero-crossing rate
        let zcr = computeZCR(samples, count: n)

        // RMS and peak for crest factor
        var rms: Float = 0
        var peak: Float = 0
        vDSP_rmsqv(samples, 1, &rms, vDSP_Length(n))
        vDSP_maxmgv(samples, 1, &peak, vDSP_Length(n))
        let crestFactor = rms > 1e-6 ? peak / rms : 1.0

        // Spectral features via FFT
        let (centroidNorm, lowRatio) = computeSpectralFeatures(
            samples, count: n, sampleRate: sampleRate
        )

        return LiveBeatFeatures(
            centroidNorm: centroidNorm,
            zcr: zcr,
            lowRatio: lowRatio,
            crestFactor: crestFactor
        )
    }

    /// Convenience overload for Array input.
    public static func extract(
        from samples: [Float],
        sampleRate: Double = 48000
    ) -> LiveBeatFeatures? {
        samples.withUnsafeBufferPointer { buffer in
            guard let base = buffer.baseAddress else { return nil }
            return extract(from: base, count: buffer.count, sampleRate: sampleRate)
        }
    }

    // MARK: - Private Helpers

    private static func computeZCR(_ samples: UnsafePointer<Float>, count: Int) -> Float {
        guard count > 1 else { return 0 }
        var crossings = 0
        for i in 1..<count {
            if (samples[i - 1] >= 0) != (samples[i] >= 0) {
                crossings += 1
            }
        }
        return Float(crossings) / Float(count - 1)
    }

    private static func computeSpectralFeatures(
        _ samples: UnsafePointer<Float>,
        count: Int,
        sampleRate: Double
    ) -> (centroidNorm: Float, lowRatio: Float) {
        // Pad to power of 2 for FFT
        let fftSize = 128
        let log2n = vDSP_Length(log2(Double(fftSize)))

        guard let fftSetup = vDSP_create_fftsetup(log2n, FFTRadix(kFFTRadix2)) else {
            return (0.5, 0.5)
        }
        defer { vDSP_destroy_fftsetup(fftSetup) }

        // Apply Hann window and zero-pad
        var windowed = [Float](repeating: 0, count: fftSize)
        var window = [Float](repeating: 0, count: count)
        vDSP_hann_window(&window, vDSP_Length(count), Int32(vDSP_HANN_NORM))
        vDSP_vmul(samples, 1, window, 1, &windowed, 1, vDSP_Length(count))

        // Real FFT
        var realPart = [Float](repeating: 0, count: fftSize / 2)
        var imagPart = [Float](repeating: 0, count: fftSize / 2)
        var magnitudes = [Float](repeating: 0, count: fftSize / 2)

        realPart.withUnsafeMutableBufferPointer { realBuf in
            imagPart.withUnsafeMutableBufferPointer { imagBuf in
                var splitComplex = DSPSplitComplex(realp: realBuf.baseAddress!, imagp: imagBuf.baseAddress!)

                windowed.withUnsafeBufferPointer { buffer in
                    buffer.baseAddress!.withMemoryRebound(to: DSPComplex.self, capacity: fftSize / 2) { ptr in
                        vDSP_ctoz(ptr, 2, &splitComplex, 1, vDSP_Length(fftSize / 2))
                    }
                }

                vDSP_fft_zrip(fftSetup, &splitComplex, 1, log2n, FFTDirection(kFFTDirection_Forward))

                // Magnitude spectrum
                vDSP_zvabs(&splitComplex, 1, &magnitudes, 1, vDSP_Length(fftSize / 2))
            }
        }

        // Spectral centroid (weighted average of bin indices)
        let binWidth = Float(sampleRate) / Float(fftSize)  // Hz per bin
        var weightedSum: Float = 0
        var totalMag: Float = 0
        for i in 0..<magnitudes.count {
            let freq = Float(i) * binWidth
            weightedSum += magnitudes[i] * freq
            totalMag += magnitudes[i]
        }
        let centroidHz = totalMag > 1e-6 ? weightedSum / totalMag : 0
        let centroidNorm = min(1.0, centroidHz / 8000)  // normalize to 0-8kHz

        // Low-band ratio (bins < 500 Hz)
        let lowBinCutoff = Int(500 / binWidth)
        var lowEnergy: Float = 0
        var totalEnergy: Float = 0
        for i in 0..<magnitudes.count {
            let energy = magnitudes[i] * magnitudes[i]
            totalEnergy += energy
            if i < lowBinCutoff {
                lowEnergy += energy
            }
        }
        let lowRatio = totalEnergy > 1e-6 ? lowEnergy / totalEnergy : 0.5

        return (centroidNorm, lowRatio)
    }
}

// MARK: - Aggregation

extension LiveBeatFeatures {
    /// Compute mean (centroid) of multiple feature vectors.
    public static func mean(_ features: [LiveBeatFeatures]) -> LiveBeatFeatures? {
        guard !features.isEmpty else { return nil }
        let n = Float(features.count)
        return LiveBeatFeatures(
            centroidNorm: features.map(\.centroidNorm).reduce(0, +) / n,
            zcr: features.map(\.zcr).reduce(0, +) / n,
            lowRatio: features.map(\.lowRatio).reduce(0, +) / n,
            crestFactor: features.map(\.crestFactor).reduce(0, +) / n
        )
    }

    /// Compute variance (spread) of multiple feature vectors.
    public static func variance(_ features: [LiveBeatFeatures], mean: LiveBeatFeatures) -> LiveBeatFeatures? {
        guard features.count > 1 else {
            return LiveBeatFeatures(centroidNorm: 0.1, zcr: 0.1, lowRatio: 0.1, crestFactor: 1.0)
        }
        let n = Float(features.count)
        func v(_ kp: KeyPath<LiveBeatFeatures, Float>) -> Float {
            let m = mean[keyPath: kp]
            return features.map { ($0[keyPath: kp] - m) * ($0[keyPath: kp] - m) }.reduce(0, +) / n
        }
        return LiveBeatFeatures(
            centroidNorm: sqrt(v(\.centroidNorm)),
            zcr: sqrt(v(\.zcr)),
            lowRatio: sqrt(v(\.lowRatio)),
            crestFactor: sqrt(v(\.crestFactor))
        )
    }
}
