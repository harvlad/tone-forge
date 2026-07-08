// PadSynthParams.swift
//
// Live-tweakable knobs for the pad synth. Matches the launchpad.js
// slider set 1:1 so the mobile UI and the web UI expose the same
// vocabulary. Defaults are the launchpad.js schema defaults.
//
// Owned by ``PadSynth``; the ``SettingsView`` observes via
// @Published on the enclosing PadSynth and mutates through
// ``PadSynth.update(params:)`` so ramp coefficients are re-computed
// atomically inside the render lock.

import Foundation

/// Voice knobs. Value type so UI diffing is cheap and thread
/// hand-off is trivial. Reverb knobs moved to
/// `AudioEngine.ReverbParams` in the v2 shared-reverb topology
/// (D-013).
public struct PadSynthParams: Sendable, Equatable {
    /// Fixed pad-synth trim into the voice bus. 0.311 × the voice
    /// bus's 0.9 default lands on the long-standing 0.28 net gain
    /// (loudness-neutral migration, D-010/D-013). The user-facing
    /// level control is `voiceGainLinear` → `AudioEngine.setVoiceGain`.
    public var masterGain: Float = 0.311

    /// Multiplier on the per-voice lowpass cutoff. 1.0 = launchpad.js
    /// default; 0.5 → duller, 2.0 → glassier.
    public var brightness: Double = 1.0

    /// Delay between successive strummed voices when a chord is
    /// pressed. 0 ms = block chord.
    public var strumMs: Double = 15

    /// Amplitude envelope attack in ms.
    public var attackMs: Double = 6

    /// Amplitude envelope release in seconds. Also caps the filter
    /// sweep duration inside the voice.
    public var releaseSec: Double = 2.5

    /// Saw/triangle oscillator balance, 0…1. 0 = triangle only,
    /// 1 = saw only. 0.5 reproduces the original equal mix exactly
    /// (render normalizes so 0.5 is byte-identical to the historic
    /// `(saw + tri) * 0.55`).
    public var sawMix: Double = 0.5

    /// Oscillator detune spread in cents: saw at −detuneCents,
    /// triangle at +detuneCents. 6 = the historic hard-coded spread.
    public var detuneCents: Double = 6.0

    public init() {}

    public init(
        masterGain: Float = 0.311,
        brightness: Double = 1.0,
        strumMs: Double = 15,
        attackMs: Double = 6,
        releaseSec: Double = 2.5,
        sawMix: Double = 0.5,
        detuneCents: Double = 6.0
    ) {
        self.masterGain = masterGain
        self.brightness = brightness
        self.strumMs = strumMs
        self.attackMs = attackMs
        self.releaseSec = releaseSec
        self.sawMix = sawMix
        self.detuneCents = detuneCents
    }
}
