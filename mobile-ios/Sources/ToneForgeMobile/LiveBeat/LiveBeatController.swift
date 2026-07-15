// LiveBeatController.swift
//
// Main coordinator for Live Beat mode. Receives onset events from
// LiveBeatTap, extracts features, matches against the active profile,
// and triggers drum samples via SampleVoicePool.
//
// Also manages the optional MIDI recording to build sequencer patterns.

import AVFoundation
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
}

/// Live Beat session state.
public enum LiveBeatState: Equatable, Sendable {
    case idle
    case active
    case recording
}

/// Controller for Live Beat real-time percussion.
@MainActor
public final class LiveBeatController: ObservableObject {
    // MARK: - Published State

    /// Current session state.
    @Published public private(set) var state: LiveBeatState = .idle

    /// Active profile (nil = use heuristic default).
    @Published public var activeProfile: LiveBeatProfile?

    /// Recent hits (last 16) for UI feedback.
    @Published public private(set) var recentHits: [LiveBeatHit] = []

    /// Total hits this session.
    @Published public private(set) var hitCount: Int = 0

    /// Current envelope level (for meter).
    @Published public private(set) var envelopeLevel: Float = 0

    /// Recorded hits (when recording is enabled).
    @Published public private(set) var recordedHits: [LiveBeatHit] = []

    /// Session BPM (from song or user-set).
    @Published public var bpm: Double = 120

    /// Whether to record hits.
    @Published public var isRecordingEnabled: Bool = false

    // MARK: - Dependencies

    private let tap: LiveBeatTap
    private var matcher: LiveBeatMatcher

    /// Callback to trigger drum sample.
    public var onTriggerSample: ((DrumRole, Float) -> Void)?

    /// Session start time (for recording timestamps).
    private var sessionStartTime: Date?

    /// Cancellables for Combine subscriptions.
    private var cancellables = Set<AnyCancellable>()

    // MARK: - Init

    public init(session: AudioSessionController) {
        self.tap = LiveBeatTap(session: session)
        self.matcher = LiveBeatMatcher(profile: .heuristicDefault)

        setupBindings()
    }

    // MARK: - Public API

    /// Start the Live Beat session.
    public func start() {
        guard state == .idle else { return }

        // Update matcher with active profile
        if let profile = activeProfile {
            matcher = LiveBeatMatcher(profile: profile)
            tap.sensitivity = profile.sensitivity
        } else {
            matcher = LiveBeatMatcher(profile: .heuristicDefault)
            tap.sensitivity = 1.0
        }

        tap.install()
        sessionStartTime = Date()
        hitCount = 0
        recentHits = []
        recordedHits = []

        state = isRecordingEnabled ? .recording : .active
    }

    /// Stop the Live Beat session.
    public func stop() {
        guard state != .idle else { return }

        tap.remove()
        sessionStartTime = nil
        state = .idle
    }

    /// Toggle recording on/off.
    public func toggleRecording() {
        isRecordingEnabled.toggle()
        if state == .active && isRecordingEnabled {
            state = .recording
            recordedHits = []
        } else if state == .recording && !isRecordingEnabled {
            state = .active
        }
    }

    /// Clear recorded hits.
    public func clearRecording() {
        recordedHits = []
    }

    /// Build a sequencer pattern from recorded hits.
    public func buildPattern(name: String, quantize: Bool = true) -> SequencerPattern? {
        guard !recordedHits.isEmpty else { return nil }

        // Group hits by role
        var trackHits: [DrumRole: [(step: Int, velocity: Float)]] = [:]
        let stepDuration = 60.0 / bpm / 4  // 16th note duration

        for hit in recordedHits {
            let step = quantize
                ? Int(round(hit.timeSec / stepDuration))
                : Int(hit.timeSec / stepDuration)

            if trackHits[hit.role] == nil {
                trackHits[hit.role] = []
            }
            trackHits[hit.role]?.append((step, hit.velocity))
        }

        // Determine pattern length (round up to 8, 16, or 32 steps)
        let maxStep = trackHits.values.flatMap { $0 }.map(\.step).max() ?? 0
        let patternStepCount: PatternStepCount
        let stepCountInt: Int
        if maxStep <= 8 {
            patternStepCount = .eight
            stepCountInt = 8
        } else if maxStep <= 16 {
            patternStepCount = .sixteen
            stepCountInt = 16
        } else {
            patternStepCount = .thirtyTwo
            stepCountInt = 32
        }

        // Build tracks
        var tracks: [SequencerTrack] = []
        for role in DrumRole.allCases {
            guard let hits = trackHits[role], !hits.isEmpty else { continue }

            // Create track with correct step count
            var track = SequencerTrack(
                chopRef: role.chopRef,
                stepCount: stepCountInt,
                name: role.displayName
            )

            // Set step velocities
            for (step, velocity) in hits {
                guard step >= 0 && step < stepCountInt else { continue }
                // Keep loudest hit if multiple on same step
                if track.steps[step].velocity < velocity {
                    track.steps[step] = SequencerStep(velocity: velocity)
                }
            }

            tracks.append(track)
        }

        return SequencerPattern(
            name: name,
            stepCount: patternStepCount,
            bpmOverride: bpm,
            tracks: tracks
        )
    }

    // MARK: - Private

    private func setupBindings() {
        // Forward envelope level from tap
        tap.$envelopeLevel
            .receive(on: DispatchQueue.main)
            .assign(to: &$envelopeLevel)

        // Handle onset events
        tap.onOnset = { [weak self] event in
            Task { @MainActor in
                self?.handleOnset(event)
            }
        }
    }

    private func handleOnset(_ event: LiveBeatOnsetEvent) {
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

        // Record if enabled
        if state == .recording {
            recordedHits.append(hit)
        }

        // Only trigger if confidence is high enough (sound matches a calibrated template)
        guard match.confidence >= 0.4 else { return }

        // Trigger sample, then arm the tap's feedback gate so the drum
        // one-shot leaving the speakers can't retrigger the detector
        // (acoustic-feedback machine-gun).
        onTriggerSample?(match.role, velocity)
        tap.suppressDetection(ms: 70)
    }
}
