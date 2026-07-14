// TransformBakeService.swift
//
// Orchestrates baking a pad's transform chain into a new local sample:
// load base buffer → render transforms (mono) → classify → save to
// PadSampleStore → return metadata. Non-destructive: original sample
// or bundle chop is untouched.
//
// Desktop port of iOS ModeCoordinator+Transforms.bakeTransforms().
// Output clamped to 8 s compliance cap (a 4× stretch of an 8 s take
// would otherwise exceed it).

import Foundation
import AVFoundation
import ToneForgeEngine

@MainActor
public final class TransformBakeService {

    public enum BakeError: Error, LocalizedError {
        /// Pad has no transform chain to bake.
        case nothingToBake
        /// Base buffer not available (file missing, decoding, or
        /// rendering produced silence).
        case noAudio
        /// Sample exceeds compliance cap.
        case durationExceeded(Double)

        public var errorDescription: String? {
            switch self {
            case .nothingToBake:
                return "This pad has no transforms to bake."
            case .noAudio:
                return "The pad's audio isn't ready yet — try again in a moment."
            case .durationExceeded(let s):
                return "Baked sample (\(String(format: "%.1f", s)) s) exceeds 8 s cap."
            }
        }
    }

    private let padSampleStore: PadSampleStore

    /// Maximum baked sample duration (compliance cap).
    public static let maxDurationSec = 8.0

    public init(padSampleStore: PadSampleStore) {
        self.padSampleStore = padSampleStore
    }

    /// Bake `chain` over base buffer into a new local sample.
    /// Returns saved metadata with UUID. Throws on empty chain,
    /// missing audio, or render failure.
    ///
    /// - Parameters:
    ///   - chain: Transform chain to apply (must be non-empty).
    ///   - baseBuffer: Source audio to transform.
    ///   - tempoBpm: Tempo for tempo-synced transforms (stutter/gate).
    ///   - chord: Current chord MIDI notes for harmony voice leading.
    ///   - sourceProvenance: If baking a local sample, pass its metadata
    ///     source so provenance propagates (mic → mic). If baking a
    ///     bundle chop, pass nil (becomes `.songChop`).
    @discardableResult
    public func bake(
        chain: [PadTransform],
        baseBuffer: AVAudioPCMBuffer,
        tempoBpm: Double,
        chord: [Int],
        sourceProvenance: PadSampleMetadata.Source?
    ) async throws -> PadSampleMetadata {
        let audible = chain.filter { $0 != .loop }
        guard !audible.isEmpty else { throw BakeError.nothingToBake }

        var mono = await renderMono(
            chain: audible,
            base: baseBuffer,
            tempoBpm: tempoBpm,
            chord: chord
        )
        guard !mono.isEmpty else { throw BakeError.noAudio }

        let rate = baseBuffer.format.sampleRate
        let maxSamples = Int(Self.maxDurationSec * rate)
        if mono.count > maxSamples {
            mono = Array(mono.prefix(maxSamples))
        }

        // Provenance: baked local sample keeps its source (mic stays
        // mic → never uploaded); bundle chop becomes songChop.
        let source = sourceProvenance ?? .songChop

        let (cls, confidence) = HeuristicClassifier().classify(
            samples: mono, sampleRate: rate
        )

        return try await padSampleStore.save(
            samples: mono,
            sampleRate: rate,
            metadata: PadSampleMetadata(
                source: source,
                classification: cls,
                confidence: confidence,
                durationSec: 0,    // filled by store
                sampleRate: 0,     // filled by store
                channels: 1,
                colorHint: Self.localColor(source)
            )
        )
    }

    /// Load full buffer from a stem file segment (for baking bundle
    /// chops). Returns nil on I/O error.
    public func loadBuffer(
        file: AVAudioFile,
        startSec: Double,
        endSec: Double
    ) async -> AVAudioPCMBuffer? {
        await Task.detached(priority: .userInitiated) {
            let sampleRate = file.fileFormat.sampleRate
            let startFrame = AVAudioFramePosition(max(0, startSec) * sampleRate)
            let endFrame = min(
                AVAudioFramePosition(endSec * sampleRate), file.length
            )
            let frameCount = AVAudioFrameCount(max(0, endFrame - startFrame))
            guard frameCount > 0, startFrame < file.length else { return nil }

            let format = file.processingFormat
            guard let buffer = AVAudioPCMBuffer(
                pcmFormat: format, frameCapacity: frameCount
            ) else { return nil }

            do {
                file.framePosition = startFrame
                try file.read(into: buffer, frameCount: frameCount)
                return buffer
            } catch {
                print("[TransformBakeService] read error: \(error)")
                return nil
            }
        }.value
    }

    // MARK: - Render

    /// Render chain over base buffer, return mono (channel-averaged).
    private func renderMono(
        chain: [PadTransform],
        base: AVAudioPCMBuffer,
        tempoBpm: Double,
        chord: [Int]
    ) async -> [Float] {
        guard let baseChannels = Self.channels(of: base) else { return [] }
        let sampleRate = base.format.sampleRate
        return await Task.detached(priority: .userInitiated) {
            let out = baseChannels.map { channel in
                TransformEngine.render(
                    channel,
                    chain: chain,
                    tempoBpm: tempoBpm,
                    sampleRate: sampleRate,
                    chordAt: { _ in chord }
                )
            }
            guard let first = out.first, !first.isEmpty else { return [] }
            guard out.count > 1 else { return first }
            var mono = first
            let scale = 1.0 / Float(out.count)
            for ch in out.dropFirst() {
                let n = min(mono.count, ch.count)
                for i in 0..<n { mono[i] += ch[i] }
            }
            for i in 0..<mono.count { mono[i] *= scale }
            return mono
        }.value
    }

    private static func channels(of buffer: AVAudioPCMBuffer) -> [[Float]]? {
        guard let data = buffer.floatChannelData else { return nil }
        let frames = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)
        guard frames > 0, channelCount > 0 else { return nil }
        return (0..<channelCount).map {
            Array(UnsafeBufferPointer(start: data[$0], count: frames))
        }
    }

    private static func localColor(_ source: PadSampleMetadata.Source) -> UInt32 {
        switch source {
        case .mic: return 0xFF8C3A        // warm orange
        case .vocoded: return 0x9B4DFF    // purple
        case .songChop: return 0xFFB547   // orange
        }
    }
}
