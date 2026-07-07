// VocoderMode.swift
//
// The five capture modes of the P5 vocoder/harmony pipeline. Raw
// values are the integers persisted in `PadSampleMetadata.vocoderMode`
// (Int?), so renumbering is a schema break — append only.
//
// M1–M3 and M5 run the mic take through SpectralVocoder against a
// mode-specific carrier (see VocoderCarriers); M4 bypasses the
// vocoder entirely and runs PSOLAHarmonizer instead. All five are
// capture-only: process on stop, save as a `.vocoded` local sample
// (neverUpload), assign to a pad. There is no persistent live
// vocoder path.

import Foundation

public enum VocoderMode: Int, Codable, CaseIterable, Sendable {
    /// M1 — classic robot: band-limited saw-stack drone carrier.
    case classic = 1
    /// M2 — song: carrier follows the loaded song's chord grid with
    /// 40 ms crossfades at chord boundaries.
    case song = 2
    /// M3 — stem: carrier is a looped window of song-stem audio,
    /// chosen by pitch confidence (autocorrelation via trackF0).
    case stem = 3
    /// M4 — harmony: PSOLA chord-aware harmonization (no vocoder).
    case harmony = 4
    /// M5 — texture: carrier is another pad's sample, looped
    /// (pad × pad cross-synthesis).
    case texture = 5

    /// True for the modes that feed SpectralVocoder; false for M4,
    /// which routes through PSOLAHarmonizer.
    public var usesSpectralVocoder: Bool { self != .harmony }

    public var displayName: String {
        switch self {
        case .classic: return "Classic"
        case .song:    return "Song Chords"
        case .stem:    return "Stem"
        case .harmony: return "Harmony"
        case .texture: return "Texture"
        }
    }

    /// One-line description for the mode picker UI.
    public var blurb: String {
        switch self {
        case .classic: return "Robot voice over a synth drone"
        case .song:    return "Voice follows the song's chords"
        case .stem:    return "Sing through the song's own audio"
        case .harmony: return "Adds chord-aware harmony voices"
        case .texture: return "Sing through another pad's sample"
        }
    }
}
