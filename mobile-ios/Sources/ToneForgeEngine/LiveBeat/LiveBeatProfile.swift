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
    ///
    /// Tuned for *mic taps*, not studio drums. A desk/chest thump through
    /// a phone mic never reaches a real kick's sub-bass energy, so the
    /// kick template does NOT demand a high `lowRatio` — it just claims
    /// the darkest, least-noisy corner of tap space, with a wide
    /// `lowRatio` variance so any relatively bass-heavy tap lands here
    /// instead of on the snare. Roles separate by *relative* brightness
    /// and noisiness (Mahalanobis-weighted), which is all the mic can
    /// reliably give us before the user calibrates their own sounds.
    public static var heuristicDefault: LiveBeatProfile {
        LiveBeatProfile(
            name: "Default",
            templates: [
                // Kick: the darkest, least-noisy tap. Modest lowRatio
                // (a mic thump, not a sub-bass drum) with wide tolerance
                // so bass-leaning taps still land here over the snare.
                LiveBeatTemplate(
                    label: "Kick",
                    role: .kick,
                    features: LiveBeatFeatures(
                        centroidNorm: 0.12,
                        zcr: 0.12,
                        lowRatio: 0.42,
                        crestFactor: 4.0
                    ),
                    variance: LiveBeatFeatures(
                        centroidNorm: 0.12,
                        zcr: 0.1,
                        lowRatio: 0.22,
                        crestFactor: 2.5
                    ),
                    onsetThreshold: 0.15
                ),
                // Snare: brighter and noisier than the kick, with clearly
                // less low-band energy. Tight lowRatio keeps it from
                // stealing genuinely bassy taps.
                LiveBeatTemplate(
                    label: "Snare",
                    role: .snare,
                    features: LiveBeatFeatures(
                        centroidNorm: 0.4,
                        zcr: 0.36,
                        lowRatio: 0.18,
                        crestFactor: 5.0
                    ),
                    variance: LiveBeatFeatures(
                        centroidNorm: 0.16,
                        zcr: 0.12,
                        lowRatio: 0.1,
                        crestFactor: 2.5
                    ),
                    onsetThreshold: 0.12
                ),
                // Closed Hat: brightest, noisiest, almost no low band.
                LiveBeatTemplate(
                    label: "Hat",
                    role: .closedHat,
                    features: LiveBeatFeatures(
                        centroidNorm: 0.68,
                        zcr: 0.55,
                        lowRatio: 0.06,
                        crestFactor: 3.0
                    ),
                    variance: LiveBeatFeatures(
                        centroidNorm: 0.18,
                        zcr: 0.18,
                        lowRatio: 0.06,
                        crestFactor: 2.0
                    ),
                    onsetThreshold: 0.08
                ),
                // Clap: mid-bright, noisy, sharp attack; little low band.
                LiveBeatTemplate(
                    label: "Clap",
                    role: .clap,
                    features: LiveBeatFeatures(
                        centroidNorm: 0.5,
                        zcr: 0.42,
                        lowRatio: 0.12,
                        crestFactor: 4.5
                    ),
                    variance: LiveBeatFeatures(
                        centroidNorm: 0.16,
                        zcr: 0.12,
                        lowRatio: 0.08,
                        crestFactor: 2.5
                    ),
                    onsetThreshold: 0.10
                ),
            ],
            sensitivity: 1.0
        )
    }
}
