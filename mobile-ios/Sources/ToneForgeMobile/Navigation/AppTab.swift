// AppTab.swift
//
// D-022 "Every mode. No scrolling.": the five top-level tabs. Each
// former Play-tab surface (D-019 PlaySurface) is now a first-class
// tab with its own focused screen; Chord Pads folds into Jam as a
// pad-mode toggle. Raw values persist in
// SampleSettingsStore.appTabRaw (with a one-time migration from the
// legacy playSurfaceRaw).
//
// TabModePolicy is the single place the tab choice maps onto the
// engine AppMode — RootView/AppState never switch on tabs themselves.

import Foundation
import ToneForgeEngine

/// The five top-level tabs.
///
/// `perform` keeps the raw value "contribute" so persisted
/// `appTabRaw` values (and the legacy PlaySurface migration) still
/// resolve after Contribute was reworked into the lean Perform
/// surface (its construction tools live under Jam now — see
/// InstrumentEditorView).
public enum AppTab: String, CaseIterable, Hashable {
    case learn, jam
    case perform = "contribute"
    case mixer, library

    var title: String {
        switch self {
        case .learn:   return "Learn"
        case .jam:     return "Jam"
        case .perform: return "Perform"
        case .mixer:   return "Mixer"
        case .library: return "Library"
        }
    }

    var systemImage: String {
        switch self {
        case .learn:   return "book.fill"
        case .jam:     return "pianokeys"
        case .perform: return "waveform"
        case .mixer:   return "slider.horizontal.3"
        case .library: return "music.note.list"
        }
    }

    /// Tabs that host a playing surface — the deep-link targets when
    /// Library activates a song while Mixer/Library is selected.
    var isPerformance: Bool {
        switch self {
        case .learn, .jam, .perform: return true
        case .mixer, .library:       return false
        }
    }

    /// Tabs that are meaningless without a loaded song: Learn needs a
    /// song to practise, Jam's chord-follow needs the song's chords.
    /// On cold launch (no song auto-reloads) we fall back to Library
    /// instead of restoring straight into an empty Learn/Jam surface.
    var requiresSong: Bool {
        switch self {
        case .learn, .jam:               return true
        case .perform, .mixer, .library: return false
        }
    }

    /// One-time migration from the legacy D-019 PlaySurface raw value
    /// ("learn" / "jam" / "contribute" / "chordPads"). Chord Pads
    /// folded into Jam (D-022); unknowns fall back to Perform (the
    /// ex-Contribute raw value).
    public static func migratedRaw(fromLegacyPlaySurface raw: String?) -> String {
        switch raw {
        case "learn", "jam", "contribute": return raw ?? ""
        case "chordPads":                  return AppTab.jam.rawValue
        default:                           return AppTab.perform.rawValue
        }
    }
}

/// Pure tab → engine-mode policy (D-022). Learn and Jam pin their
/// modes; Perform inherits Jam's instrument (.jamInKey) so the surface
/// built in Jam plays identically live; Mixer and Library are passive
/// surfaces that leave the engine mode untouched (so audio keeps
/// behaving while the user mixes or browses).
public enum TabModePolicy {
    public static func mode(
        for tab: AppTab,
        lastContributeModeRaw: String
    ) -> AppMode? {
        switch tab {
        case .learn:
            return .learnSong
        case .jam, .perform:
            // Perform reuses the Jam controllers + instrument state.
            // The deeper .sample construction grid is entered only
            // inside the Instrument Editor (see InstrumentEditorView).
            return .jamInKey
        case .mixer, .library:
            return nil
        }
    }
}
