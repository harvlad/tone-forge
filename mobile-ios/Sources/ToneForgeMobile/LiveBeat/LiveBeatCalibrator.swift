// LiveBeatCalibrator.swift
//
// Handles the calibration flow for Live Beat. Users tap to teach each
// drum sound (5+ hits per role), and we build templates from the
// captured features.

import AVFoundation
import Foundation
import ToneForgeEngine

/// Calibration step state.
public enum CalibrationStep: Equatable, Sendable {
    case idle
    case waitingForHits(role: DrumRole, collected: Int, target: Int)
    case computing
    case complete
    case failed(String)
}

/// Calibrator for building Live Beat profiles.
@MainActor
public final class LiveBeatCalibrator: ObservableObject {
    // MARK: - Published State

    /// Current calibration step.
    @Published public private(set) var step: CalibrationStep = .idle

    /// Roles remaining to calibrate.
    @Published public private(set) var remainingRoles: [DrumRole] = []

    /// Current envelope level (for meter).
    @Published public private(set) var envelopeLevel: Float = 0

    /// The profile being built.
    @Published public private(set) var profile: LiveBeatProfile?

    // MARK: - Configuration

    /// Minimum hits required per role.
    public var minHitsPerRole: Int = 5

    /// Roles to calibrate (default: basic kit).
    public var rolesToCalibrate: [DrumRole] = [.kick, .snare, .closedHat, .clap]

    // MARK: - Private

    private let tap: LiveBeatTap
    private var collectedFeatures: [LiveBeatFeatures] = []
    private var collectedRMS: [Float] = []
    private var currentRole: DrumRole?

    public init(session: AudioSessionController) {
        self.tap = LiveBeatTap(session: session)
        setupBindings()
    }

    // MARK: - Public API

    /// Start a new calibration session.
    public func start(profileName: String) {
        profile = LiveBeatProfile(name: profileName)
        remainingRoles = rolesToCalibrate
        nextRole()
    }

    /// Cancel the current calibration.
    public func cancel() {
        tap.remove()
        step = .idle
        currentRole = nil
        collectedFeatures = []
        collectedRMS = []
        profile = nil
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

    // MARK: - Private

    private func setupBindings() {
        tap.$envelopeLevel
            .receive(on: DispatchQueue.main)
            .assign(to: &$envelopeLevel)

        tap.onOnset = { [weak self] event in
            Task { @MainActor in
                self?.handleOnset(event)
            }
        }
    }

    private func nextRole() {
        tap.remove()
        collectedFeatures = []
        collectedRMS = []

        guard let role = remainingRoles.first else {
            // All roles done
            step = .complete
            return
        }

        currentRole = role
        step = .waitingForHits(role: role, collected: 0, target: minHitsPerRole)
        tap.install()
    }

    private func handleOnset(_ event: LiveBeatOnsetEvent) {
        guard case let .waitingForHits(role, collected, target) = step else { return }

        // Extract features
        guard let features = LiveBeatFeatures.extract(
            from: event.samples,
            sampleRate: event.sampleRate
        ) else { return }

        collectedFeatures.append(features)
        collectedRMS.append(event.rmsLevel)

        let newCount = collected + 1
        step = .waitingForHits(role: role, collected: newCount, target: target)

        // Auto-advance after enough hits
        if newCount >= target + 3 {
            // Collected enough with buffer, auto-advance
            buildTemplateForCurrentRole(role: role)
            remainingRoles.removeFirst()
            nextRole()
        }
    }

    private func buildTemplateForCurrentRole(role: DrumRole) {
        guard !collectedFeatures.isEmpty else { return }

        // Compute mean and variance
        guard let mean = LiveBeatFeatures.mean(collectedFeatures),
              let variance = LiveBeatFeatures.variance(collectedFeatures, mean: mean)
        else { return }

        // Compute onset threshold from collected RMS levels
        let sortedRMS = collectedRMS.sorted()
        let p25Index = max(0, sortedRMS.count / 4 - 1)
        let onsetThreshold = sortedRMS[p25Index] * 0.6  // 60% of 25th percentile

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
