// SamplePadEffects.swift
//
// Per-pad realtime effect parameters — a delay unit and a resonant
// lowpass filter. Applied to a voice slot at trigger time in
// `SampleVoicePool.trigger(...)` so every voice inherits the pad's
// current effect settings for the lifetime of that trigger.
//
// Three sources feed the effective effect params at trigger time,
// evaluated in priority order:
//   1. User override (persisted per (packId, padIdx) in
//      SampleSettingsStore.padEffectsByKey).
//   2. Manifest baseline (SamplePad.effects — optional, most packs
//      leave it nil).
//   3. `.neutral` — a fully-open filter + zero-mix delay so the pad
//      sounds untouched.
//
// Value ranges are chosen to match `AVAudioUnitDelay` and
// `AVAudioUnitEQ` API conventions on iOS so the audio-graph mapping
// stays 1:1 with no scaling maths inside the voice pool:
//   * delayTimeSec:   0..2 (AVAudioUnitDelay.delayTime range).
//   * delayFeedback:  0..95 percent (below 100 to avoid runaway).
//   * delayMix:       0..100 percent (dry/wet).
//   * filterCutoffHz: 100..20_000 (audible range; 20 kHz = open).
//   * filterResonanceDb: 0..24 dB (AVAudioUnitEQ bandwidth for
//     resonantLowPass).
//
// LOCK-IN: this schema participates in the SamplePack manifest
// (SamplePad.effects). Any change must bump `SamplePack.manifestVersion`
// so cached manifests remain readable.

import Foundation

/// Per-pad delay + filter parameter set. Codable so it can live
/// inside SamplePad manifests and inside the user-override JSON blob
/// persisted by SampleSettingsStore.
public struct SamplePadEffects: Codable, Sendable, Equatable, Hashable {
    /// Delay time in seconds, 0..2. AVAudioUnitDelay clamps outside
    /// this range so we mirror the same window in our validator.
    public var delayTimeSec: Double
    /// Feedback percent, 0..95. Values ≥ 100 self-oscillate; we cap
    /// below to keep the delay stable regardless of user drag speed.
    public var delayFeedback: Double
    /// Dry/wet mix percent, 0..100. 0 = fully dry (delay silent);
    /// 100 = fully wet.
    public var delayMix: Double
    /// Lowpass cutoff Hz, 100..20_000. 20 kHz reads as "off" because
    /// the filter passes the full audible band.
    public var filterCutoffHz: Double
    /// Resonance in dB, 0..24. Applied as `bandwidth` on the
    /// resonant-lowpass EQ band — higher values produce a peakier
    /// response at cutoff.
    public var filterResonanceDb: Double

    public init(
        delayTimeSec: Double,
        delayFeedback: Double,
        delayMix: Double,
        filterCutoffHz: Double,
        filterResonanceDb: Double
    ) {
        self.delayTimeSec = delayTimeSec
        self.delayFeedback = delayFeedback
        self.delayMix = delayMix
        self.filterCutoffHz = filterCutoffHz
        self.filterResonanceDb = filterResonanceDb
    }

    /// Fully-open filter + silent delay. Voices that pick this up
    /// sound bit-identical to the pre-effects path.
    public static let neutral = SamplePadEffects(
        delayTimeSec: 0.25,
        delayFeedback: 0,
        delayMix: 0,
        filterCutoffHz: 20_000,
        filterResonanceDb: 0
    )

    /// Clamp every field into its documented range. Called by the
    /// voice pool before pushing values onto the AU nodes so a bad
    /// persisted value can't crash `AVAudioUnitDelay.delayTime`
    /// (which asserts on out-of-range input).
    public func clamped() -> SamplePadEffects {
        SamplePadEffects(
            delayTimeSec: max(0, min(2, delayTimeSec)),
            delayFeedback: max(0, min(95, delayFeedback)),
            delayMix: max(0, min(100, delayMix)),
            filterCutoffHz: max(100, min(20_000, filterCutoffHz)),
            filterResonanceDb: max(0, min(24, filterResonanceDb))
        )
    }

    /// True iff every field equals its neutral counterpart within a
    /// hairline epsilon — used by the effects editor to draw the
    /// pad's "clean" indicator.
    public var isNeutral: Bool {
        let n = SamplePadEffects.neutral
        let eps = 1e-6
        return abs(delayTimeSec - n.delayTimeSec) < eps
            && abs(delayFeedback - n.delayFeedback) < eps
            && abs(delayMix - n.delayMix) < eps
            && abs(filterCutoffHz - n.filterCutoffHz) < eps
            && abs(filterResonanceDb - n.filterResonanceDb) < eps
    }

    // Custom Codable so absent JSON keys degrade to `.neutral`
    // instead of failing decode. Old manifests + user overrides
    // written before a field existed still round-trip cleanly.
    private enum CodingKeys: String, CodingKey {
        case delayTimeSec, delayFeedback, delayMix,
             filterCutoffHz, filterResonanceDb
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let n = SamplePadEffects.neutral
        self.delayTimeSec = try c.decodeIfPresent(Double.self, forKey: .delayTimeSec) ?? n.delayTimeSec
        self.delayFeedback = try c.decodeIfPresent(Double.self, forKey: .delayFeedback) ?? n.delayFeedback
        self.delayMix = try c.decodeIfPresent(Double.self, forKey: .delayMix) ?? n.delayMix
        self.filterCutoffHz = try c.decodeIfPresent(Double.self, forKey: .filterCutoffHz) ?? n.filterCutoffHz
        self.filterResonanceDb = try c.decodeIfPresent(Double.self, forKey: .filterResonanceDb) ?? n.filterResonanceDb
    }
}
