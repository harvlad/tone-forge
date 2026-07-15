// LiveBeatCalibrationEngine.swift
//
// Platform-independent calibration state machine for Live Beat. Users tap
// to teach each drum sound (5+ hits per role); we aggregate the captured
// features into a template (centroid + variance + onset threshold) and
// assemble them into a LiveBeatProfile.
//
// This owns NO audio: the platform calibrator feeds it onset events via
// `ingest(_:)` and reacts to `onRoleChange` to (re)install its tap. Keeping
// the flow here means iOS and macOS share one implementation.

import Foundation

/// Calibration step state.
public enum CalibrationStep: Equatable, Sendable {
    case idle
    case waitingForHits(role: DrumRole, collected: Int, target: Int)
    case computing
    case complete
    case failed(String)
}

/// Builds Live Beat profiles from captured onset features.
@MainActor
public final class LiveBeatCalibrationEngine: ObservableObject {
    // MARK: - Published State

    /// Current calibration step.
    @Published public private(set) var step: CalibrationStep = .idle

    /// Roles remaining to calibrate.
    @Published public private(set) var remainingRoles: [DrumRole] = []

    /// The profile being built.
    @Published public private(set) var profile: LiveBeatProfile?

    // MARK: - Configuration

    /// Minimum hits required per role.
    public var minHitsPerRole: Int = 5

    /// Roles to calibrate (default: basic kit).
    public var rolesToCalibrate: [DrumRole] = [.kick, .snare, .closedHat, .clap]

    /// Fired when the active role changes (nil = finished). The platform
    /// glue reinstalls its tap so each role captures a clean run.
    public var onRoleChange: ((DrumRole?) -> Void)?

    // MARK: - Private

    private var collectedFeatures: [LiveBeatFeatures] = []
    private var collectedRMS: [Float] = []
    private var currentRole: DrumRole?

    public init() {}

    // MARK: - Public API

    /// Start a new calibration session.
    public func start(profileName: String) {
        profile = LiveBeatProfile(name: profileName)
        remainingRoles = rolesToCalibrate
        nextRole()
    }

    /// Cancel the current calibration.
    public func reset() {
        step = .idle
        currentRole = nil
        collectedFeatures = []
        collectedRMS = []
        profile = nil
        onRoleChange?(nil)
    }

    /// Skip the current role and move to the next.
    public func skipCurrentRole() {
        guard case .waitingForHits = step else { return }
        remainingRoles.removeFirst()
        nextRole()
    }

    /// Manually advance to the next role (if enough hits collected).
    public func advanceToNextRole() {
        guard case let .waitingForHits(role, collected, _) = step,
              collected >= minHitsPerRole
        else { return }

        buildTemplateForCurrentRole(role: role)
        remainingRoles.removeFirst()
        nextRole()
    }

    /// Get the built profile (only valid when step == .complete).
    public func finalize() -> LiveBeatProfile? {
        guard case .complete = step else { return nil }
        return profile
    }

    /// Feed an onset event captured during calibration.
    public func ingest(_ event: LiveBeatOnsetEvent) {
        guard case let .waitingForHits(role, collected, target) = step else { return }

        guard let features = LiveBeatFeatures.extract(
            from: event.samples,
            sampleRate: event.sampleRate
        ) else { return }

        collectedFeatures.append(features)
        collectedRMS.append(event.rmsLevel)

        let newCount = collected + 1
        step = .waitingForHits(role: role, collected: newCount, target: target)

        // Auto-advance once we have the target plus a small buffer.
        if newCount >= target + 3 {
            buildTemplateForCurrentRole(role: role)
            remainingRoles.removeFirst()
            nextRole()
        }
    }

    // MARK: - Private

    private func nextRole() {
        collectedFeatures = []
        collectedRMS = []

        guard let role = remainingRoles.first else {
            step = .complete
            currentRole = nil
            onRoleChange?(nil)
            return
        }

        currentRole = role
        step = .waitingForHits(role: role, collected: 0, target: minHitsPerRole)
        onRoleChange?(role)
    }

    private func buildTemplateForCurrentRole(role: DrumRole) {
        guard !collectedFeatures.isEmpty else { return }

        guard let mean = LiveBeatFeatures.mean(collectedFeatures),
              let variance = LiveBeatFeatures.variance(collectedFeatures, mean: mean)
        else { return }

        // Onset threshold from collected RMS: 60% of the 25th percentile.
        let sortedRMS = collectedRMS.sorted()
        let p25Index = max(0, sortedRMS.count / 4 - 1)
        let onsetThreshold = sortedRMS[p25Index] * 0.6

        let template = LiveBeatTemplate(
            label: role.displayName,
            role: role,
            features: mean,
            variance: variance,
            onsetThreshold: max(0.03, onsetThreshold),
            hitCount: collectedFeatures.count
        )

        profile?.setTemplate(template)
    }
}
