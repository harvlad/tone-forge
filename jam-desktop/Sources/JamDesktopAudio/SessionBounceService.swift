// SessionBounceService.swift
//
// Offline export of a captured take (P4): loads each assigned pad's
// chop segment into a PCM buffer keyed by PadIndex.rawValue, then
// hands the capture + buffers to the engine's deterministic
// SessionBounceRenderer (pure-Swift mixer, bit-identical WAV).
//
// Pad resolution mirrors ReplayExecutor: desktop captures store an
// empty padMapping, so pads resolve against the CURRENT Launchpad
// grid (assignments) for the same songBackendId. Pads without an
// assignment are simply absent from padBuffers — the renderer skips
// those events (same semantics as replay's no-op).
//
// Buffer contract (SessionBounceRenderer doc): Float32 PCM at the
// render sample rate, mono or stereo, NO resampling inside — this
// service converts each segment with AVAudioConverter up front.

import Foundation
import AVFoundation
import ToneForgeEngine
import JamDesktopCore

/// Reads a [startSec, endSec] slice of an audio file and converts it
/// to deinterleaved Float32 at the bounce sample rate.
enum ChopBufferLoader {

    enum LoadError: Error {
        case emptySegment
        case conversionFailed(String)
    }

    /// Blocking file IO — call off the main actor.
    static func loadSegment(
        url: URL,
        startSec: Double,
        endSec: Double,
        sampleRate: Double = 48_000
    ) throws -> AVAudioPCMBuffer {
        let file = try AVAudioFile(forReading: url)
        let fileRate = file.fileFormat.sampleRate
        let startFrame = AVAudioFramePosition(max(0, startSec) * fileRate)
        let endFrame = min(
            AVAudioFramePosition(endSec * fileRate), file.length)
        let frameCount = endFrame - startFrame
        guard frameCount > 0, startFrame < file.length else {
            throw LoadError.emptySegment
        }

        let sourceFormat = file.processingFormat
        guard let raw = AVAudioPCMBuffer(
            pcmFormat: sourceFormat,
            frameCapacity: AVAudioFrameCount(frameCount)
        ) else {
            throw LoadError.conversionFailed("buffer alloc failed")
        }
        file.framePosition = startFrame
        try file.read(into: raw, frameCount: AVAudioFrameCount(frameCount))

        // Already Float32 non-interleaved at the render rate with a
        // renderer-supported channel count? Use as-is.
        if sourceFormat.commonFormat == .pcmFormatFloat32,
           sourceFormat.sampleRate == sampleRate,
           !sourceFormat.isInterleaved,
           sourceFormat.channelCount <= 2
        {
            return raw
        }

        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: sampleRate,
            channels: min(sourceFormat.channelCount, 2),
            interleaved: false
        ), let converter = AVAudioConverter(
            from: sourceFormat, to: targetFormat
        ) else {
            throw LoadError.conversionFailed("no converter for formats")
        }

        let ratio = sampleRate / fileRate
        let capacity = AVAudioFrameCount(
            (Double(frameCount) * ratio).rounded(.up) + 64)
        guard let converted = AVAudioPCMBuffer(
            pcmFormat: targetFormat, frameCapacity: capacity
        ) else {
            throw LoadError.conversionFailed("buffer alloc failed")
        }

        var fed = false
        var conversionError: NSError?
        let status = converter.convert(
            to: converted, error: &conversionError
        ) { _, outStatus in
            if fed {
                outStatus.pointee = .endOfStream
                return nil
            }
            fed = true
            outStatus.pointee = .haveData
            return raw
        }
        if status == .error {
            throw LoadError.conversionFailed(
                conversionError?.localizedDescription ?? "convert failed")
        }
        guard converted.frameLength > 0 else {
            throw LoadError.emptySegment
        }
        return converted
    }
}

/// Renders a SessionCapture to a WAV under Application
/// Support/Jamn/bounces/, resolving pads against the current grid.
@MainActor
public enum SessionBounceService {

    public enum BounceError: Error, LocalizedError {
        case noAssignedPads

        public var errorDescription: String? {
            switch self {
            case .noAssignedPads:
                return "No pads in this take resolve against the current grid."
            }
        }
    }

    /// Default output directory: `{Application Support/Jamn}/bounces/`.
    public static func bouncesDir() throws -> URL {
        let base = try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent("Jamn", isDirectory: true)
        let dir = base.appendingPathComponent("bounces", isDirectory: true)
        try FileManager.default.createDirectory(
            at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// Bounce `session` using the current Launchpad grid. Buffers
    /// load and convert on a detached task (blocking IO); the render
    /// itself is deterministic pure Swift.
    public static func bounce(
        session: SessionCapture,
        assignments: [LaunchpadPad: PadAssignment],
        stemURLs: [String: URL],
        outputDirectory: URL,
        sampleRate: Double = 48_000
    ) async throws -> SessionBounceRenderer.Result {
        // Snapshot the segments we need (main-actor state) before
        // hopping off for IO.
        struct SegmentSpec: Sendable {
            let padRawValue: Int
            let url: URL
            let startSec: Double
            let endSec: Double
        }
        var specs: [SegmentSpec] = []
        for (pad, assignment) in assignments {
            guard let index = PadEventMapping.padIndex(for: pad),
                  let url = stemURLs[assignment.stem]
            else { continue }
            specs.append(SegmentSpec(
                padRawValue: index.rawValue,
                url: url,
                startSec: assignment.chop.startSec,
                endSec: assignment.chop.endSec
            ))
        }
        guard !specs.isEmpty else { throw BounceError.noAssignedPads }

        let loaded = specs
        let rate = sampleRate
        let padBuffers = await Task.detached(
            priority: .userInitiated
        ) { () -> [Int: AVAudioPCMBuffer] in
            var out: [Int: AVAudioPCMBuffer] = [:]
            for spec in loaded {
                do {
                    out[spec.padRawValue] = try ChopBufferLoader.loadSegment(
                        url: spec.url,
                        startSec: spec.startSec,
                        endSec: spec.endSec,
                        sampleRate: rate
                    )
                } catch {
                    // Skip unloadable segments — renderer treats the
                    // pad as unresolvable, matching replay no-ops.
                    print("[Bounce] skip pad \(spec.padRawValue): \(error)")
                }
            }
            return out
        }.value
        guard !padBuffers.isEmpty else { throw BounceError.noAssignedPads }

        return try SessionBounceRenderer.bounceSession(
            session,
            padBuffers: padBuffers,
            layout: EmptyLayout(),
            outputDirectory: outputDirectory,
            sampleRate: sampleRate
        )
    }
}
