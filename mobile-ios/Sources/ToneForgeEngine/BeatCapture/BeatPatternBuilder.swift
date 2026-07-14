// BeatPatternBuilder.swift
//
// Beat Capture (D-024): assemble detected hits into an editable
// `SequencerPattern`. One track per role that fired, hits snapped to
// the step grid at the resolved BPM, velocity carried through.
//
// Step model limitation: the sequencer grid is fixed 16th-note
// resolution (see SequencerPattern.stepDuration). There is no
// sub-16th micro-timing, so `.keep` maps to the finest available
// grid (16th / up to 32 steps = 2 bars). True free-timing groove is
// a future enhancement (needs per-step micro-offset).

import Foundation

/// How performed hits are aligned to the step grid.
public enum BeatQuantize: String, Codable, CaseIterable, Sendable {
    /// Finest grid (16th). Closest the current model gets to "as played".
    case keep
    case sixteenth
    case eighth
    case quarter

    public var displayName: String {
        switch self {
        case .keep: return "Keep"
        case .sixteenth: return "1/16"
        case .eighth: return "1/8"
        case .quarter: return "1/4"
        }
    }

    /// Grid spacing in 16th-note steps.
    var stepStride: Int {
        switch self {
        case .keep, .sixteenth: return 1
        case .eighth: return 2
        case .quarter: return 4
        }
    }
}

public enum BeatPatternBuilder {

    /// Build a pattern from detected hits.
    /// - Parameters:
    ///   - songSynced: true when following a loaded song's tempo —
    ///     `bpmOverride` stays nil so playback tracks the song. false
    ///     (standalone) sets `bpmOverride = bpm`.
    public static func build(
        hits: [DetectedHit],
        bpm: Double,
        quantize: BeatQuantize,
        songSynced: Bool,
        name: String = "Beat Capture"
    ) -> SequencerPattern {
        let safeBPM = bpm > 0 ? bpm : 120
        let stepDur = 60.0 / safeBPM / 4.0  // 16th-note seconds

        // Longest hit time decides how many bars we need.
        let maxTime = hits.map(\.timeSec).max() ?? 0
        let neededSteps = stepDur > 0 ? Int((maxTime / stepDur).rounded(.up)) + 1 : 1
        let stepCount = resolveStepCount(neededSteps)
        let capacity = stepCount.rawValue

        var pattern = SequencerPattern(
            id: UUID(),
            name: name,
            stepCount: stepCount,
            bpmOverride: songSynced ? nil : safeBPM,
            tracks: [],
            swing: 0,
            isLooping: true
        )

        let stride = quantize.stepStride

        // Group hits by role, preserving role order (kick first).
        for role in DrumRole.allCases {
            let roleHits = hits.filter { $0.role == role }
            guard !roleHits.isEmpty else { continue }

            var track = SequencerTrack(
                chopRef: role.chopRef,
                stepCount: capacity,
                name: role.displayName
            )

            for hit in roleHits {
                guard stepDur > 0 else { continue }
                let rawStep = Int((hit.timeSec / stepDur).rounded())
                // Snap to the quantize grid.
                let snapped = (Int((Double(rawStep) / Double(stride)).rounded())) * stride
                guard snapped >= 0, snapped < capacity else { continue }
                // Keep the loudest hit if two land on the same step.
                let existing = track.steps[snapped].velocity
                if hit.velocity > existing {
                    track.setStepVelocity(at: snapped, velocity: hit.velocity)
                }
            }

            pattern.tracks.append(track)
        }

        return pattern
    }

    /// Smallest grid (8/16/32) that holds `neededSteps`.
    static func resolveStepCount(_ neededSteps: Int) -> PatternStepCount {
        if neededSteps <= 8 { return .eight }
        if neededSteps <= 16 { return .sixteen }
        return .thirtyTwo
    }
}
