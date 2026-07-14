// BeatTrainingStore.swift
//
// Beat Capture (D-024): append-only, device-local log of user
// corrections to the heuristic drum classifier. Each record pairs the
// onset features with the model's original guess and the user's
// correction — future training data for a Core ML classifier.
//
// Posture: `neverUpload`, same as mic samples. Nothing here leaves the
// device unless a later, explicitly-consented flow exports it.
//
// Desktop port of iOS BeatTrainingStore.

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

extension BeatCorrection {
    /// Wire form for the upload endpoint: features as a name→value map
    /// (keyed by `OnsetFeatures.featureNames`), roles as raw strings, and
    /// an ISO-8601 timestamp. No audio — analysis features only.
    var uploadPayload: BeatCorrectionUpload {
        var map: [String: Double] = [:]
        let names = OnsetFeatures.featureNames
        let vec = features.featureVector
        for (i, name) in names.enumerated() where i < vec.count {
            map[name] = vec[i]
        }
        return BeatCorrectionUpload(
            features: map,
            original: original.rawValue,
            corrected: corrected.rawValue,
            ts: ISO8601DateFormatter().string(from: timestamp)
        )
    }
}

@MainActor
public final class BeatTrainingStore: ObservableObject {

    /// Corrections queued for upload (newest last). This is an outbound
    /// queue, not a growing corpus — `flush` clears it on a successful
    /// upload and `maxBuffered` bounds it so a device never accumulates
    /// the large training set (that lives server-side).
    @Published public private(set) var corrections: [BeatCorrection] = []

    /// Hard cap on locally-buffered corrections. Oldest are dropped past
    /// this so an offline device can't grow the queue without bound.
    public static let maxBuffered = 500

    /// UserDefaults key for the "Help improve drum detection" opt-in.
    public static let shareOptInKey = "beat.shareCorrections"

    /// Whether the user consents to uploading corrections. Default on;
    /// revocable in Settings. Absent key reads as opted-in.
    public static var shareOptIn: Bool {
        get {
            if UserDefaults.standard.object(forKey: shareOptInKey) == nil {
                return true
            }
            return UserDefaults.standard.bool(forKey: shareOptInKey)
        }
        set { UserDefaults.standard.set(newValue, forKey: shareOptInKey) }
    }

    private let fileURL: URL
    private let uploadClient: BeatCorrectionClient

    /// - Parameters:
    ///   - directory: override for tests. Defaults to Application
    ///     Support (created if missing).
    ///   - uploadClient: injectable for tests.
    public init(directory: URL? = nil, uploadClient: BeatCorrectionClient = BeatCorrectionClient()) {
        let dir = directory ?? Self.defaultDirectory()
        self.fileURL = dir.appendingPathComponent("beat_training.json")
        self.uploadClient = uploadClient
        load()
    }

    /// Append a correction and persist immediately. Trims the oldest
    /// rows past `maxBuffered`.
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
        if corrections.count > Self.maxBuffered {
            corrections.removeFirst(corrections.count - Self.maxBuffered)
        }
        persist()
    }

    // MARK: - Upload

    /// Upload all queued corrections to the backend, clearing them on
    /// success. No-op when the queue is empty. Returns true when an
    /// upload succeeded (or nothing needed sending); false on failure so
    /// the rows stay queued for a later retry.
    @discardableResult
    public func flush(baseURL: URL) async -> Bool {
        let pending = corrections
        guard !pending.isEmpty else { return true }
        let payload = pending.map { $0.uploadPayload }
        do {
            try await uploadClient.upload(baseURL: baseURL, corrections: payload)
        } catch {
            return false
        }
        // Drop exactly the rows we sent; keep any logged during upload.
        corrections.removeFirst(min(pending.count, corrections.count))
        persist()
        return true
    }

    // MARK: - Export (training data)

    /// CSV of every correction: the canonical feature columns followed
    /// by `original`, `corrected`, and an ISO-8601 `timestamp`. Feeds an
    /// off-device Core ML training run. Empty (header only) when nothing
    /// has been logged.
    public func exportCSV() -> String {
        let header =
            (OnsetFeatures.featureNames + ["original", "corrected", "timestamp"])
            .joined(separator: ",")
        let iso = ISO8601DateFormatter()
        let rows = corrections.map { c -> String in
            let feats = c.features.featureVector
                .map { String(format: "%.6f", $0) }
                .joined(separator: ",")
            return
                "\(feats),\(c.original.rawValue),\(c.corrected.rawValue),\(iso.string(from: c.timestamp))"
        }
        return ([header] + rows).joined(separator: "\n")
    }

    /// Write the CSV export to a temp file and return its URL (for a
    /// share sheet). Throws on write failure.
    public func exportCSVFile() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("beat_training_export.csv")
        try exportCSV().write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    // MARK: - Persistence

    private static func defaultDirectory() -> URL {
        let base = FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        ).first ?? FileManager.default.temporaryDirectory
        let dir = base.appendingPathComponent("JamDesktop", isDirectory: true)
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
