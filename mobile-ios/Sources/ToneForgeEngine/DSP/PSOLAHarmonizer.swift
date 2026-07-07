// PSOLAHarmonizer.swift
//
// Pitch-tracked harmony generation for captured vocals, built from
// three offline stages:
//
//   trackF0 — normalized-autocorrelation pitch tracker, 60–500 Hz,
//   25 ms frames / 10 ms hop. To dodge octave errors (every multiple
//   of the true period also correlates at ~1.0) the tracker takes the
//   SMALLEST lag whose local NCC maximum reaches 90% of the global
//   maximum, then refines it with parabolic interpolation for
//   sub-sample (cent-level) accuracy. Confidence is the NCC peak.
//
//   shift — epoch-synchronous PSOLA. Analysis epochs are seeded from
//   the F0 track and snapped to local waveform maxima (one mark per
//   period at a consistent phase); synthesis epochs are re-spaced by
//   the pitch ratio; each synthesis epoch copies a Hann-windowed
//   two-period grain from its nearest analysis epoch. The grain
//   accumulator is normalized by the accumulated window sum (with a
//   floor so region edges taper instead of exploding), and unvoiced
//   material (confidence < 0.6) passes through unshifted with a
//   window-sum crossfade at the voiced boundaries. Shifts of ≥ 3 st
//   flatten the signal against its own LPC envelope (FormantEstimator,
//   order 20, per STFT frame) before PSOLA and re-apply the ORIGINAL
//   envelopes afterwards, so formants stay put (no chipmunk).
//
//   harmonize — dry voice at unity plus optional third/fifth/octave
//   PSOLA voices. Per contiguous voiced region each harmony voice
//   picks the chord tone (from `chordAt`, any octave) closest to
//   dryPitch × nominal interval and clamps the shift to nominal ±2 st
//   (voice-leading); one representative shift per voice per region.
//   Choir mode splits each harmony voice into 3 copies detuned
//   ±7 cents, delayed 0/15/30 ms, at 1/√3 gain each.
//
// Everything is pure and deterministic — same input, same output.

import Foundation
import Accelerate

// MARK: - Settings

/// Voice-stack switches for `PSOLAHarmonizer.harmonize`.
public struct HarmonySettings: Sendable, Equatable {
    /// Add a diatonic-third voice (nominal +4 st, gain 0.7).
    public var addThird: Bool
    /// Add a fifth voice (nominal +7 st, gain 0.6).
    public var addFifth: Bool
    /// Add an octave voice (+12 st, gain 0.4).
    public var addOctave: Bool
    /// Split each harmony voice into 3 detuned/delayed copies.
    public var choir: Bool

    public init(
        addThird: Bool = true,
        addFifth: Bool = true,
        addOctave: Bool = false,
        choir: Bool = false
    ) {
        self.addThird = addThird
        self.addFifth = addFifth
        self.addOctave = addOctave
        self.choir = choir
    }
}

// MARK: - Harmonizer

public enum PSOLAHarmonizer {

    static let f0MinHz = 60.0
    static let f0MaxHz = 500.0
    static let frameSec = 0.025
    static let hopSec = 0.010
    static let voicedConfidence = 0.6

    // MARK: - F0 tracking

    /// Autocorrelation F0 track: 25 ms frames every 10 ms, search range
    /// 60–500 Hz. `timeSec` is the frame center; `f0Hz` is 0 for
    /// unvoiced/silent frames; `confidence` is the normalized
    /// autocorrelation peak (0…1).
    public static func trackF0(
        _ input: [Float], sampleRate: Double
    ) -> [(timeSec: Double, f0Hz: Double, confidence: Double)] {
        trackF0Frames(input, sampleRate: sampleRate)
            .map { ($0.time, $0.f0, $0.conf) }
    }

    // MARK: - Pitch shifting

    /// Epoch-synchronous PSOLA pitch shift by `semitones`. Unvoiced
    /// segments (tracker confidence < 0.6) pass through unshifted; a
    /// signal with no voiced content is returned unchanged.
    public static func shift(
        _ input: [Float], sampleRate: Double, semitones: Double
    ) -> [Float] {
        guard !input.isEmpty else { return [] }
        guard abs(semitones) > 1e-3 else { return input }
        let track = trackF0Frames(input, sampleRate: sampleRate)
        let regions = voicedRegions(track: track, sampleCount: input.count, sampleRate: sampleRate)
        guard !regions.isEmpty else { return input }
        let shifts = [Double](repeating: semitones, count: regions.count)
        return renderShifted(
            input, sampleRate: sampleRate, track: track,
            regions: regions, semitonesPerRegion: shifts
        )
    }

    // MARK: - Harmonization

    /// Dry voice plus chord-aware harmony voices. `chordAt` maps a time
    /// in seconds to the active chord as MIDI note numbers (octave is
    /// ignored — the nearest octave transposition of each tone is
    /// considered). Output length equals the input length.
    public static func harmonize(
        _ input: [Float], sampleRate: Double,
        chordAt: (Double) -> [Int], settings: HarmonySettings
    ) -> [Float] {
        guard !input.isEmpty else { return [] }
        var out = input  // dry at unity
        let track = trackF0Frames(input, sampleRate: sampleRate)
        let regions = voicedRegions(track: track, sampleCount: input.count, sampleRate: sampleRate)
        guard !regions.isEmpty else { return out }

        var voices: [(nominal: Double, gain: Float)] = []
        if settings.addThird { voices.append((4, 0.7)) }
        if settings.addFifth { voices.append((7, 0.6)) }
        if settings.addOctave { voices.append((12, 0.4)) }

        for voice in voices {
            // One representative shift per contiguous voiced region:
            // nearest chord tone to dryPitch × nominal, clamped ±2 st.
            let shifts = regions.map { region -> Double in
                let dryMidi = 69 + 12 * log2(region.medianF0 / 440)
                let chord = chordAt(region.centerTime)
                guard !chord.isEmpty else { return voice.nominal }
                let targetMidi = dryMidi + voice.nominal
                var best = targetMidi
                var bestDist = Double.infinity
                for tone in chord {
                    let candidate = Double(tone)
                        + 12 * ((targetMidi - Double(tone)) / 12).rounded()
                    let dist = abs(candidate - targetMidi)
                    if dist < bestDist {
                        bestDist = dist
                        best = candidate
                    }
                }
                let shift = best - dryMidi
                return min(max(shift, voice.nominal - 2), voice.nominal + 2)
            }

            if settings.choir {
                let copies: [(detune: Double, delayMs: Double)] =
                    [(0, 0), (0.07, 15), (-0.07, 30)]
                let copyGain = voice.gain / Float(3.0.squareRoot())
                for copy in copies {
                    let rendered = renderShifted(
                        input, sampleRate: sampleRate, track: track,
                        regions: regions,
                        semitonesPerRegion: shifts.map { $0 + copy.detune }
                    )
                    let delay = Int(copy.delayMs * sampleRate / 1_000)
                    for i in delay..<out.count {
                        out[i] += copyGain * rendered[i - delay]
                    }
                }
            } else {
                let rendered = renderShifted(
                    input, sampleRate: sampleRate, track: track,
                    regions: regions, semitonesPerRegion: shifts
                )
                for i in out.indices {
                    out[i] += voice.gain * rendered[i]
                }
            }
        }
        return out
    }

    // MARK: - Internal: tracker

    struct F0Frame {
        var time: Double
        var f0: Double
        var conf: Double
        var start: Int  // frame start sample
    }

    struct VoicedRegion {
        var range: Range<Int>       // sample range
        var frames: Range<Int>      // indices into the track
        var medianF0: Double
        var centerTime: Double
    }

    static func trackF0Frames(_ input: [Float], sampleRate: Double) -> [F0Frame] {
        let n = input.count
        let frameLen = max(16, Int(frameSec * sampleRate))
        let hop = max(1, Int(hopSec * sampleRate))
        let minLag = max(2, Int(sampleRate / f0MaxHz))
        let maxLag = max(minLag + 2, Int(sampleRate / f0MinHz))
        guard n >= frameLen else { return [] }

        // Zero-pad so every frame can correlate out to maxLag.
        var padded = input
        padded.append(contentsOf: [Float](repeating: 0, count: maxLag + frameLen))

        // Prefix energy sums (Double) for O(1) window energies.
        var cum = [Double](repeating: 0, count: padded.count + 1)
        for i in 0..<padded.count {
            let v = Double(padded[i])
            cum[i + 1] = cum[i] + v * v
        }

        var frames: [F0Frame] = []
        var ncc = [Float](repeating: 0, count: maxLag + 1)

        var p = 0
        while p + frameLen <= n {
            let time = (Double(p) + Double(frameLen) / 2) / sampleRate
            let e0 = cum[p + frameLen] - cum[p]
            if e0 < 1e-9 {
                frames.append(F0Frame(time: time, f0: 0, conf: 0, start: p))
                p += hop
                continue
            }
            padded.withUnsafeBufferPointer { buf in
                let base = buf.baseAddress!
                for lag in minLag...maxLag {
                    var cross: Float = 0
                    vDSP_dotpr(base + p, 1, base + p + lag, 1, &cross, vDSP_Length(frameLen))
                    let eL = cum[p + lag + frameLen] - cum[p + lag]
                    let denom = (e0 * eL).squareRoot()
                    ncc[lag] = denom > 1e-12 ? Float(Double(cross) / denom) : 0
                }
            }

            var globalMax: Float = 0
            for lag in minLag...maxLag where ncc[lag] > globalMax { globalMax = ncc[lag] }
            guard globalMax > 0 else {
                frames.append(F0Frame(time: time, f0: 0, conf: 0, start: p))
                p += hop
                continue
            }

            // Smallest qualifying local maximum beats octave errors.
            var bestLag = 0
            let threshold = 0.9 * globalMax
            for lag in minLag...maxLag {
                let left = lag > minLag ? ncc[lag - 1] : -Float.infinity
                let right = lag < maxLag ? ncc[lag + 1] : -Float.infinity
                if ncc[lag] >= left && ncc[lag] >= right && ncc[lag] >= threshold {
                    bestLag = lag
                    break
                }
            }
            guard bestLag != 0 else {
                frames.append(F0Frame(time: time, f0: 0, conf: 0, start: p))
                p += hop
                continue
            }

            // Parabolic refinement for sub-sample lag.
            var lagF = Double(bestLag)
            if bestLag > minLag && bestLag < maxLag {
                let a = Double(ncc[bestLag - 1])
                let b = Double(ncc[bestLag])
                let c = Double(ncc[bestLag + 1])
                let denom = a - 2 * b + c
                if abs(denom) > 1e-12 {
                    let offset = 0.5 * (a - c) / denom
                    if abs(offset) < 1 { lagF += offset }
                }
            }
            let conf = Double(min(max(ncc[bestLag], 0), 1))
            frames.append(F0Frame(time: time, f0: sampleRate / lagF, conf: conf, start: p))
            p += hop
        }
        return frames
    }

    /// Maximal runs of consecutive voiced frames, as sample ranges.
    static func voicedRegions(
        track: [F0Frame], sampleCount: Int, sampleRate: Double
    ) -> [VoicedRegion] {
        let frameLen = max(16, Int(frameSec * sampleRate))
        var regions: [VoicedRegion] = []
        var i = 0
        while i < track.count {
            guard track[i].conf >= voicedConfidence && track[i].f0 > 0 else {
                i += 1
                continue
            }
            var j = i
            while j + 1 < track.count
                && track[j + 1].conf >= voicedConfidence && track[j + 1].f0 > 0 {
                j += 1
            }
            let start = track[i].start
            let end = min(sampleCount, track[j].start + frameLen)
            let f0s = track[i...j].map(\.f0).sorted()
            let median = f0s[f0s.count / 2]
            let center = (track[i].time + track[j].time) / 2
            regions.append(VoicedRegion(
                range: start..<end, frames: i..<(j + 1),
                medianF0: median, centerTime: center
            ))
            i = j + 1
        }
        return regions
    }

    // MARK: - Internal: PSOLA rendering

    /// Shift each voiced region by its semitone amount; unvoiced
    /// material passes through. Applies LPC formant preservation when
    /// any region shift reaches 3 st.
    static func renderShifted(
        _ input: [Float], sampleRate: Double, track: [F0Frame],
        regions: [VoicedRegion], semitonesPerRegion: [Double]
    ) -> [Float] {
        let preserveFormants = semitonesPerRegion.contains { abs($0) >= 3 }
        if preserveFormants {
            let (flat, envelopes) = formantPass(input, sampleRate: sampleRate, given: nil)
            let shifted = psolaRender(
                flat, sampleRate: sampleRate, track: track,
                regions: regions, semitonesPerRegion: semitonesPerRegion
            )
            return formantPass(shifted, sampleRate: sampleRate, given: envelopes).out
        }
        return psolaRender(
            input, sampleRate: sampleRate, track: track,
            regions: regions, semitonesPerRegion: semitonesPerRegion
        )
    }

    /// Core epoch-synchronous overlap-add.
    static func psolaRender(
        _ input: [Float], sampleRate: Double, track: [F0Frame],
        regions: [VoicedRegion], semitonesPerRegion: [Double]
    ) -> [Float] {
        let n = input.count
        var acc = [Float](repeating: 0, count: n)
        var wsum = [Float](repeating: 0, count: n)

        for (region, semitones) in zip(regions, semitonesPerRegion) {
            let ratio = pow(2.0, semitones / 12.0)
            let rs = region.range.lowerBound
            let re = region.range.upperBound

            // Interpolated period (samples) at an absolute sample
            // position, from this region's track frames.
            let frames = Array(track[region.frames])
            func periodAt(_ sample: Int) -> Double {
                let t = Double(sample) / sampleRate
                if t <= frames[0].time { return sampleRate / frames[0].f0 }
                if t >= frames[frames.count - 1].time {
                    return sampleRate / frames[frames.count - 1].f0
                }
                for k in 1..<frames.count where frames[k].time >= t {
                    let t0 = frames[k - 1].time
                    let t1 = frames[k].time
                    let mix = (t - t0) / max(t1 - t0, 1e-9)
                    let f0 = frames[k - 1].f0 + mix * (frames[k].f0 - frames[k - 1].f0)
                    return sampleRate / f0
                }
                return sampleRate / frames[frames.count - 1].f0
            }

            // Analysis epochs: seed one per period, snapped to the local
            // waveform maximum for a consistent phase.
            func argmax(_ lo: Int, _ hi: Int) -> Int {
                var best = lo
                for i in lo...hi where input[i] > input[best] { best = i }
                return best
            }
            var epochs: [Int] = []
            let t0 = periodAt(rs)
            let seedHi = min(rs + max(2, Int(t0)) - 1, n - 1)
            guard rs <= seedHi else { continue }
            var prev = argmax(rs, seedHi)
            epochs.append(prev)
            while true {
                let period = periodAt(prev)
                let predicted = prev + Int(period.rounded())
                if predicted >= re || predicted >= n { break }
                let slack = max(1, Int(period / 5))
                let lo = max(prev + 2, predicted - slack)
                let hi = min(n - 1, predicted + slack)
                if lo > hi { break }
                prev = argmax(lo, hi)
                epochs.append(prev)
            }
            guard epochs.count >= 2 else { continue }

            // Synthesis epochs at period/ratio spacing; each copies a
            // two-period Hann grain from the nearest analysis epoch.
            var s = Double(epochs[0])
            var ai = 0
            while s < Double(re) {
                while ai + 1 < epochs.count
                    && abs(Double(epochs[ai + 1]) - s) <= abs(Double(epochs[ai]) - s) {
                    ai += 1
                }
                let a = epochs[ai]
                let period = periodAt(a)
                let half = max(2, Int(period.rounded()))
                let center = Int(s.rounded())
                for j in -half...half {
                    let dst = center + j
                    guard dst >= 0 && dst < n else { continue }
                    let src = a + j
                    let sample = (src >= 0 && src < n) ? input[src] : 0
                    let w = Float(0.5 * (1 + cos(Double.pi * Double(j) / Double(half))))
                    acc[dst] += w * sample
                    wsum[dst] += w
                }
                s += period / ratio
            }
        }

        // Blend: PSOLA where the window sum has weight, dry elsewhere,
        // crossfaded through the taper zone.
        var out = [Float](repeating: 0, count: n)
        for i in 0..<n {
            let w = wsum[i]
            let dryWeight = min(max(1 - 2 * w, 0), 1)
            out[i] = acc[i] / max(w, 0.5) + dryWeight * input[i]
        }
        return out
    }

    // MARK: - Internal: formant flatten / restore

    static let formantFFT = 1_024
    static let formantHop = 256
    static let formantOrder = 20
    /// Envelope floor: caps the whitening boost at ~26 dB and makes
    /// flatten→restore an exact inverse pair.
    static let envelopeFloor: Float = 0.05

    /// One STFT pass. With `given == nil` it computes each frame's LPC
    /// envelope, divides it out (flatten) and returns the envelopes;
    /// with `given` envelopes it multiplies them back in (restore).
    static func formantPass(
        _ x: [Float], sampleRate: Double, given: [[Float]]?
    ) -> (out: [Float], envelopes: [[Float]]) {
        let n = formantFFT
        let half = n / 2
        let outLen = x.count
        guard outLen > 0 else { return ([], []) }
        guard
            let fwd = vDSP_DFT_zop_CreateSetup(nil, vDSP_Length(n), .FORWARD),
            let inv = vDSP_DFT_zop_CreateSetup(fwd, vDSP_Length(n), .INVERSE)
        else { return (x, given ?? []) }
        defer {
            vDSP_DFT_DestroySetup(fwd)
            vDSP_DFT_DestroySetup(inv)
        }

        var window = [Float](repeating: 0, count: n)
        for i in 0..<n {
            window[i] = Float(0.5 - 0.5 * cos(2.0 * Double.pi * Double(i) / Double(n)))
        }

        var frame = [Float](repeating: 0, count: n)
        let zeroIn = [Float](repeating: 0, count: n)
        var re = [Float](repeating: 0, count: n)
        var im = [Float](repeating: 0, count: n)
        var timeRe = [Float](repeating: 0, count: n)
        var timeIm = [Float](repeating: 0, count: n)
        var acc = [Float](repeating: 0, count: outLen + n)
        var wsum = [Float](repeating: 0, count: outLen + n)
        var envelopes: [[Float]] = []
        let invN = Float(1.0 / Double(n))

        var frameIndex = 0
        var p = 0
        while p < outLen {
            for i in 0..<n {
                let s = p + i
                frame[i] = s < outLen ? x[s] * window[i] : 0
            }

            var env: [Float]
            if let given {
                env = frameIndex < given.count
                    ? given[frameIndex]
                    : [Float](repeating: 1, count: half + 1)
            } else {
                env = FormantEstimator.lpcEnvelope(
                    frame, order: formantOrder,
                    binCount: half + 1, sampleRate: sampleRate
                )
                for k in env.indices { env[k] = max(env[k], envelopeFloor) }
                envelopes.append(env)
            }

            vDSP_DFT_Execute(fwd, frame, zeroIn, &re, &im)
            for k in 0...half {
                let factor = given == nil ? 1 / env[k] : env[k]
                re[k] *= factor
                im[k] *= factor
                if k > 0 && k < half {
                    re[n - k] = re[k]
                    im[n - k] = -im[k]
                }
            }
            im[0] = 0
            im[half] = 0
            vDSP_DFT_Execute(inv, re, im, &timeRe, &timeIm)

            for i in 0..<n {
                let w = window[i]
                acc[p + i] += timeRe[i] * invN * w
                wsum[p + i] += w * w
            }
            frameIndex += 1
            p += formantHop
        }

        var out = [Float](repeating: 0, count: outLen)
        for i in 0..<outLen {
            out[i] = acc[i] / max(wsum[i], 1e-6)
        }
        return (out, given ?? envelopes)
    }
}
