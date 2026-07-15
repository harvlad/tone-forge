// LiveBeatProfile.swift
//
// Data models for Live Beat calibration profiles. A profile contains
// multiple templates, each mapping a physical sound source (desk tap,
// thigh slap, clap) to a drum role (kick, snare, clap).
//
// Users can create multiple profiles for different environments:
// "Office", "Studio", "Beatbox", etc.

import Foundation

/// One calibrated sound the user has taught.
public struct LiveBeatTemplate: Codable, Identifiable, Sendable, Equatable {
    public let id: UUID

    /// User-assigned label for this sound source ("Desk", "Thigh", "Clap").
    public var label: String

    /// Which drum role this triggers.
    public var role: DrumRole

    /// Feature centroid from calibration samples.
    public var features: LiveBeatFeatures

    /// Feature variance (spread) for weighted distance matching.
    public var variance: LiveBeatFeatures

    /// Onset threshold tuned during calibration.
    public var onsetThreshold: Float

    /// Number of calibration hits used to build this template.
    public var hitCount: Int

    public init(
        id: UUID = UUID(),
        label: String,
        role: DrumRole,
        features: LiveBeatFeatures,
        variance: LiveBeatFeatures,
        onsetThreshold: Float = 0.1,
        hitCount: Int = 0
    ) {
        self.id = id
        self.label = label
        self.role = role
        self.features = features
        self.variance = variance
        self.onsetThreshold = onsetThreshold
        self.hitCount = hitCount
    }
}

/// A complete calibration profile containing multiple templates.
public struct LiveBeatProfile: Codable, Identifiable, Sendable, Equatable {
    public let id: UUID

    /// User-visible name ("Office", "Studio", "Beatbox").
    public var name: String

    /// Calibrated sound templates.
    public var templates: [LiveBeatTemplate]

    /// Global sensitivity multiplier (scales all onset thresholds).
    /// Range: 0.5 (less sensitive) to 2.0 (more sensitive).
    public var sensitivity: Float

    /// When this profile was created.
    public var createdAt: Date

    /// When this profile was last modified.
    public var modifiedAt: Date

    public init(
        id: UUID = UUID(),
        name: String,
        templates: [LiveBeatTemplate] = [],
        sensitivity: Float = 1.0,
        createdAt: Date = Date(),
        modifiedAt: Date = Date()
    ) {
        self.id = id
        self.name = name
        self.templates = templates
        self.sensitivity = sensitivity
        self.createdAt = createdAt
        self.modifiedAt = modifiedAt
    }

    /// Find template for a given role.
    public func template(for role: DrumRole) -> LiveBeatTemplate? {
        templates.first { $0.role == role }
    }

    /// Update or add a template for a role.
    public mutating func setTemplate(_ template: LiveBeatTemplate) {
        if let index = templates.firstIndex(where: { $0.role == template.role }) {
            templates[index] = template
        } else {
            templates.append(template)
        }
        modifiedAt = Date()
    }

    /// Remove template for a role.
    public mutating func removeTemplate(for role: DrumRole) {
        templates.removeAll { $0.role == role }
        modifiedAt = Date()
    }
}

// MARK: - Default Profile

extension LiveBeatProfile {
    /// Default profile with heuristic-based templates (no calibration).
    /// Uses the same thresholds as HeuristicBeatClassifier but as
    /// feature space templates. Useful as a starting point before
    /// user calibration.
    public static var heuristicDefault: LiveBeatProfile {
        LiveBeatProfile(
            name: "Default",
            templates: [
                // Kick: dark, low-band heavy
                LiveBeatTemplate(
                    label: "Kick",
                    role: .kick,
                    features: LiveBeatFeatures(
                        centroidNorm: 0.1,
                        zcr: 0.15,
                        lowRatio: 0.6,
                        crestFactor: 4.0
                    ),
                    variance: LiveBeatFeatures(
                        centroidNorm: 0.1,
                        zcr: 0.1,
                        lowRatio: 0.15,
                        crestFactor: 2.0
                    ),
                    onsetThreshold: 0.15
                ),
                // Snare: mid-bright, medium low
                LiveBeatTemplate(
                    label: "Snare",
                    role: .snare,
                    features: LiveBeatFeatures(
                        centroidNorm: 0.4,
                        zcr: 0.3,
                        lowRatio: 0.25,
                        crestFactor: 5.0
                    ),
                    variance: LiveBeatFeatures(
                        centroidNorm: 0.15,
                        zcr: 0.1,
                        lowRatio: 0.1,
                        crestFactor: 2.0
                    ),
                    onsetThreshold: 0.12
                ),
                // Closed Hat: bright, noisy, no bass
                LiveBeatTemplate(
                    label: "Hat",
                    role: .closedHat,
                    features: LiveBeatFeatures(
                        centroidNorm: 0.7,
                        zcr: 0.5,
                        lowRatio: 0.05,
                        crestFactor: 3.0
                    ),
                    variance: LiveBeatFeatures(
                        centroidNorm: 0.15,
                        zcr: 0.15,
                        lowRatio: 0.05,
                        crestFactor: 1.5
                    ),
                    onsetThreshold: 0.08
                ),
                // Clap: mid-bright, medium duration
                LiveBeatTemplate(
                    label: "Clap",
                    role: .clap,
                    features: LiveBeatFeatures(
                        centroidNorm: 0.5,
                        zcr: 0.35,
                        lowRatio: 0.15,
                        crestFactor: 4.5
                    ),
                    variance: LiveBeatFeatures(
                        centroidNorm: 0.15,
                        zcr: 0.1,
                        lowRatio: 0.1,
                        crestFactor: 2.0
                    ),
                    onsetThreshold: 0.10
                ),
            ],
            sensitivity: 1.0
        )
    }
}
