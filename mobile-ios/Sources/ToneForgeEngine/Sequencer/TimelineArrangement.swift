// TimelineArrangement.swift
//
// Data model for DAW-style timeline arrangement (D-023 Phase 5).
// Clips are beat-anchored regions that trigger chops at specific
// song positions. Unlike patterns (which loop), arrangements play
// linearly through the song.
//
// Timeline clips are quantized to beat boundaries for clean
// arrangement. Each clip references a ChopReference and spans
// a duration in beats.

import Foundation

// MARK: - Timeline Clip

/// A single clip on the timeline.
public struct TimelineClip: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    /// What sound to trigger.
    public var chopRef: ChopReference
    /// Start position in beats (from song start).
    public var startBeat: Double
    /// Duration in beats.
    public var durationBeats: Double
    /// Velocity for this clip (0–1).
    public var velocity: Float
    /// Track/lane index (for vertical positioning).
    public var track: Int
    /// Display name override.
    public var name: String?

    public init(
        id: UUID = UUID(),
        chopRef: ChopReference,
        startBeat: Double,
        durationBeats: Double = 1.0,
        velocity: Float = 1.0,
        track: Int = 0,
        name: String? = nil
    ) {
        self.id = id
        self.chopRef = chopRef
        self.startBeat = startBeat
        self.durationBeats = durationBeats
        self.velocity = velocity.clamped(to: 0...1)
        self.track = track
        self.name = name
    }

    /// End position in beats.
    public var endBeat: Double {
        startBeat + durationBeats
    }

    /// Move clip to new start position.
    public mutating func move(to newStartBeat: Double) {
        startBeat = max(0, newStartBeat)
    }

    /// Resize clip duration.
    public mutating func resize(to newDuration: Double) {
        durationBeats = max(0.25, newDuration) // Minimum 1/4 beat
    }

    /// Convert beat position to seconds at given BPM.
    public func startSeconds(bpm: Double) -> Double {
        (startBeat / bpm) * 60.0
    }

    public func endSeconds(bpm: Double) -> Double {
        (endBeat / bpm) * 60.0
    }

    public func durationSeconds(bpm: Double) -> Double {
        (durationBeats / bpm) * 60.0
    }
}

// MARK: - Timeline Arrangement

/// A collection of clips arranged on a timeline.
public struct TimelineArrangement: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    /// Analysis ID this arrangement belongs to.
    public var analysisId: String
    /// Name for display.
    public var name: String
    /// All clips in the arrangement.
    public var clips: [TimelineClip]
    /// Number of tracks/lanes for vertical organization.
    public var trackCount: Int
    /// Total length in beats (for loop region, if applicable).
    public var lengthBeats: Double?

    public init(
        id: UUID = UUID(),
        analysisId: String,
        name: String = "Arrangement 1",
        clips: [TimelineClip] = [],
        trackCount: Int = 4,
        lengthBeats: Double? = nil
    ) {
        self.id = id
        self.analysisId = analysisId
        self.name = name
        self.clips = clips
        self.trackCount = trackCount
        self.lengthBeats = lengthBeats
    }

    // MARK: - Clip Management

    /// Add a clip to the arrangement.
    public mutating func addClip(_ clip: TimelineClip) {
        clips.append(clip)
    }

    /// Remove a clip by ID.
    public mutating func removeClip(id: UUID) {
        clips.removeAll { $0.id == id }
    }

    /// Update a clip in place.
    public mutating func updateClip(_ clip: TimelineClip) {
        if let idx = clips.firstIndex(where: { $0.id == clip.id }) {
            clips[idx] = clip
        }
    }

    /// Get clips that should trigger at or after the given beat.
    public func clips(atOrAfter beat: Double) -> [TimelineClip] {
        clips.filter { $0.startBeat >= beat }
            .sorted { $0.startBeat < $1.startBeat }
    }

    /// Get clips that overlap the given beat range.
    public func clips(in range: ClosedRange<Double>) -> [TimelineClip] {
        clips.filter { clip in
            clip.startBeat < range.upperBound && clip.endBeat > range.lowerBound
        }
    }

    /// Get clips on a specific track.
    public func clips(onTrack track: Int) -> [TimelineClip] {
        clips.filter { $0.track == track }
            .sorted { $0.startBeat < $1.startBeat }
    }

    /// Total duration in beats (end of last clip).
    public var totalBeats: Double {
        clips.map(\.endBeat).max() ?? 0
    }

    /// Total duration in seconds at given BPM.
    public func totalSeconds(bpm: Double) -> Double {
        (totalBeats / bpm) * 60.0
    }

    // MARK: - Quantization

    /// Snap a beat position to the given grid (e.g., 0.25 for 16th notes).
    public static func quantize(_ beat: Double, grid: Double) -> Double {
        guard grid > 0 else { return beat }
        return (beat / grid).rounded() * grid
    }
}

// MARK: - Helpers

private extension Float {
    func clamped(to range: ClosedRange<Float>) -> Float {
        min(max(self, range.lowerBound), range.upperBound)
    }
}
