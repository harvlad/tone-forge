// VocoderCarriers.swift
//
// Pure, deterministic carrier builders for the SpectralVocoder modes
// (see VocoderMode). Each builder returns a mono Float buffer of
// exactly round(durationSec × sampleRate) samples, peak-normalized to
// −1 dBFS-ish (0.9 linear) — the vocoder whitens the carrier per band
// anyway, so absolute level only needs to be sane, not calibrated.
//
//   M1 classic  → sawStack(notes:)       band-limited polyBLEP saws
//   M2 song     → chordGrid(spans:)      piecewise saw stacks with
//                                        40 ms equal-power crossfades
//                                        at chord boundaries
//   M3 stem     → loopedStem(_:)         most-pitched ~1 s window of
//                                        stem audio (trackF0
//                                        confidence), loop-crossfaded
//   M5 texture  → texture(_:)            whole pad sample, loop-
//                                        crossfaded end-to-start
//
// Determinism: no RNG anywhere — oscillator phases start at 0, the
// stem window pick is an argmax with earliest-wins tie-breaking, and
// loop seams are fixed linear crossfades. Same inputs → bit-identical
// output.

import Foundation
import Accelerate

public enum VocoderCarriers {

    // MARK: - Tunables

    /// Fallback chord when a caller has no chord information: a C
    /// power-stack drone (C2, C3, G3, C4) — dense enough low end for
    /// the vocoder's 80 Hz bottom band, an octave+fifth for body.
    public static let defaultDroneNotes = [36, 48, 55, 60]

    /// Chord-boundary crossfade for the song carrier (plan: "40 ms
    /// downbeat crossfade").
    static let chordCrossfadeSec = 0.040
    /// Loop-seam crossfade for audio carriers (stem/texture).
    static let loopCrossfadeSec = 0.010
    /// Target window length for the stem carrier's pitched-region
    /// search.
    static let stemWindowSec = 1.0
    /// Peak-normalization target for every carrier.
    static let peakTarget: Float = 0.9

    // MARK: - M1: classic saw stack

    /// Band-limited saw drone at the given MIDI notes (empty →
    /// ``defaultDroneNotes``). Notes at or above Nyquist are skipped.
    public static func sawStack(
        notes: [Int], durationSec: Double, sampleRate: Double
    ) -> [Float] {
        let frames = frameCount(durationSec, sampleRate)
        guard frames > 0 else { return [] }
        var out = [Float](repeating: 0, count: frames)
        renderSawStack(
            notes: notes.isEmpty ? defaultDroneNotes : notes,
            into: &out, range: 0..<frames, sampleRate: sampleRate
        )
        normalizePeak(&out)
        return out
    }

    // MARK: - M2: chord-grid carrier

    /// One chord's reign over the carrier: `midiNotes` sound from
    /// `startSec` until the next span begins (the last span runs to
    /// the end). Empty `midiNotes` fall back to the default drone so
    /// the carrier never goes silent mid-take.
    public struct ChordSpan: Sendable, Equatable {
        public let startSec: Double
        public let midiNotes: [Int]
        public init(startSec: Double, midiNotes: [Int]) {
            self.startSec = startSec
            self.midiNotes = midiNotes
        }
    }

    /// Piecewise saw-stack carrier following a chord grid, with an
    /// equal-power crossfade centered on every chord boundary. The
    /// first span is extended back to t = 0 (a take usually starts
    /// mid-chord); no spans at all → plain default drone.
    public static func chordGrid(
        spans: [ChordSpan], durationSec: Double, sampleRate: Double
    ) -> [Float] {
        let frames = frameCount(durationSec, sampleRate)
        guard frames > 0 else { return [] }

        // Spans starting at/after the end of the take can never
        // sound; dropping them up front keeps the true last segment
        // un-faded at the buffer edge.
        let sorted = spans
            .filter { $0.startSec < durationSec }
            .sorted { $0.startSec < $1.startSec }
        guard !sorted.isEmpty else {
            return sawStack(notes: [], durationSec: durationSec,
                            sampleRate: sampleRate)
        }

        let fade = max(2, Int((chordCrossfadeSec * sampleRate).rounded()))
        var out = [Float](repeating: 0, count: frames)
        var segment = [Float]()

        for (idx, span) in sorted.enumerated() {
            // Segment body [start, end) in frames; first span pulled
            // back to 0, last span runs to the end.
            let start = idx == 0
                ? 0
                : min(frames, Int((span.startSec * sampleRate).rounded()))
            let end = idx + 1 < sorted.count
                ? min(frames,
                      Int((sorted[idx + 1].startSec * sampleRate).rounded()))
                : frames
            guard end > start else { continue }

            // Extend by half the fade on interior edges so adjacent
            // segments overlap by exactly `fade` frames.
            let extStart = idx == 0 ? start : max(0, start - fade / 2)
            let extEnd = idx + 1 < sorted.count
                ? min(frames, end + fade - fade / 2)
                : end
            let length = extEnd - extStart
            guard length > 0 else { continue }

            if segment.count != length {
                segment = [Float](repeating: 0, count: length)
            } else {
                for i in 0..<length { segment[i] = 0 }
            }
            renderSawStack(
                notes: span.midiNotes.isEmpty
                    ? defaultDroneNotes : span.midiNotes,
                into: &segment, range: 0..<length, sampleRate: sampleRate
            )

            // Equal-power ramps on interior edges (the two saw stacks
            // are uncorrelated, so sin/cos keeps power flat through
            // the fade).
            let rampIn = idx > 0 ? min(fade, length) : 0
            let rampOut = idx + 1 < sorted.count ? min(fade, length) : 0
            for i in 0..<rampIn {
                let g = sin(0.5 * .pi * Double(i) / Double(rampIn))
                segment[i] *= Float(g)
            }
            for i in 0..<rampOut {
                let g = cos(0.5 * .pi * Double(i) / Double(rampOut))
                segment[length - rampOut + i] *= Float(g)
            }

            for i in 0..<length { out[extStart + i] += segment[i] }
        }

        normalizePeak(&out)
        return out
    }

    // MARK: - M3: looped stem carrier

    /// Carrier from stem audio: pick the most tonally confident
    /// ~1 s window (mean PSOLA trackF0 confidence, earliest wins on
    /// ties) and loop it with a short crossfade to the requested
    /// duration. Empty source → silence of the requested length.
    public static func loopedStem(
        _ source: [Float], sampleRate: Double, durationSec: Double
    ) -> [Float] {
        let frames = frameCount(durationSec, sampleRate)
        guard frames > 0 else { return [] }
        guard !source.isEmpty else {
            return [Float](repeating: 0, count: frames)
        }

        let winLen = min(source.count,
                         max(1, Int(stemWindowSec * sampleRate)))
        var window: [Float]
        if source.count <= winLen {
            window = source
        } else {
            let start = bestPitchedWindowStart(
                source, sampleRate: sampleRate, windowLen: winLen
            )
            window = Array(source[start..<(start + winLen)])
        }

        var out = loopWithCrossfade(
            window, outFrames: frames,
            fadeFrames: Int((loopCrossfadeSec * sampleRate).rounded())
        )
        normalizePeak(&out)
        return out
    }

    // MARK: - M5: texture carrier

    /// Carrier from another pad's sample: the whole buffer, looped
    /// with a short crossfade to the requested duration. Empty source
    /// → silence of the requested length.
    public static func texture(
        _ source: [Float], sampleRate: Double, durationSec: Double
    ) -> [Float] {
        let frames = frameCount(durationSec, sampleRate)
        guard frames > 0 else { return [] }
        guard !source.isEmpty else {
            return [Float](repeating: 0, count: frames)
        }
        var out = loopWithCrossfade(
            source, outFrames: frames,
            fadeFrames: Int((loopCrossfadeSec * sampleRate).rounded())
        )
        normalizePeak(&out)
        return out
    }

    // MARK: - Saw synthesis (polyBLEP)

    /// Sum one polyBLEP saw per note into `out[range]`, each at
    /// 1/√count gain. polyBLEP knocks the aliased partials down far
    /// enough for a carrier that gets spectrally reshaped anyway.
    private static func renderSawStack(
        notes: [Int], into out: inout [Float],
        range: Range<Int>, sampleRate: Double
    ) {
        guard sampleRate > 0 else { return }
        let playable = notes.filter {
            let f = midiFrequency($0)
            return f > 0 && f < sampleRate * 0.5
        }
        guard !playable.isEmpty else { return }
        let gain = Float(1.0 / Double(playable.count).squareRoot())

        for note in playable {
            let dt = midiFrequency(note) / sampleRate
            var phase = 0.0
            for i in range {
                var v = 2.0 * phase - 1.0
                v -= polyBLEP(phase, dt)
                out[i] += Float(v) * gain
                phase += dt
                if phase >= 1 { phase -= 1 }
            }
        }
    }

    /// Two-sample polynomial band-limited step correction at the saw
    /// wrap discontinuity.
    private static func polyBLEP(_ t: Double, _ dt: Double) -> Double {
        if t < dt {
            let x = t / dt
            return x + x - x * x - 1
        }
        if t > 1 - dt {
            let x = (t - 1) / dt
            return x * x + x + x + 1
        }
        return 0
    }

    private static func midiFrequency(_ note: Int) -> Double {
        440.0 * pow(2.0, Double(note - 69) / 12.0)
    }

    // MARK: - Stem window selection

    /// Start index of the `windowLen`-sample window with the highest
    /// mean trackF0 confidence (voiced frames only; unvoiced count as
    /// 0). Candidates step by 100 ms; earliest wins ties, so the pick
    /// is fully deterministic.
    private static func bestPitchedWindowStart(
        _ source: [Float], sampleRate: Double, windowLen: Int
    ) -> Int {
        let frames = PSOLAHarmonizer.trackF0(source, sampleRate: sampleRate)
        guard !frames.isEmpty else { return 0 }

        let step = max(1, Int(0.1 * sampleRate))
        let lastStart = source.count - windowLen
        var bestStart = 0
        var bestScore = -Double.infinity

        var start = 0
        while start <= lastStart {
            let t0 = Double(start) / sampleRate
            let t1 = Double(start + windowLen) / sampleRate
            var sum = 0.0
            var count = 0
            for f in frames where f.timeSec >= t0 && f.timeSec < t1 {
                sum += f.f0Hz > 0 ? f.confidence : 0
                count += 1
            }
            let score = count > 0 ? sum / Double(count) : 0
            if score > bestScore {
                bestScore = score
                bestStart = start
            }
            if start == lastStart { break }
            start = min(start + step, lastStart)
        }
        return bestStart
    }

    // MARK: - Loop assembly

    /// Tile `window` to `outFrames` samples, overlapping consecutive
    /// copies by `fadeFrames` with linear ramps (linear, not
    /// equal-power, because the two sides of a loop seam are the SAME
    /// material — correlated — and equal-power would bump it +3 dB).
    /// The first copy's head is unfaded; the loop invariant places a
    /// successor under every in-range tail fade, so summed gain stays
    /// 1 everywhere.
    private static func loopWithCrossfade(
        _ window: [Float], outFrames: Int, fadeFrames: Int
    ) -> [Float] {
        let n = window.count
        guard n > 0, outFrames > 0 else {
            return [Float](repeating: 0, count: max(0, outFrames))
        }
        if n >= outFrames {
            return Array(window[0..<outFrames])
        }

        let fade = max(1, min(fadeFrames, n / 2))
        var out = [Float](repeating: 0, count: outFrames + n)
        var pos = 0
        var first = true
        while pos < outFrames {
            for i in 0..<n {
                var g: Float = 1
                if !first && i < fade {
                    g = Float(i) / Float(fade)
                }
                if i >= n - fade {
                    g *= Float(n - i) / Float(fade)
                }
                out[pos + i] += window[i] * g
            }
            first = false
            pos += n - fade
        }
        return Array(out[0..<outFrames])
    }

    // MARK: - Shared helpers

    private static func frameCount(
        _ durationSec: Double, _ sampleRate: Double
    ) -> Int {
        guard durationSec > 0, sampleRate > 0 else { return 0 }
        return Int((durationSec * sampleRate).rounded())
    }

    /// Scale so the absolute peak lands on ``peakTarget``. Silence is
    /// left untouched.
    private static func normalizePeak(_ x: inout [Float]) {
        guard !x.isEmpty else { return }
        var peak: Float = 0
        vDSP_maxmgv(x, 1, &peak, vDSP_Length(x.count))
        guard peak > 0 else { return }
        var scale = peakTarget / peak
        vDSP_vsmul(x, 1, &scale, &x, 1, vDSP_Length(x.count))
    }
}
