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
public enum AppTab: String, CaseIterable, Hashable {
    case learn, jam, contribute, mixer, library

    var title: String {
        switch self {
        case .learn:      return "Learn"
        case .jam:        return "Jam"
        case .contribute: return "Contribute"
        case .mixer:      return "Mixer"
        case .library:    return "Library"
        }
    }

    var systemImage: String {
        switch self {
        case .learn:      return "book.fill"
        case .jam:        return "pianokeys"
        case .contribute: return "square.grid.3x3.fill"
        case .mixer:      return "slider.horizontal.3"
        case .library:    return "music.note.list"
        }
    }

    /// Tabs that host a playing surface — the deep-link targets when
    /// Library activates a song while Mixer/Library is selected.
    var isPerformance: Bool {
        switch self {
        case .learn, .jam, .contribute: return true
        case .mixer, .library:          return false
        }
    }

    /// One-time migration from the legacy D-019 PlaySurface raw value
    /// ("learn" / "jam" / "contribute" / "chordPads"). Chord Pads
    /// folded into Jam (D-022); unknowns fall back to Contribute.
    public static func migratedRaw(fromLegacyPlaySurface raw: String?) -> String {
        switch raw {
        case "learn", "jam", "contribute": return raw ?? ""
        case "chordPads":                  return AppTab.jam.rawValue
        default:                           return AppTab.contribute.rawValue
        }
    }
}

/// Pure tab → engine-mode policy (D-022). Learn and Jam pin their
/// modes; Contribute restores the last sample/hybrid grid the user
/// was in; Mixer and Library are passive surfaces that leave the
/// engine mode untouched (so audio keeps behaving while the user
/// mixes or browses).
public enum TabModePolicy {
    public static func mode(
        for tab: AppTab,
        lastContributeModeRaw: String
    ) -> AppMode? {
        switch tab {
        case .learn:
            return .learnSong
        case .jam:
            return .jamInKey
        case .contribute:
            if let last = AppMode(rawValue: lastContributeModeRaw),
               last == .sample || last == .hybrid {
                return last
            }
            return .sample
        case .mixer, .library:
            return nil
        }
    }
}
