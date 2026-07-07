// AppMode.swift
//
// The seven contribution modes. Replaces both the old PlayView
// Learn/Jam/Contribute picker (v1 PerformMode) AND the standalone
// Sketch tab (D-016: sketch = `.sample` mode without a song — the
// grid context degrades to SketchSettingsStore's synthetic tempo
// grid; no separate mode needed).
//
// Only `.sample` and `.hybrid` are implemented in v2; the other five
// appear in the mode menu as disabled "Coming soon" entries so the
// information architecture is visible from day one and session JSON
// (P6) can already carry their raw values.

import Foundation

public enum AppMode: String, CaseIterable, Codable, Sendable {
    /// 8×8 grid, every pad a sample (pack pads + local mic/vocoded
    /// samples). Works with or without a song loaded.
    case sample
    /// Rows 5–8 samples, rows 1–4 synth notes from the song's
    /// key/chord grid (chord tones bright).
    case hybrid
    /// FUTURE: guided section-by-section song learning.
    case learnSong
    /// FUTURE: full-song performance with section gating.
    case performSong
    /// FUTURE: harmony-line practice against the stem.
    case learnHarmony
    /// FUTURE: free jam constrained to the song's key.
    case jamInKey
    /// FUTURE: melody transcription/echo mode.
    case melody

    /// Whether the mode is playable in this build. Drives the
    /// "Coming soon" disable state in the mode menu.
    public var isImplemented: Bool {
        self == .sample || self == .hybrid
    }

    /// Menu label.
    public var displayName: String {
        switch self {
        case .sample:       return "Sample"
        case .hybrid:       return "Hybrid"
        case .learnSong:    return "Learn Song"
        case .performSong:  return "Perform Song"
        case .learnHarmony: return "Learn Harmony"
        case .jamInKey:     return "Jam in Key"
        case .melody:       return "Melody"
        }
    }
}
