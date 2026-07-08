// LearnProgress.swift
//
// Codable progress records for Learn mode (redesign Phase 8).
// One SongLearnProgress per analysed song, persisted by the mobile
// LearnProgressStore under Documents/learnProgress/{analysisId}.json.
//
// Sections are keyed by lowercased label ("chorus", "verse") so
// backend capitalisation churn can't fork a section's history —
// same normalisation SectionResolver.uniqueLabels uses for chips.

import Foundation

/// Per-section practice history.
public struct SectionProgress: Codable, Sendable, Equatable {
    /// One passing pass flips this true; it never reverts.
    public var learned: Bool
    /// Completed practice passes over this section.
    public var passCount: Int
    /// Best single-pass accuracy so far (0…1).
    public var bestAccuracy: Double

    public init(
        learned: Bool = false,
        passCount: Int = 0,
        bestAccuracy: Double = 0
    ) {
        self.learned = learned
        self.passCount = passCount
        self.bestAccuracy = bestAccuracy
    }

    /// Fold one completed pass into the history.
    public mutating func fold(_ result: LearnPassResult) {
        passCount += 1
        bestAccuracy = max(bestAccuracy, result.accuracy)
        if result.isPassing { learned = true }
    }
}

/// Whole-song learn progress. schemaVersion guards future migrations.
public struct SongLearnProgress: Codable, Sendable, Equatable {
    public var schemaVersion: Int
    public var analysisId: String
    /// Keyed by ``sectionKey(_:)`` (lowercased label).
    public var sections: [String: SectionProgress]
    /// Longest consecutive-hit streak across all sessions.
    public var longestStreak: Int

    public init(analysisId: String) {
        self.schemaVersion = 1
        self.analysisId = analysisId
        self.sections = [:]
        self.longestStreak = 0
    }

    /// Normalised dictionary key for a section label.
    public static func sectionKey(_ label: String) -> String {
        label.lowercased()
    }

    /// History for a section (defaults for never-practiced ones).
    public func progress(for label: String) -> SectionProgress {
        sections[Self.sectionKey(label)] ?? SectionProgress()
    }

    /// Fold a completed pass into the section's history.
    public mutating func recordPass(
        _ result: LearnPassResult, sectionLabel: String
    ) {
        var entry = progress(for: sectionLabel)
        entry.fold(result)
        sections[Self.sectionKey(sectionLabel)] = entry
    }

    /// Raise the all-time streak if this session beat it.
    public mutating func recordStreak(_ streak: Int) {
        longestStreak = max(longestStreak, streak)
    }

    // MARK: - Derived stats

    /// Sections marked learned.
    public var learnedCount: Int {
        sections.values.filter(\.learned).count
    }

    /// Mean best-accuracy across practiced sections (0 when none).
    public var overallAccuracy: Double {
        guard !sections.isEmpty else { return 0 }
        let sum = sections.values.reduce(0) { $0 + $1.bestAccuracy }
        return sum / Double(sections.count)
    }

    /// learnedCount / totalSections, clamped to 0…1. The song's true
    /// section count comes from the bundle timeline — this record
    /// only knows practiced sections.
    public func percentComplete(totalSections: Int) -> Double {
        guard totalSections > 0 else { return 0 }
        return min(1.0, Double(learnedCount) / Double(totalSections))
    }
}
