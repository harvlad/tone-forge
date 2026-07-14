// SequencerPattern.swift
//
// Data model for the step sequencer (D-023 Phase 3). Patterns are
// MPC-style grids: 8/16/32 steps × N tracks. Each track references
// a sound source (bundle chop, pack pad, or custom sample) via
// ChopReference. Steps store velocity and probability.
//
// Patterns are Codable for persistence in SequencerSettingsStore
// and session export. The wire format is versioned for future
// schema evolution.
//
// BPM is derived from the song's tempo by default, but can be
// overridden for standalone pattern playback or practice.

import Foundation

// MARK: - Step

/// A single step in a sequencer track.
public struct SequencerStep: Codable, Equatable, Sendable {
    /// Velocity 0 = off. 0.01–1.0 = on with that velocity.
    public var velocity: Float
    /// Probability of firing (0–1). Default 1.0 = always fires.
    /// Values < 1.0 introduce controlled randomness (drum variation).
    public var probability: Float

    public init(velocity: Float = 0, probability: Float = 1.0) {
        self.velocity = velocity.clamped(to: 0...1)
        self.probability = probability.clamped(to: 0...1)
    }

    /// True if this step will trigger (velocity > 0).
    public var isActive: Bool { velocity > 0 }

    /// Create an active step with full velocity.
    public static var on: SequencerStep {
        SequencerStep(velocity: 1.0, probability: 1.0)
    }

    /// Create an inactive step.
    public static var off: SequencerStep {
        SequencerStep(velocity: 0, probability: 1.0)
    }
}

// MARK: - Track

/// A track in a sequencer pattern. Each track triggers one sound
/// source at step positions.
public struct SequencerTrack: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    /// What sound to trigger (bundle chop, pack pad, or custom).
    public var chopRef: ChopReference
    /// Steps in this track. Count must match pattern's stepCount.
    public var steps: [SequencerStep]
    /// Track volume (0–1). Applied as velocity multiplier.
    public var volume: Float
    /// Stereo pan (-1 = left, 0 = center, +1 = right).
    public var pan: Float
    /// Muted tracks don't trigger but stay in the pattern.
    public var isMuted: Bool
    /// Soloed tracks play alone when any track is soloed.
    public var isSoloed: Bool
    /// Display name (auto-generated from chopRef if nil).
    public var name: String?

    public init(
        id: UUID = UUID(),
        chopRef: ChopReference,
        stepCount: Int,
        volume: Float = 1.0,
        pan: Float = 0,
        isMuted: Bool = false,
        isSoloed: Bool = false,
        name: String? = nil
    ) {
        self.id = id
        self.chopRef = chopRef
        self.steps = Array(repeating: .off, count: stepCount)
        self.volume = volume.clamped(to: 0...1)
        self.pan = pan.clamped(to: -1...1)
        self.isMuted = isMuted
        self.isSoloed = isSoloed
        self.name = name
    }

    /// Toggle a step on/off with full velocity.
    public mutating func toggleStep(at index: Int) {
        guard index >= 0, index < steps.count else { return }
        steps[index] = steps[index].isActive ? .off : .on
    }

    /// Set velocity for a step (dragging gesture).
    public mutating func setStepVelocity(at index: Int, velocity: Float) {
        guard index >= 0, index < steps.count else { return }
        steps[index].velocity = velocity.clamped(to: 0...1)
    }

    /// Resize track to new step count (truncate or extend with off steps).
    public mutating func resize(to newStepCount: Int) {
        if newStepCount < steps.count {
            steps = Array(steps.prefix(newStepCount))
        } else if newStepCount > steps.count {
            steps.append(contentsOf:
                Array(repeating: .off, count: newStepCount - steps.count)
            )
        }
    }

    /// Rotate steps left by `count` positions (for pattern variation).
    public mutating func rotateLeft(by count: Int = 1) {
        guard !steps.isEmpty else { return }
        let n = count % steps.count
        steps = Array(steps.dropFirst(n)) + Array(steps.prefix(n))
    }

    /// Rotate steps right by `count` positions.
    public mutating func rotateRight(by count: Int = 1) {
        guard !steps.isEmpty else { return }
        let n = count % steps.count
        rotateLeft(by: steps.count - n)
    }
}

// MARK: - Pattern

/// Allowed step counts for patterns.
public enum PatternStepCount: Int, Codable, CaseIterable, Sendable {
    case eight = 8
    case sixteen = 16
    case thirtyTwo = 32

    public var label: String {
        switch self {
        case .eight: return "8"
        case .sixteen: return "16"
        case .thirtyTwo: return "32"
        }
    }
}

/// A complete sequencer pattern with multiple tracks.
public struct SequencerPattern: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    /// Pattern name for display.
    public var name: String
    /// Number of steps (8, 16, or 32).
    public var stepCount: PatternStepCount
    /// BPM override. If nil, sync to song tempo.
    public var bpmOverride: Double?
    /// Tracks in this pattern.
    public var tracks: [SequencerTrack]
    /// Swing amount (0 = straight, 0.5 = max swing). Delays odd
    /// steps (off-beat 16ths) to create shuffle feel.
    public var swing: Float
    /// Loop the pattern (vs one-shot playback for arrangement mode).
    public var isLooping: Bool

    public init(
        id: UUID = UUID(),
        name: String = "Pattern 1",
        stepCount: PatternStepCount = .sixteen,
        bpmOverride: Double? = nil,
        tracks: [SequencerTrack] = [],
        swing: Float = 0,
        isLooping: Bool = true
    ) {
        self.id = id
        self.name = name
        self.stepCount = stepCount
        self.bpmOverride = bpmOverride
        self.tracks = tracks
        self.swing = swing.clamped(to: 0...0.5)
        self.isLooping = isLooping
    }

    /// Duration of one step in seconds at the given BPM.
    /// Default subdivision is 16th notes (4 steps per beat).
    public func stepDuration(bpm: Double) -> Double {
        let beatsPerSecond = bpm / 60.0
        // 4 steps per beat (16th notes)
        return 1.0 / (beatsPerSecond * 4)
    }

    /// Total duration of the pattern in seconds at the given BPM.
    public func totalDuration(bpm: Double) -> Double {
        stepDuration(bpm: bpm) * Double(stepCount.rawValue)
    }

    /// Add a new track for the given chop reference.
    public mutating func addTrack(for chopRef: ChopReference, name: String? = nil) {
        let track = SequencerTrack(
            chopRef: chopRef,
            stepCount: stepCount.rawValue,
            name: name
        )
        tracks.append(track)
    }

    /// Remove track at index.
    public mutating func removeTrack(at index: Int) {
        guard index >= 0, index < tracks.count else { return }
        tracks.remove(at: index)
    }

    /// Resize all tracks to new step count.
    public mutating func setStepCount(_ newCount: PatternStepCount) {
        stepCount = newCount
        for i in tracks.indices {
            tracks[i].resize(to: newCount.rawValue)
        }
    }

    /// True if any track is soloed.
    public var hasSoloedTrack: Bool {
        tracks.contains { $0.isSoloed }
    }

    /// Indices of tracks that should play (respecting mute/solo).
    public var activeTrackIndices: [Int] {
        if hasSoloedTrack {
            return tracks.indices.filter { tracks[$0].isSoloed && !tracks[$0].isMuted }
        }
        return tracks.indices.filter { !tracks[$0].isMuted }
    }

    /// Steps that should trigger at the given step index.
    /// Returns (trackIndex, velocity) pairs.
    public func triggersAt(step: Int) -> [(trackIndex: Int, velocity: Float)] {
        guard step >= 0, step < stepCount.rawValue else { return [] }

        return activeTrackIndices.compactMap { trackIdx in
            let track = tracks[trackIdx]
            let stepData = track.steps[step]
            guard stepData.isActive else { return nil }

            // Check probability
            if stepData.probability < 1.0 {
                let roll = Float.random(in: 0...1)
                if roll > stepData.probability { return nil }
            }

            // Apply track volume
            let finalVelocity = stepData.velocity * track.volume
            return (trackIdx, finalVelocity)
        }
    }
}

// MARK: - Helpers

private extension Float {
    func clamped(to range: ClosedRange<Float>) -> Float {
        min(max(self, range.lowerBound), range.upperBound)
    }
}
