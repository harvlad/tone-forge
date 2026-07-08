// SynthPresets.swift
//
// Named PadSynthParams bundles for the Jam / Learn / Chord Pads
// surfaces (redesign Phase 6). "Dreamy Lead" is byte-identical to the
// PadSynthParams defaults, so selecting it (or never touching the
// preset picker) leaves the historic pad sound untouched.
//
// masterGain stays at the fixed 0.311 bus trim on every preset —
// loudness lives on the voice-bus fader (D-010/D-013), not here.
// Octave shift is deliberately NOT part of a preset: callers apply it
// through ChordVoicing.midiNotes(octaveShift:).

import Foundation

/// One selectable synth sound. Identified by a stable string id so
/// settings stores can persist the selection across catalog reorders.
public struct SynthPreset: Identifiable, Sendable, Equatable {
    public let id: String
    public let name: String
    public let params: PadSynthParams

    public init(id: String, name: String, params: PadSynthParams) {
        self.id = id
        self.name = name
        self.params = params
    }
}

public enum SynthPresetCatalog {

    /// The default sound — exactly PadSynthParams().
    public static let dreamyLead = SynthPreset(
        id: "dreamyLead",
        name: "Dreamy Lead",
        params: PadSynthParams()
    )

    /// Slow-blooming, dark, triangle-heavy wash.
    public static let warmPad = SynthPreset(
        id: "warmPad",
        name: "Warm Pad",
        params: PadSynthParams(
            brightness: 0.6,
            strumMs: 25,
            attackMs: 120,
            releaseSec: 4.0,
            sawMix: 0.35,
            detuneCents: 10
        )
    )

    /// Instant attack, short tail, saw-forward bite.
    public static let pluck = SynthPreset(
        id: "pluck",
        name: "Pluck",
        params: PadSynthParams(
            brightness: 1.4,
            strumMs: 8,
            attackMs: 1,
            releaseSec: 0.6,
            sawMix: 0.7,
            detuneCents: 3
        )
    )

    /// Glassy triangle-dominant shimmer with a long ring.
    public static let crystalBell = SynthPreset(
        id: "crystalBell",
        name: "Crystal Bell",
        params: PadSynthParams(
            brightness: 2.2,
            strumMs: 12,
            attackMs: 2,
            releaseSec: 3.0,
            sawMix: 0.2,
            detuneCents: 4
        )
    )

    /// Dark, tight, near-block chords; pair with a low octave shift.
    public static let deepBass = SynthPreset(
        id: "deepBass",
        name: "Deep Bass",
        params: PadSynthParams(
            brightness: 0.45,
            strumMs: 0,
            attackMs: 4,
            releaseSec: 1.2,
            sawMix: 0.85,
            detuneCents: 2
        )
    )

    /// Display order for pickers. First entry is the default.
    public static let all: [SynthPreset] = [
        dreamyLead, warmPad, pluck, crystalBell, deepBass,
    ]

    public static let defaultPreset = dreamyLead

    /// Lookup by persisted id; nil for unknown ids (callers fall back
    /// to ``defaultPreset``).
    public static func preset(id: String) -> SynthPreset? {
        all.first { $0.id == id }
    }
}
