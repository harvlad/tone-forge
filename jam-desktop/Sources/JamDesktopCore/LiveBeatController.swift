// LiveBeatController.swift
//
// Main coordinator for Live Beat mode on macOS. Receives onset events
// from LiveBeatTap, extracts features, matches against the active
// profile, and fires sample trigger callbacks.
//
// Desktop port of iOS LiveBeatController.

import Combine
import Foundation
import ToneForgeEngine

/// Hit detected and triggered by Live Beat.
public struct LiveBeatHit: Sendable, Equatable {
    /// Drum role that was triggered.
    public let role: DrumRole

    /// Confidence of the match (0-1).
    public let confidence: Float

    /// Velocity (0-1) derived from RMS level.
    public let velocity: Float

    /// Time relative to session start (seconds).
    public let timeSec: Double

    /// The extracted features (for debugging/training).
    public let features: LiveBeatFeatures

    public init(
        role: DrumRole,
        confidence: Float,
        velocity: Float,
        timeSec: Double,
        features: LiveBeatFeatures
    ) {
        self.role = role
        self.confidence = confidence
        self.velocity = velocity
        self.timeSec = timeSec
        self.features = features
    }
}

/// Live Beat session state.
public enum LiveBeatState: Equatable, Sendable {
    case idle
    case active
}

/// Controller for Live Beat real-time percussion.
@MainActor
public final class LiveBeatController: ObservableObject {
    // MARK: - Published State

    /// Current session state.
    @Published public private(set) var state: LiveBeatState = .idle

    /// Recent hits (last 16) for UI feedback.
    @Published public private(set) var recentHits: [LiveBeatHit] = []

    /// Total hits this session.
    @Published public private(set) var hitCount: Int = 0

    /// Current envelope level (for meter).
    @Published public private(set) var envelopeLevel: Float = 0

    // MARK: - Dependencies

    private var matcher: LiveBeatMatcher

    /// Callback to trigger drum sample: (role, velocity).
    public var onTriggerSample: ((DrumRole, Float) -> Void)?

    /// Callback when onset is detected (for tap integration).
    public var onOnsetDetected: ((LiveBeatOnsetEvent) -> Void)?

    /// Session start time (for hit timestamps).
    private var sessionStartTime: Date?

    // MARK: - Init

    public init() {
        self.matcher = LiveBeatMatcher(profile: .heuristicDefault)
    }

    // MARK: - Public API

    /// Start the Live Beat session.
    public func start() {
        guard state == .idle else { return }

        sessionStartTime = Date()
        hitCount = 0
        recentHits = []
        state = .active
    }

    /// Stop the Live Beat session.
    public func stop() {
        guard state != .idle else { return }

        sessionStartTime = nil
        state = .idle
    }

    /// Update the active profile.
    public func setProfile(_ profile: LiveBeatProfile) {
        matcher = LiveBeatMatcher(profile: profile)
    }

    /// Reset to default heuristic profile.
    public func resetToDefault() {
        matcher = LiveBeatMatcher(profile: .heuristicDefault)
    }

    /// Update envelope level from tap.
    public func updateEnvelope(_ level: Float) {
        envelopeLevel = level
    }

    /// Handle onset event from tap.
    public func handleOnset(_ event: LiveBeatOnsetEvent) {
        guard state == .active else { return }

        // Extract features
        guard let features = LiveBeatFeatures.extract(
            from: event.samples,
            sampleRate: event.sampleRate
        ) else { return }

        // Match against profile
        let match = matcher.match(features)

        // Compute velocity from RMS (normalize to 0.1-1.0 range)
        let velocity = min(1.0, max(0.1, event.rmsLevel * 5))

        // Compute time since session start
        let timeSec = sessionStartTime.map { Date().timeIntervalSince($0) } ?? 0

        // Create hit record
        let hit = LiveBeatHit(
            role: match.role,
            confidence: match.confidence,
            velocity: velocity,
            timeSec: timeSec,
            features: features
        )

        // Update state
        hitCount += 1
        recentHits.append(hit)
        if recentHits.count > 16 {
            recentHits.removeFirst()
        }

        // Trigger sample
        onTriggerSample?(match.role, velocity)
    }
}

// `LiveBeatOnsetEvent` is defined in ToneForgeEngine (shared with iOS).
