// LiveBeatCalibrator.swift
//
// iOS glue for Live Beat calibration. Owns the mic tap and drives the
// platform-independent `LiveBeatCalibrationEngine` (ToneForgeEngine):
// forwards onset events into the engine, republishes the envelope meter,
// and reinstalls the tap per role. The calibration flow itself lives in
// the shared engine so macOS reuses it.

import AVFoundation
import Combine
import Foundation
import ToneForgeEngine

/// Calibrator for building Live Beat profiles (iOS).
@MainActor
public final class LiveBeatCalibrator: ObservableObject {
    // MARK: - Published State (mirrors the shared engine)

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
    public var minHitsPerRole: Int {
        get { engine.minHitsPerRole }
        set { engine.minHitsPerRole = newValue }
    }

    /// Roles to calibrate (default: basic kit).
    public var rolesToCalibrate: [DrumRole] {
        get { engine.rolesToCalibrate }
        set { engine.rolesToCalibrate = newValue }
    }

    /// Guided ("tap-along") calibration driver. Shares this calibrator's
    /// tap; deterministic segmentation, no reliance on the live detector.
    public let guided = LiveBeatGuidedSession()

    // MARK: - Private

    private let tap: LiveBeatTap
    private let engine = LiveBeatCalibrationEngine()

    public init(session: AudioSessionController) {
        self.tap = LiveBeatTap(session: session)
        setupBindings()
    }

    // MARK: - Guided API

    /// Start a guided tap-along session (installs the tap for the whole run).
    public func startGuided(profileName: String) {
        tap.install()
        guided.start(profileName: profileName)
    }

    /// Cancel a guided session and release the tap.
    public func cancelGuided() {
        guided.cancel()
        tap.remove()
    }

    /// The profile built by the guided session (valid once complete).
    public func finalizeGuided() -> LiveBeatProfile? { guided.finalize() }

    // MARK: - Public API

    /// Start a new calibration session.
    public func start(profileName: String) {
        engine.start(profileName: profileName)
    }

    /// Cancel the current calibration.
    public func cancel() {
        tap.remove()
        engine.reset()
    }

    /// Skip the current role and move to the next.
    public func skipCurrentRole() {
        engine.skipCurrentRole()
    }

    /// Manually advance to the next role (if enough hits collected).
    public func advanceToNextRole() {
        engine.advanceToNextRole()
    }

    /// Get the built profile (only valid when step == .complete).
    public func finalize() -> LiveBeatProfile? {
        engine.finalize()
    }

    // MARK: - Private

    private func setupBindings() {
        tap.$envelopeLevel
            .receive(on: DispatchQueue.main)
            .assign(to: &$envelopeLevel)

        engine.$step.receive(on: DispatchQueue.main).assign(to: &$step)
        engine.$remainingRoles.receive(on: DispatchQueue.main).assign(to: &$remainingRoles)
        engine.$profile.receive(on: DispatchQueue.main).assign(to: &$profile)

        tap.onOnset = { [weak self] event in
            Task { @MainActor in
                self?.engine.ingest(event)
            }
        }

        // Reinstall the tap for each role so every run captures cleanly.
        engine.onRoleChange = { [weak self] role in
            guard let self else { return }
            self.tap.remove()
            if role != nil {
                self.tap.install()
            }
        }

        // Guided session drives the shared tap's raw capture. The tap stays
        // installed for the whole run (no per-role reinstall).
        guided.onBeginCapture = { [weak self] in self?.tap.beginRawCapture() }
        guided.onEndCapture = { [weak self] in self?.tap.endRawCapture() ?? [] }
        guided.sampleRate = { [weak self] in self?.tap.sampleRate ?? 48_000 }
    }
}
