// StyleBeats.swift  (ToneForgeEngine)
//
// Predefined full drum beats per style — the GarageBand-Drummer move:
// pick a style, get a complete, musical groove at the song's tempo
// instantly (bpmOverride nil = song-synced), instead of programming
// steps from zero. Patterns play through the bundled BeatKit one-shots
// (kick/snare/hats/clap via BeatKit.chopRef), so they work on both
// platforms through the existing sequencer engines.
//
// IDs are DETERMINISTIC (fixed UUID strings) so re-loading a style
// replaces the stored pattern instead of piling up duplicates
// (SequencerPatternStore.save is idempotent by id).
//
// Velocity language: 1.0 accent · 0.8 normal · 0.4 ghost.

import Foundation

/// A beat style the user can drop in with one tap.
public enum BeatStyle: String, CaseIterable, Identifiable, Sendable {
    case house
    case boomBap
    case trap
    case dnb
    case rock

    public var id: String { rawValue }

    public var displayName: String {
        switch self {
        case .house:   return "House"
        case .boomBap: return "Boom Bap"
        case .trap:    return "Trap"
        case .dnb:     return "Drum & Bass"
        case .rock:    return "Rock"
        }
    }

    /// Stable pattern id per style (idempotent store saves).
    var patternId: UUID {
        switch self {
        case .house:   return UUID(uuidString: "B0000000-0000-4000-8000-000000000001")!
        case .boomBap: return UUID(uuidString: "B0000000-0000-4000-8000-000000000002")!
        case .trap:    return UUID(uuidString: "B0000000-0000-4000-8000-000000000003")!
        case .dnb:     return UUID(uuidString: "B0000000-0000-4000-8000-000000000004")!
        case .rock:    return UUID(uuidString: "B0000000-0000-4000-8000-000000000005")!
        }
    }
}

public enum StyleBeats {

    /// The complete 16-step pattern for `style`, at song tempo
    /// (bpmOverride nil), looping.
    public static func pattern(_ style: BeatStyle) -> SequencerPattern {
        let tracks: [SequencerTrack]
        var swing: Float = 0

        switch style {
        case .house:
            // Four-on-the-floor: kick every quarter, clap on 2 & 4,
            // closed hats on 8ths, open hat on the off-beats.
            tracks = [
                track(.kick,      [0: 1.0, 4: 1.0, 8: 1.0, 12: 1.0]),
                track(.clap,      [4: 0.9, 12: 0.9]),
                track(.closedHat, [0: 0.6, 2: 0.8, 4: 0.6, 6: 0.8,
                                   8: 0.6, 10: 0.8, 12: 0.6, 14: 0.8]),
                track(.openHat,   [2: 0.8, 6: 0.8, 10: 0.8, 14: 0.8]),
            ]

        case .boomBap:
            // Classic head-nod: syncopated kick, cracking 2 & 4 snare,
            // swung 8th hats with a ghost snare pickup.
            swing = 0.14
            tracks = [
                track(.kick,      [0: 1.0, 7: 0.8, 10: 0.9]),
                track(.snare,     [4: 1.0, 12: 1.0, 15: 0.4]),
                track(.closedHat, [0: 0.8, 2: 0.6, 4: 0.8, 6: 0.6,
                                   8: 0.8, 10: 0.6, 12: 0.8, 14: 0.6]),
            ]

        case .trap:
            // Half-time: snare on 3, rolling 16th hats with accents,
            // open hat lift before the loop.
            tracks = [
                track(.kick,      [0: 1.0, 6: 0.9, 10: 0.85]),
                track(.snare,     [8: 1.0]),
                track(.closedHat, [0: 0.9, 1: 0.4, 2: 0.6, 3: 0.4,
                                   4: 0.9, 5: 0.4, 6: 0.6, 7: 0.4,
                                   8: 0.9, 9: 0.4, 10: 0.6, 11: 0.4,
                                   12: 0.9, 13: 0.6, 14: 0.8, 15: 0.8]),
                track(.openHat,   [14: 0.9]),
            ]

        case .dnb:
            // Two-step: kick 1 and the "and" of 3, snares on 2 & 4,
            // light 16th hat shuffle.
            tracks = [
                track(.kick,      [0: 1.0, 10: 0.9]),
                track(.snare,     [4: 1.0, 12: 1.0]),
                track(.closedHat, [0: 0.6, 2: 0.5, 3: 0.35, 4: 0.6,
                                   6: 0.5, 8: 0.6, 10: 0.5, 11: 0.35,
                                   12: 0.6, 14: 0.5]),
            ]

        case .rock:
            // Driving 8ths: kick 1 and 3 (+pickup), backbeat snare,
            // solid closed hats.
            tracks = [
                track(.kick,      [0: 1.0, 6: 0.7, 8: 1.0]),
                track(.snare,     [4: 1.0, 12: 1.0]),
                track(.closedHat, [0: 0.9, 2: 0.7, 4: 0.9, 6: 0.7,
                                   8: 0.9, 10: 0.7, 12: 0.9, 14: 0.7]),
            ]
        }

        return SequencerPattern(
            id: style.patternId,
            name: "\(style.displayName) Beat",
            stepCount: .sixteen,
            bpmOverride: nil,          // song tempo
            tracks: tracks,
            swing: swing,
            isLooping: true
        )
    }

    /// One 16-step track for a BeatKit role with velocities per step.
    private static func track(
        _ role: DrumRole, _ hits: [Int: Float]
    ) -> SequencerTrack {
        var t = SequencerTrack(
            chopRef: BeatKit.chopRef(for: role),
            stepCount: 16,
            name: role.displayName
        )
        for (step, velocity) in hits where step >= 0 && step < 16 {
            t.steps[step] = SequencerStep(velocity: velocity)
        }
        return t
    }
}
