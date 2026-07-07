// TransformEngine.swift
//
// Applies a PadTransform chain in sequence: reverse, stutter, stretch,
// granular, octave-shift, harmony/choir, gate, spectral freeze, loop.
// Each transform reads the prior stage's output and writes the next
// input; empty input → empty output. The final result is peak-normalised
// when |peak| > 1 (like GranularEngine does).
//
// Tempo-synced transforms (stutter, gate) use the supplied tempoBpm,
// guarded to > 0 (120 fallback). The harmony transform calls chordAt(t)
// for chord-aware voice leading; nil → no chord info → nominal intervals.

import Foundation
import Accelerate

public enum TransformEngine {

    // MARK: - Public API

    /// Apply `chain` in sequence to one channel of audio. `tempoBpm`
    /// drives stutter/gate (guard >0 else 120). `chordAt` feeds the
    /// harmony transform (nil → no chord info → nominal intervals).
    public static func render(
        _ channel: [Float],
        chain: [PadTransform],
        tempoBpm: Double,
        sampleRate: Double,
        chordAt: ((Double) -> [Int])? = nil
    ) -> [Float] {
        guard !channel.isEmpty else { return [] }
        let bpm = max(tempoBpm, 0.001)
        let chord = chordAt ?? { _ in [] }

        var audio = channel
        for transform in chain {
            audio = apply(transform, to: audio, bpm: bpm,
                          sampleRate: sampleRate, chordAt: chord)
            if audio.isEmpty { return [] }
        }

        // Peak-normalise only if clipping (0.999 target, match granular).
        var peak: Float = 0
        vDSP_maxmgv(audio, 1, &peak, vDSP_Length(audio.count))
        if peak > 1 {
            var scale: Float = 0.999 / peak
            vDSP_vsmul(audio, 1, &scale, &audio, 1, vDSP_Length(audio.count))
        }

        return audio
    }

    // MARK: - Per-transform dispatch

    private static func apply(
        _ transform: PadTransform,
        to input: [Float],
        bpm: Double,
        sampleRate: Double,
        chordAt: (Double) -> [Int]
    ) -> [Float] {
        switch transform {
        case .reverse:
            return applyReverse(input)
        case .stutter(let rate):
            return applyStutter(input, rate: rate, bpm: bpm,
                                sampleRate: sampleRate)
        case .granular(let params):
            return applyGranular(input, params: params,
                                 sampleRate: sampleRate)
        case .stretch(let factor):
            return applyStretch(input, factor: factor,
                                sampleRate: sampleRate)
        case .octave(let n):
            return applyOctave(input, semitones: n * 12,
                               sampleRate: sampleRate)
        case .harmony:
            return applyHarmony(input, chordAt: chordAt,
                                sampleRate: sampleRate)
        case .choir:
            return applyChoir(input, sampleRate: sampleRate)
        case .gate(let steps):
            return applyGate(input, steps: steps, bpm: bpm,
                             sampleRate: sampleRate)
        case .loop:
            return input
        case .spectralFreeze(let atSec, let seed):
            return applySpectralFreeze(input, atSec: atSec, seed: seed,
                                        sampleRate: sampleRate)
        }
    }

    // MARK: - Transform implementations

    private static func applyReverse(_ input: [Float]) -> [Float] {
        return [Float](input.reversed())
    }

    private static func applyStutter(
        _ input: [Float], rate: StutterRate, bpm: Double,
        sampleRate: Double
    ) -> [Float] {
        let segLen = Int((60.0 / bpm * rate.beats * sampleRate).rounded())
        guard segLen > 0 && segLen < input.count else { return input }

        // Fade ~5 ms, clamped to segLen/4 to avoid overlapping fades.
        let fadeSamples = min(
            max(1, Int((0.005 * sampleRate).rounded())),
            segLen / 4
        )

        var out = [Float](repeating: 0, count: input.count)
        let slice = Array(input[0..<segLen])

        var pos = 0
        while pos < input.count {
            let remain = input.count - pos
            let copy = min(segLen, remain)

            for i in 0..<copy {
                var sample = slice[i]
                // Fade in
                if i < fadeSamples {
                    sample *= Float(i) / Float(fadeSamples)
                }
                // Fade out
                if i >= copy - fadeSamples {
                    sample *= Float(copy - 1 - i) / Float(fadeSamples)
                }
                out[pos + i] = sample
            }
            pos += segLen
        }
        return out
    }

    private static func applyGranular(
        _ input: [Float], params: GranularParams, sampleRate: Double
    ) -> [Float] {
        let durationSec = Double(input.count) / sampleRate
        return GranularEngine.render(input, params: params,
                                     durationSec: durationSec,
                                     sampleRate: sampleRate)
    }

    private static func applyStretch(
        _ input: [Float], factor: Double, sampleRate: Double
    ) -> [Float] {
        return WSOLAStretch.stretch(input, factor: factor,
                                    sampleRate: sampleRate)
    }

    private static func applyOctave(
        _ input: [Float], semitones: Int, sampleRate: Double
    ) -> [Float] {
        let clamped = min(max(semitones, -24), 24)
        guard clamped != 0 else { return input }

        let rate = pow(2.0, Double(clamped) / 12.0)

        // Step 1: resample at rate (pitch shift)
        let newLen = Int((Double(input.count) / rate).rounded())
        guard newLen > 0 else { return [] }
        var resampled = [Float](repeating: 0, count: newLen)

        for i in 0..<newLen {
            let srcPos = Double(i) * rate
            let i0 = Int(srcPos)
            let frac = Float(srcPos - Double(i0))
            if i0 + 1 < input.count {
                resampled[i] = input[i0] + (input[i0 + 1] - input[i0]) * frac
            } else if i0 < input.count {
                resampled[i] = input[i0]
            }
        }

        // Step 2: time-stretch back to original duration
        return WSOLAStretch.stretch(resampled, factor: rate,
                                    sampleRate: sampleRate)
    }

    private static func applyHarmony(
        _ input: [Float], chordAt: (Double) -> [Int],
        sampleRate: Double
    ) -> [Float] {
        let settings = HarmonySettings(
            addThird: true, addFifth: true,
            addOctave: false, choir: false
        )
        return PSOLAHarmonizer.harmonize(input, sampleRate: sampleRate,
                                         chordAt: chordAt,
                                         settings: settings)
    }

    private static func applyChoir(
        _ input: [Float], sampleRate: Double
    ) -> [Float] {
        // Choir-only: dry + 3× detuned copies via octave harmony voice.
        // Choir mode splits each harmony voice into 3 detuned copies; we
        // need at least one harmony voice for choir to activate.
        let settings = HarmonySettings(
            addThird: false, addFifth: false,
            addOctave: true, choir: true
        )
        return PSOLAHarmonizer.harmonize(
            input, sampleRate: sampleRate,
            chordAt: { _ in [] },
            settings: settings
        )
    }

    private static func applyGate(
        _ input: [Float], steps: [Bool], bpm: Double,
        sampleRate: Double
    ) -> [Float] {
        guard !steps.isEmpty else { return input }

        // 1/16-note steps
        let stepLen = Int((60.0 / bpm * 0.25 * sampleRate).rounded())
        guard stepLen > 0 else { return input }

        let fadeSamples = min(
            max(1, Int((0.005 * sampleRate).rounded())),
            stepLen / 4
        )

        var out = input
        for i in 0..<out.count {
            let stepIdx = (i / stepLen) % steps.count
            let posInStep = i % stepLen

            if !steps[stepIdx] {
                out[i] = 0
            } else {
                // Apply fades at step boundaries
                var gain: Float = 1.0
                // Fade in at start of ON step
                if posInStep < fadeSamples {
                    gain = Float(posInStep) / Float(fadeSamples)
                }
                // Fade out at end of ON step (if next is OFF)
                let nextIdx = (stepIdx + 1) % steps.count
                if !steps[nextIdx] && posInStep >= stepLen - fadeSamples {
                    gain = Float(stepLen - 1 - posInStep) / Float(fadeSamples)
                }
                out[i] *= gain
            }
        }
        return out
    }

    private static func applySpectralFreeze(
        _ input: [Float], atSec: Double, seed: UInt64,
        sampleRate: Double
    ) -> [Float] {
        let atSample = max(0, Int(atSec * sampleRate))
        let durationSec = Double(input.count) / sampleRate
        return SpectralFreeze.freeze(input, atSample: atSample,
                                     durationSec: durationSec,
                                     seed: seed, sampleRate: sampleRate)
    }
}
