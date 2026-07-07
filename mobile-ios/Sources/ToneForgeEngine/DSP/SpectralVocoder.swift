// SpectralVocoder.swift
//
// Capture-only channel-vocoder core: imposes the modulator's band
// envelope onto an arbitrary carrier. Mode-agnostic — the perform
// modes build their own carriers (synth stacks, noise, chords) and
// feed both signals here as plain buffers.
//
// Signal path (offline, whole-buffer):
//   STFT: FFT 2048 / hop 512, Hann analysis AND synthesis windows,
//   vDSP_DFT_zop. The overlap-add is normalized by the accumulated
//   window-square sum, so a unity gain path is transparent regardless
//   of hop/window pairing.
//
//   Bands: `config.bands` log-spaced bands spanning 80 Hz–12 kHz
//   (bins outside the span clamp to the edge bands). Per frame the
//   modulator and carrier band powers are smoothed with the SAME
//   attack/release one-pole envelopes (time constants derived from
//   the hop period); the band gain is √(modPower/carrierPower) — a
//   whitening/cross-synthesis formulation, so carrier == modulator
//   gives gain 1 in every band (the OLA-unity guarantee) while any
//   other carrier is reshaped to the modulator's envelope. Gains are
//   clamped to +24 dB so spectrally empty carrier bands can't explode.
//
//   Emphasis: the +6 dB/oct modulator pre-emphasis and the matching
//   output de-emphasis are implemented as ZERO-PHASE per-bin spectral
//   weights e(f) = clamp(f, 62.5 Hz, 12 kHz)/1000 and 1/e(f). A
//   time-domain one-zero/one-pole pair would add large phase rotation
//   (its inverse only matches in magnitude through the band-gain
//   staircase), audibly smearing transients and breaking the unity
//   path; the spectral weights invert exactly.
//
//   Gains scale the CARRIER magnitudes only — carrier phases are kept
//   untouched (phase-locked resynthesis, no phase-vocoder smearing).
//
//   Sibilance: above `sibilanceCutoffHz` the (pre-emphasized)
//   modulator spectrum is mixed in at `sibilancePassthrough`; after
//   de-emphasis that term is exactly passthrough·modulator, so
//   consonants survive tonal carriers.
//
// Output length always equals the modulator length; a shorter carrier
// is looped, an empty carrier yields silence.

import Foundation
import Accelerate

// MARK: - Configuration

/// Tuning for `SpectralVocoder.process`. Defaults match the capture
/// voice preset.
public struct VocoderConfig: Sendable, Equatable {
    /// Number of log-spaced analysis bands across 80 Hz–12 kHz.
    public var bands: Int
    /// Band-envelope rise time constant (ms).
    public var attackMs: Double
    /// Band-envelope fall time constant (ms).
    public var releaseMs: Double
    /// Modulator mix above `sibilanceCutoffHz` (0 = none, 1 = full).
    public var sibilancePassthrough: Float
    /// Frequency above which the modulator is passed through.
    public var sibilanceCutoffHz: Double

    public init(
        bands: Int = 64,
        attackMs: Double = 30,
        releaseMs: Double = 80,
        sibilancePassthrough: Float = 0.3,
        sibilanceCutoffHz: Double = 6_000
    ) {
        self.bands = bands
        self.attackMs = attackMs
        self.releaseMs = releaseMs
        self.sibilancePassthrough = sibilancePassthrough
        self.sibilanceCutoffHz = sibilanceCutoffHz
    }
}

// MARK: - Vocoder

public enum SpectralVocoder {

    static let fftSize = 2_048
    static let hop = 512
    static let bandLoHz = 80.0
    static let bandHiHz = 12_000.0

    /// Vocode `carrier` with `modulator`'s band envelope. Deterministic
    /// and pure; the output has exactly `modulator.count` samples.
    public static func process(
        modulator: [Float], carrier: [Float], config: VocoderConfig, sampleRate: Double
    ) -> [Float] {
        let outLen = modulator.count
        guard outLen > 0 else { return [] }
        guard !carrier.isEmpty else { return [Float](repeating: 0, count: outLen) }

        let n = fftSize
        let half = n / 2
        let bands = max(1, config.bands)

        // Loop the carrier out to the modulator length.
        var carrierExt = [Float](repeating: 0, count: outLen)
        for i in 0..<outLen { carrierExt[i] = carrier[i % carrier.count] }

        guard
            let fwd = vDSP_DFT_zop_CreateSetup(nil, vDSP_Length(n), .FORWARD),
            let inv = vDSP_DFT_zop_CreateSetup(fwd, vDSP_Length(n), .INVERSE)
        else { return [Float](repeating: 0, count: outLen) }
        defer {
            vDSP_DFT_DestroySetup(fwd)
            vDSP_DFT_DestroySetup(inv)
        }

        // Periodic Hann for analysis and synthesis.
        var window = [Float](repeating: 0, count: n)
        for i in 0..<n {
            window[i] = Float(0.5 - 0.5 * cos(2.0 * Double.pi * Double(i) / Double(n)))
        }

        // Per-bin precomputation for the low half [0…n/2]:
        // frequency, band index, emphasis weight.
        let binHz = sampleRate / Double(n)
        let hi = min(bandHiHz, sampleRate * 0.45)
        let logSpan = log(hi / bandLoHz)
        var bandOfBin = [Int](repeating: 0, count: half + 1)
        var emphasis = [Float](repeating: 1, count: half + 1)
        for k in 0...half {
            let f = Double(k) * binHz
            let raw = f <= bandLoHz
                ? 0
                : Int(floor(Double(bands) * log(f / bandLoHz) / logSpan))
            bandOfBin[k] = min(max(raw, 0), bands - 1)
            emphasis[k] = Float(min(max(f, 62.5), bandHiHz) / 1_000.0)
        }
        let sibStartBin = min(half + 1, max(0, Int(ceil(config.sibilanceCutoffHz / binHz))))

        // One-pole smoothing coefficients from the hop period.
        let hopSec = Double(hop) / sampleRate
        let attackCoef = Float(1 - exp(-hopSec / max(1e-4, config.attackMs / 1_000)))
        let releaseCoef = Float(1 - exp(-hopSec / max(1e-4, config.releaseMs / 1_000)))

        // Scratch buffers.
        var frameM = [Float](repeating: 0, count: n)
        var frameC = [Float](repeating: 0, count: n)
        let zeroIn = [Float](repeating: 0, count: n)
        var modRe = [Float](repeating: 0, count: n)
        var modIm = [Float](repeating: 0, count: n)
        var carRe = [Float](repeating: 0, count: n)
        var carIm = [Float](repeating: 0, count: n)
        var outRe = [Float](repeating: 0, count: n)
        var outIm = [Float](repeating: 0, count: n)
        var timeRe = [Float](repeating: 0, count: n)
        var timeIm = [Float](repeating: 0, count: n)

        var modEnv = [Float](repeating: 0, count: bands)
        var carEnv = [Float](repeating: 0, count: bands)
        var modPow = [Float](repeating: 0, count: bands)
        var carPow = [Float](repeating: 0, count: bands)
        var gain = [Float](repeating: 1, count: bands)

        var acc = [Float](repeating: 0, count: outLen + n)
        var wsum = [Float](repeating: 0, count: outLen + n)

        let invN = Float(1.0 / Double(n))
        let sib = config.sibilancePassthrough
        let epsilon: Float = 1e-12
        let maxGain: Float = 16  // +24 dB

        var p = 0
        while p < outLen {
            // Windowed frames (zero-padded past the end).
            for i in 0..<n {
                let s = p + i
                frameM[i] = s < outLen ? modulator[s] * window[i] : 0
                frameC[i] = s < outLen ? carrierExt[s] * window[i] : 0
            }
            vDSP_DFT_Execute(fwd, frameM, zeroIn, &modRe, &modIm)
            vDSP_DFT_Execute(fwd, frameC, zeroIn, &carRe, &carIm)

            // Band powers: modulator pre-emphasized, carrier plain.
            for b in 0..<bands { modPow[b] = 0; carPow[b] = 0 }
            for k in 0...half {
                let b = bandOfBin[k]
                let e = emphasis[k]
                let mp = (modRe[k] * modRe[k] + modIm[k] * modIm[k]) * e * e
                let cp = carRe[k] * carRe[k] + carIm[k] * carIm[k]
                modPow[b] += mp
                carPow[b] += cp
            }

            // Shared attack/release smoothing, then whitening gains.
            for b in 0..<bands {
                let cm = modPow[b] > modEnv[b] ? attackCoef : releaseCoef
                modEnv[b] += cm * (modPow[b] - modEnv[b])
                let cc = carPow[b] > carEnv[b] ? attackCoef : releaseCoef
                carEnv[b] += cc * (carPow[b] - carEnv[b])
                gain[b] = min(sqrt((modEnv[b] + epsilon) / (carEnv[b] + epsilon)), maxGain)
            }

            // Shape carrier, add sibilance passthrough, de-emphasize.
            // Upper half mirrors the conjugate of the lower half so the
            // inverse transform stays real.
            for k in 0...half {
                let g = gain[bandOfBin[k]]
                var re = carRe[k] * g
                var im = carIm[k] * g
                if k >= sibStartBin {
                    let e = emphasis[k]
                    re += sib * modRe[k] * e
                    im += sib * modIm[k] * e
                }
                let d = 1 / emphasis[k]
                re *= d
                im *= d
                outRe[k] = re
                outIm[k] = im
                if k > 0 && k < half {
                    outRe[n - k] = re
                    outIm[n - k] = -im
                }
            }
            outIm[0] = 0
            outIm[half] = 0

            vDSP_DFT_Execute(inv, outRe, outIm, &timeRe, &timeIm)

            // Windowed overlap-add with window-square bookkeeping.
            for i in 0..<n {
                let w = window[i]
                acc[p + i] += timeRe[i] * invN * w
                wsum[p + i] += w * w
            }
            p += hop
        }

        var out = [Float](repeating: 0, count: outLen)
        for i in 0..<outLen {
            out[i] = acc[i] / max(wsum[i], 1e-6)
        }
        return out
    }
}
