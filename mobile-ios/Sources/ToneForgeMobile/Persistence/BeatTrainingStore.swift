// BeatTrainingStore.swift
//
// Beat Capture (D-024): append-only, device-local log of user
// corrections to the heuristic drum classifier. Each record pairs the
// onset features with the model's original guess and the user's
// correction — future training data for a Core ML classifier.
//
// Posture: `neverUpload`, same as mic samples. Nothing here leaves the
// device unless a later, explicitly-consented flow exports it.

import Foundation
import ToneForgeEngine

/// One logged correction: what the model guessed vs. what the user set.
public struct BeatCorrection: Codable, Equatable, Sendable {
    public let features: OnsetFeatures
    public let original: DrumRole
    public let corrected: DrumRole
    public let timestamp: Date

    public init(
        features: OnsetFeatures,
        original: DrumRole,
        corrected: DrumRole,
        timestamp: Date = Date()
    ) {
        self.features = features
        self.original = original
        self.corrected = corrected
        self.timestamp = timestamp
    }
}

@MainActor
public final class BeatTrainingStore: ObservableObject {

    /// All corrections logged on this device (newest last).
    @Published public private(set) var corrections: [BeatCorrection] = []

    private let fileURL: URL

    /// - Parameter directory: override for tests. Defaults to
    ///   Application Support (created if missing).
    public init(directory: URL? = nil) {
        let dir = directory ?? Self.defaultDirectory()
        self.fileURL = dir.appendingPathComponent("beat_training.json")
        load()
    }

    /// Append a correction and persist immediately.
    public func log(
        features: OnsetFeatures,
        original: DrumRole,
        corrected: DrumRole
    ) {
        // No-op when the user "corrects" to the same role.
        guard original != corrected else { return }
        corrections.append(
            BeatCorrection(
                features: features, original: original, corrected: corrected
            )
        )
        persist()
    }

    // MARK: - Persistence

    private static func defaultDirectory() -> URL {
        let base = FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        ).first ?? FileManager.default.temporaryDirectory
        let dir = base.appendingPathComponent("ToneForge", isDirectory: true)
        try? FileManager.default.createDirectory(
            at: dir, withIntermediateDirectories: true
        )
        return dir
    }

    private func load() {
        guard let data = try? Data(contentsOf: fileURL) else { return }
        if let decoded = try? JSONDecoder().decode(
            [BeatCorrection].self, from: data
        ) {
            corrections = decoded
        }
    }

    private func persist() {
        guard let data = try? JSONEncoder().encode(corrections) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }
}
