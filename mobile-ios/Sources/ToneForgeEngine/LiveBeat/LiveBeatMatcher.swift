// LiveBeatMatcher.swift
//
// Template bank matching for Live Beat. Given extracted micro-features,
// finds the closest matching template and returns the drum role.
//
// Uses weighted Euclidean distance (Mahalanobis-lite) with per-template
// variance. Falls back to `.perc` when no template matches confidently.

import Foundation

/// Result of template matching.
public struct LiveBeatMatch: Sendable, Equatable {
    /// The matched drum role.
    public let role: DrumRole

    /// Confidence (0-1). Higher = closer match.
    public let confidence: Float

    /// The matched template, if any.
    public let template: LiveBeatTemplate?

    /// Whether this was a fallback to percussion.
    public var isFallback: Bool { template == nil }

    public init(role: DrumRole, confidence: Float, template: LiveBeatTemplate?) {
        self.role = role
        self.confidence = confidence
        self.template = template
    }

    /// Fallback match when no template matches well.
    public static func fallback(confidence: Float = 0.1) -> LiveBeatMatch {
        LiveBeatMatch(role: .perc, confidence: confidence, template: nil)
    }
}

/// Matches incoming features against a bank of calibrated templates.
public struct LiveBeatMatcher: Sendable {
    /// Maximum distance for a valid match (beyond this → fallback).
    public var maxDistance: Float = 2.0

    /// Minimum confidence floor (below this → fallback).
    public var confidenceFloor: Float = 0.3

    /// The active profile's templates.
    public var templates: [LiveBeatTemplate]

    public init(templates: [LiveBeatTemplate] = []) {
        self.templates = templates
    }

    /// Initialize from a profile.
    public init(profile: LiveBeatProfile) {
        self.templates = profile.templates
    }

    /// Find the best matching template for the given features.
    ///
    /// - Parameter features: Extracted micro-features from onset.
    /// - Returns: Match result with role, confidence, and template.
    public func match(_ features: LiveBeatFeatures) -> LiveBeatMatch {
        guard !templates.isEmpty else {
            return .fallback()
        }

        var bestTemplate: LiveBeatTemplate?
        var bestDistance: Float = .greatestFiniteMagnitude

        for template in templates {
            let distance = features.distance(
                to: template.features,
                variance: template.variance
            )
            if distance < bestDistance {
                bestDistance = distance
                bestTemplate = template
            }
        }

        guard let matched = bestTemplate else {
            return .fallback()
        }

        // Convert distance to confidence (0-1, inverse)
        // distance 0 → confidence 1.0
        // distance maxDistance → confidence 0.0
        let confidence = max(0, 1.0 - bestDistance / maxDistance)

        if confidence < confidenceFloor {
            return .fallback(confidence: confidence)
        }

        return LiveBeatMatch(
            role: matched.role,
            confidence: confidence,
            template: matched
        )
    }

    /// Match with explicit threshold check.
    /// Returns nil if the best match is below the threshold.
    public func matchOrNil(_ features: LiveBeatFeatures, threshold: Float? = nil) -> LiveBeatMatch? {
        let result = match(features)
        let floor = threshold ?? confidenceFloor
        return result.confidence >= floor ? result : nil
    }
}

// MARK: - Batch Operations

extension LiveBeatMatcher {
    /// Match multiple feature vectors at once.
    public func matchAll(_ featuresList: [LiveBeatFeatures]) -> [LiveBeatMatch] {
        featuresList.map { match($0) }
    }

    /// Find templates that haven't been calibrated.
    /// Returns drum roles that have no template in the bank.
    public var missingRoles: [DrumRole] {
        let calibrated = Set(templates.map(\.role))
        return DrumRole.allCases.filter { !calibrated.contains($0) }
    }

    /// Check if the matcher has at least the essential drum kit:
    /// kick, snare, and some kind of hat.
    public var hasBasicKit: Bool {
        let roles = Set(templates.map(\.role))
        return roles.contains(.kick) &&
               roles.contains(.snare) &&
               (roles.contains(.closedHat) || roles.contains(.openHat))
    }
}
