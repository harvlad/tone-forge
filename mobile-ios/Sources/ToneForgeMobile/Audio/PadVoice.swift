// PadVoice.swift
//
// Single polyphonic voice used by ``PadSynth``. Value semantics on
// purpose — the synth keeps a fixed-size C array of these inside a
// class wrapper and mutates them in place from the audio thread.
//
// Voice topology mirrors launchpad.js `_launchpadPlayVoice`:
//   (saw @ -6ct + triangle @ +6ct) → biquad LP → gain env → panner
//
// The filter is a Direct Form II Transposed biquad. Coefficients are
// re-computed each block from the sweeping cutoff (fast enough at
// ~44 kHz / 512-sample blocks that we don't need per-sample updates).
//
// Envelope is a three-stage exponential to match the web's two-stage
// decay-then-tail shape (attack → peak, then peak → 0.45*peak, then
// → 0.0001 * total release). We generate it per-sample from smoothed
// targets so slider-driven attack/release changes on the next press
// take effect without discontinuities.

import Foundation

/// Per-voice runtime state. Non-Sendable — only touched from the
/// audio render thread once ``PadSynth.trigger`` copies incoming
/// values across the atomic queue.
struct PadVoice {

    // MARK: - Lifecycle

    /// True while the voice is emitting samples. Reset to false when
    /// the envelope has fully decayed so ``PadSynth`` can steal it.
    var isActive: Bool = false

    /// Sample offset relative to the current render block start when
    /// the voice should first produce a non-zero sample. Set on
    /// trigger so strummed chords can defer each voice without
    /// splitting the render call.
    var startFrameOffset: Int = 0

    // MARK: - Oscillators

    /// Phase 0..1 for the sawtooth oscillator (detuned -6 cents).
    var phaseSaw: Double = 0
    /// Phase 0..1 for the triangle oscillator (detuned +6 cents).
    var phaseTri: Double = 0
    /// Per-sample phase increment for the sawtooth (Hz / sampleRate).
    var incSaw: Double = 0
    /// Per-sample phase increment for the triangle.
    var incTri: Double = 0

    // MARK: - Filter (biquad LP, Direct Form II Transposed)

    var b0: Double = 0
    var b1: Double = 0
    var b2: Double = 0
    var a1: Double = 0
    var a2: Double = 0
    var z1: Double = 0
    var z2: Double = 0

    /// Cutoff at trigger time (Hz). Sweeps exponentially toward
    /// ``endCut`` over ``filterSweepSec``.
    var startCut: Double = 0
    /// Cutoff at end of sweep (Hz).
    var endCut: Double = 0
    /// Current cutoff — decayed each block.
    var curCut: Double = 0
    /// Total sweep duration in samples.
    var filterSweepSamples: Int = 0
    /// Samples elapsed since trigger.
    var samplesElapsed: Int = 0

    // MARK: - Envelope

    /// Peak amplitude (velocity-scaled).
    var envPeak: Float = 0
    /// Sustain level (0.45 * peak in launchpad.js).
    var envSustain: Float = 0
    /// Attack duration in samples.
    var attackSamples: Int = 0
    /// Time (in samples) from attack-end to sustain drop.
    var decayEndSample: Int = 0
    /// Total voice length in samples (release end).
    var totalSamples: Int = 0
    /// Current envelope value (per-sample smoothed).
    var envValue: Float = 0.0001

    // MARK: - Output

    /// Pan −1..+1. Applied via equal-power panning in the render loop.
    var pan: Float = 0

    // MARK: - Coefficient math

    /// Recompute biquad coefficients for a given cutoff and sample rate.
    /// Q is fixed at 4.5 (matches launchpad.js filt.Q.value = 4.5).
    mutating func updateBiquad(cutoffHz: Double, sampleRate: Double) {
        let q = 4.5
        let nyquist = sampleRate * 0.5
        let f = max(20.0, min(cutoffHz, nyquist * 0.98))
        let w0 = 2.0 * .pi * f / sampleRate
        let cw = cos(w0)
        let sw = sin(w0)
        let alpha = sw / (2.0 * q)

        let b0raw = (1.0 - cw) * 0.5
        let b1raw = 1.0 - cw
        let b2raw = (1.0 - cw) * 0.5
        let a0 = 1.0 + alpha
        let a1raw = -2.0 * cw
        let a2raw = 1.0 - alpha

        b0 = b0raw / a0
        b1 = b1raw / a0
        b2 = b2raw / a0
        a1 = a1raw / a0
        a2 = a2raw / a0
    }
}
