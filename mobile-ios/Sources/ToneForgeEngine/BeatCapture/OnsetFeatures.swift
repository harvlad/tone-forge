// OnsetFeatures.swift
//
// Beat Capture (D-024): per-onset acoustic features the
// `BeatClassifier` reads to assign a `DrumRole`. Computed from a
// single onset slice (48 kHz mono). Reuses the existing
// `HeuristicClassifier` DSP (spectral centroid, pitchedness,
// low-band ratio) plus a light local envelope for attack/peak.

import Foundation
import Accelerate

/// Acoustic descriptor for one percussive onset.
public struct OnsetFeatures: Sendable, Equatable, Codable {
    /// Mel-weighted spectral centroid (Hz) — brightness.
    public let centroidHz: Float
    /// Zero-crossing rate (crossings per sample, 0…1) — noisiness.
    public let zcr: Float
    /// Seconds to reach 90% of the envelope peak — transient sharpness.
    public let attackSec: Double
    /// Slice length in seconds.
    public let durationSec: Double
    /// Autocorrelation pitchedness (≈1 tonal, ≈0 noise).
    public let pitchedness: Float
    /// Fraction of spectral energy below ~150 Hz — key kick cue.
    public let lowBandRatio: Float
    /// Peak RMS of the slice — drives velocity.
    public let peakRMS: Float

    public init(
        centroidHz: Float,
        zcr: Float,
        attackSec: Double,
        durationSec: Double,
        pitchedness: Float,
        lowBandRatio: Float,
        peakRMS: Float
    ) {
        self.centroidHz = centroidHz
        self.zcr = zcr
        self.attackSec = attackSec
        self.durationSec = durationSec
        self.pitchedness = pitchedness
        self.lowBandRatio = lowBandRatio
        self.peakRMS = peakRMS
    }

    /// Extract features from one onset slice.
    ///
    /// Spectral features need ≥1024 samples and pitchedness ≥2048;
    /// short slices degrade gracefully (those cues return 0).
    public static func extract(_ slice: [Float], sampleRate: Double) -> OnsetFeatures {
        let durationSec = Double(slice.count) / sampleRate

        // 5 ms RMS envelope for attack + peak.
        let win = max(1, Int(0.005 * sampleRate))
        var rms: [Float] = []
        var i = 0
        while i < slice.count {
            let n = min(win, slice.count - i)
            var value: Float = 0
            slice.withUnsafeBufferPointer { buf in
                vDSP_rmsqv(buf.baseAddress! + i, 1, &value, vDSP_Length(n))
            }
            rms.append(value)
            i += n
        }
        var peakRMS: Float = 0
        if !rms.isEmpty { vDSP_maxv(rms, 1, &peakRMS, vDSP_Length(rms.count)) }
        let peakIdx = rms.firstIndex(of: peakRMS) ?? 0
        var attackSec = 0.0
        if peakRMS > 0 {
            let attackIdx = rms.prefix(peakIdx + 1)
                .firstIndex(where: { $0 >= 0.9 * peakRMS }) ?? peakIdx
            attackSec = Double(attackIdx) * 0.005
        }

        // Zero-crossing rate.
        var crossings = 0
        if slice.count > 1 {
            for j in 1..<slice.count where (slice[j - 1] < 0) != (slice[j] < 0) {
                crossings += 1
            }
        }
        let zcr = slice.isEmpty ? 0 : Float(crossings) / Float(slice.count)

        let (centroidHz, _) = HeuristicClassifier.spectralFeatures(
            slice, sampleRate: sampleRate)
        let pitchedness = HeuristicClassifier.pitchedness(
            slice, sampleRate: sampleRate)
        let lowBandRatio = HeuristicClassifier.lowBandRatio(
            slice, sampleRate: sampleRate)

        return OnsetFeatures(
            centroidHz: centroidHz,
            zcr: zcr,
            attackSec: attackSec,
            durationSec: durationSec,
            pitchedness: pitchedness,
            lowBandRatio: lowBandRatio,
            peakRMS: peakRMS
        )
    }
}
