// PadSampleStore.swift
//
// On-disk persistence for locally-created pad samples (mic captures,
// vocoder output, baked transforms). Port of the iOS PadSampleStore.
//
// Layout:
//   {AppSupport}/samples/{uuid}.wav    48 kHz mono Float32 payload
//   {AppSupport}/samples/{uuid}.json   PadSampleMetadata sidecar
//
// Application Support (not Caches) because these are user creations.
// @MainActor because it publishes the sample list to SwiftUI; actual
// WAV encode/decode runs off-main so writes never block the grid.

import Foundation
import AVFoundation
import Combine
import ToneForgeEngine

@MainActor
public final class PadSampleStore: ObservableObject {

    public enum StoreError: Error, LocalizedError {
        case durationExceedsCap(Double)
        case emptyPayload
        case notFound(UUID)
        case unreadableWAV(UUID)

        public var errorDescription: String? {
            switch self {
            case .durationExceedsCap(let s):
                return "Sample is \(String(format: "%.2f", s)) s; the cap is "
                    + "\(StemSlice.maxChopDurationSec) s"
            case .emptyPayload:
                return "Refusing to save an empty sample"
            case .notFound(let id):
                return "Sample \(id.uuidString) not found on disk"
            case .unreadableWAV(let id):
                return "Sample \(id.uuidString) has an unreadable WAV payload"
            }
        }
    }

    /// All known samples, newest first. Reloaded from disk at init
    /// and maintained incrementally by save/update/delete.
    @Published public private(set) var samples: [PadSampleMetadata] = []

    private let root: URL?
    private let fileManager: FileManager

    /// - Parameter root: base directory override for tests; nil =
    ///   the app's Application Support directory.
    public init(root: URL? = nil, fileManager: FileManager = .default) {
        self.root = root
        self.fileManager = fileManager
        reload()
    }

    // MARK: - Paths

    /// `{AppSupport}/samples/`. Created on first access.
    public func samplesDir() throws -> URL {
        let base: URL
        if let root {
            base = root
        } else {
            base = try fileManager.url(
                for: .applicationSupportDirectory,
                in: .userDomainMask,
                appropriateFor: nil,
                create: true
            ).appendingPathComponent("Jamn", isDirectory: true)
        }
        let dir = base.appendingPathComponent("samples", isDirectory: true)
        try fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    public func wavURL(id: UUID) throws -> URL {
        try samplesDir().appendingPathComponent("\(id.uuidString).wav")
    }

    public func sidecarURL(id: UUID) throws -> URL {
        try samplesDir().appendingPathComponent("\(id.uuidString).json")
    }

    // MARK: - Save

    /// Persist a conditioned capture. Fills in `durationSec`,
    /// `sampleRate`, `channels` from the payload, enforces the 8 s
    /// compliance cap, writes the WAV off-main, then the sidecar.
    @discardableResult
    public func save(
        samples payload: [Float],
        sampleRate: Double,
        metadata: PadSampleMetadata
    ) async throws -> PadSampleMetadata {
        guard !payload.isEmpty else { throw StoreError.emptyPayload }
        let durationSec = Double(payload.count) / sampleRate
        guard durationSec <= StemSlice.maxChopDurationSec + 0.001 else {
            throw StoreError.durationExceedsCap(durationSec)
        }

        var meta = metadata
        meta.durationSec = durationSec
        meta.sampleRate = sampleRate
        meta.channels = 1

        let wav = try wavURL(id: meta.id)
        try await Task.detached(priority: .userInitiated) {
            try Self.writeWAV(payload, sampleRate: sampleRate, to: wav)
        }.value

        do {
            try writeSidecar(meta)
        } catch {
            // Never leave an orphaned WAV behind a failed sidecar.
            try? fileManager.removeItem(at: wav)
            throw error
        }

        samples.removeAll { $0.id == meta.id }
        samples.insert(meta, at: 0)
        sortSamples()
        return meta
    }

    // MARK: - Load

    /// Decode a sample's WAV into a deinterleaved Float32 buffer at
    /// its stored rate.
    public func loadBuffer(id: UUID) async throws -> AVAudioPCMBuffer {
        let url = try wavURL(id: id)
        guard fileManager.fileExists(atPath: url.path) else {
            throw StoreError.notFound(id)
        }
        return try await Task.detached(priority: .userInitiated) {
            guard let buffer = try Self.readWAV(at: url) else {
                throw StoreError.unreadableWAV(id)
            }
            return buffer
        }.value
    }

    public func metadata(id: UUID) -> PadSampleMetadata? {
        samples.first { $0.id == id }
    }

    // MARK: - Update / delete

    /// Rewrite a sample's sidecar (class override, color, …). The WAV
    /// payload is immutable — transforms bake to NEW samples.
    public func updateMetadata(_ meta: PadSampleMetadata) throws {
        guard samples.contains(where: { $0.id == meta.id }) else {
            throw StoreError.notFound(meta.id)
        }
        try writeSidecar(meta)
        samples = samples.map { $0.id == meta.id ? meta : $0 }
        sortSamples()
    }

    /// Remove WAV + sidecar. No-op when already gone.
    public func delete(id: UUID) {
        if let url = try? wavURL(id: id), fileManager.fileExists(atPath: url.path) {
            try? fileManager.removeItem(at: url)
        }
        if let url = try? sidecarURL(id: id), fileManager.fileExists(atPath: url.path) {
            try? fileManager.removeItem(at: url)
        }
        samples.removeAll { $0.id == id }
    }

    /// Total bytes on disk across all payloads + sidecars.
    public func totalBytes() -> Int64 {
        guard let dir = try? samplesDir(),
              let contents = try? fileManager.contentsOfDirectory(
                  at: dir,
                  includingPropertiesForKeys: [.fileSizeKey],
                  options: [.skipsHiddenFiles]
              )
        else { return 0 }
        return contents.reduce(0) { sum, url in
            let size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
            return sum + Int64(size)
        }
    }

    // MARK: - Listing

    /// Re-scan the sidecars. Bad JSON is skipped; a sidecar whose WAV
    /// is missing is treated as corrupt and skipped too.
    public func reload() {
        guard let dir = try? samplesDir(),
              let contents = try? fileManager.contentsOfDirectory(
                  at: dir,
                  includingPropertiesForKeys: nil,
                  options: [.skipsHiddenFiles]
              )
        else {
            samples = []
            return
        }
        let decoder = JSONDecoder()
        var out: [PadSampleMetadata] = []
        for url in contents where url.pathExtension.lowercased() == "json" {
            guard let data = try? Data(contentsOf: url),
                  let meta = try? decoder.decode(PadSampleMetadata.self, from: data),
                  let wav = try? wavURL(id: meta.id),
                  fileManager.fileExists(atPath: wav.path)
            else { continue }
            out.append(meta)
        }
        samples = out
        sortSamples()
    }

    private func sortSamples() {
        samples.sort { $0.createdAt > $1.createdAt }
    }

    private func writeSidecar(_ meta: PadSampleMetadata) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(meta)
        try data.write(to: try sidecarURL(id: meta.id), options: [.atomic])
    }

    // MARK: - WAV codec (nonisolated, runs off-main)

    nonisolated static func writeWAV(
        _ payload: [Float], sampleRate: Double, to url: URL
    ) throws {
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: sampleRate,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 32,
            AVLinearPCMIsFloatKey: true,
            AVLinearPCMIsBigEndianKey: false,
            AVLinearPCMIsNonInterleaved: false,
        ]
        let file = try AVAudioFile(forWriting: url, settings: settings)
        guard let format = AVAudioFormat(
            standardFormatWithSampleRate: sampleRate, channels: 1
        ), let buffer = AVAudioPCMBuffer(
            pcmFormat: format, frameCapacity: AVAudioFrameCount(payload.count)
        ) else {
            throw StoreError.emptyPayload
        }
        buffer.frameLength = AVAudioFrameCount(payload.count)
        payload.withUnsafeBufferPointer { src in
            buffer.floatChannelData![0].update(
                from: src.baseAddress!, count: payload.count
            )
        }
        try file.write(from: buffer)
    }

    nonisolated static func readWAV(at url: URL) throws -> AVAudioPCMBuffer? {
        let file = try AVAudioFile(forReading: url, commonFormat: .pcmFormatFloat32,
                                   interleaved: false)
        let frames = AVAudioFrameCount(file.length)
        guard frames > 0,
              let buffer = AVAudioPCMBuffer(
                  pcmFormat: file.processingFormat, frameCapacity: frames
              )
        else { return nil }
        try file.read(into: buffer)
        return buffer
    }
}
