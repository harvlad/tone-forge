// DrumRole.swift
//
// Beat Capture (D-024): drum roles a percussive onset can be
// classified into. Classification is by *sound character/role*, not
// by physical source — a table thump and a chest hit both map to
// `.kick`; a thigh slap and a "psh" both map toward `.snare`.
//
// Each role has a stable `padIdx` into the bundled `beatkit` pack
// and a `chopRef` the sequencer triggers. Wire values are frozen for
// pattern persistence.

import Foundation

/// A percussive role the beat classifier can assign to an onset.
public enum DrumRole: String, Codable, CaseIterable, Sendable {
    case kick
    case snare
    case closedHat = "closed_hat"
    case openHat = "open_hat"
    case clap
    case rim
    case perc

    /// Human-facing label (review UI, e.g. "Closed Hat ×23").
    public var displayName: String {
        switch self {
        case .kick: return "Kick"
        case .snare: return "Snare"
        case .closedHat: return "Closed Hat"
        case .openHat: return "Open Hat"
        case .clap: return "Clap"
        case .rim: return "Rim"
        case .perc: return "Perc"
        }
    }

    /// Pad index within the `beatkit` pack (frozen ordering).
    public var padIdx: Int {
        switch self {
        case .kick: return 0
        case .snare: return 1
        case .closedHat: return 2
        case .openHat: return 3
        case .clap: return 4
        case .rim: return 5
        case .perc: return 6
        }
    }

    /// The sequencer reference triggered for this role.
    public var chopRef: ChopReference {
        BeatKit.chopRef(for: self)
    }
}

/// Resolves `DrumRole` to a concrete pack pad.
///
/// MVP ships without dedicated `beatkit` audio, so roles fall back to
/// the bundled `starter` percussion pads (kick=4, snare=5, hat=6,
/// tom=7). When the 7-piece `beatkit` audio lands, flip `useBeatKit`
/// and the sequencer picks up the proper kit with no pattern changes
/// (patterns store roles indirectly via this single seam).
public enum BeatKit {
    /// Bundled pack identifier for the dedicated kit.
    public static let packId = "beatkit"

    /// Fallback pack used until `beatkit` audio ships.
    static let fallbackPackId = "starter"

    /// Flip to `true` once `beatkit` audio assets are bundled.
    static let useBeatKit = true

    public static func chopRef(for role: DrumRole) -> ChopReference {
        if useBeatKit {
            return .packPad(packId: packId, padIdx: role.padIdx)
        }
        return .packPad(packId: fallbackPackId, padIdx: fallbackPadIdx(for: role))
    }

    /// Nearest starter-kit pad for a role (kick=4, snare=5, hat=6,
    /// tom=7). Roles without a dedicated starter pad borrow the
    /// closest match: clap/rim → snare, openHat → hat, perc → tom.
    static func fallbackPadIdx(for role: DrumRole) -> Int {
        switch role {
        case .kick: return 4
        case .snare: return 5
        case .clap: return 5
        case .rim: return 5
        case .closedHat: return 6
        case .openHat: return 6
        case .perc: return 7
        }
    }
}
