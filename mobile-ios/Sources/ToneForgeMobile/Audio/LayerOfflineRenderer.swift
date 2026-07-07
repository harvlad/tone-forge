// LayerOfflineRenderer.swift
//
// Offline (faster-than-realtime) render of a LayerTimeline into an
// AAC-encoded .m4a file via `AVAudioEngine.enableManualRenderingMode`.
// Mirrors the live SampleBus + SampleVoicePool topology at a smaller
// scale so exported audio sounds like what the user hears in
// Play → Contribute → Samples:
//
//   voicePool ──> voiceMixer ──┬──> dry (1.0) ──┐
//                              │                ├──> mainMixer
//                              └──> reverb ──> wet (0.15) ──┘
//
// Scope for v1 (Phase 6c):
//   - `sampleOn` events render as one-shot player-node triggers at
//     sample-accurate positions on the manual-rendering clock.
//   - `sampleOff`, `noteOn`, `noteOff` are reported in the result but
//     not rendered (release fades are inaudible for the typical
//     short percussive samples LayerRecorder captures; open-jam MIDI
//     notes require a manual-rendering-mode PadSynth which is
//     deferred to a follow-up slice).
//   - Loops are flattened to one-shots. Curated packs mostly use
//     one-shot pads; loop-mode contributions are rare enough that
//     losing sustain in the export is an acceptable v1 compromise.
//
// Multi-pack layers: each sampleOn resolves its pack via
// `event.params.packIdOverride ?? timeline.activePackId` — the same
// rule LayerPlayer uses for replay — so takes recorded across a
// carousel swap export every hit, not just the fronted pack's.
// Song-derived (DNA) pads carry a `StemSlice` instead of a file; the
// pack-based overloads slice the parent stem on the fly when the
// caller supplies `stemFiles` (role → local URL), mirroring
// `SampleScheduler.preloadPack`. Events whose pack can't be resolved
// (deleted cache, another song's DNA pack, device-local samples) are
// skipped and surfaced in `RenderResult.unresolvedSampleEvents`.
//
// Output format: 44.1 kHz stereo AAC, 192 kb/s. `AVAudioFile` writes
// its buffers through Core Audio's AAC encoder transparently — the
// caller sees a single `write(from:)` per render block.

import Foundation
#if canImport(AVFoundation)
import AVFoundation
#endif
import ToneForgeEngine

/// Offline layer → m4a renderer. Kept as a class so the render call
/// can extend to async cancellation without changing the caller.
public final class LayerOfflineRenderer: @unchecked Sendable {

    // MARK: - Errors + results

    public enum RenderError: Error, LocalizedError {
        case noRenderableEvents
        case bufferLoadFailed(padIdx: Int, path: String)
        case engineStartFailed(String)
        case writeFailed(String)
        case unavailable

        public var errorDescription: String? {
            switch self {
            case .noRenderableEvents:
                return "Layer has no sample events to render."
            case .bufferLoadFailed(let idx, let path):
                return "Could not load pad \(idx) audio at \(path)."
            case .engineStartFailed(let msg):
                return "Offline engine start failed: \(msg)"
            case .writeFailed(let msg):
                return "Writing m4a failed: \(msg)"
            case .unavailable:
                return "Offline rendering is unavailable on this platform."
            }
        }
    }

    /// Reported back to the caller after a successful render. Surfaces
    /// the counts so callers can nudge users about note-events that
    /// were silently skipped ("Note: 4 instrument notes were not
    /// included in the export").
    public struct RenderResult: Sendable, Equatable {
        public let url: URL
        public let durationSec: Double
        public let renderedSampleEvents: Int
        public let skippedNoteEvents: Int
        public let skippedSampleOffEvents: Int
        /// sampleOn events whose pad audio couldn't be resolved —
        /// pack unavailable at export time, or a padIdx missing from
        /// the supplied buffers. Skipped, never mis-rendered with
        /// another pack's audio.
        public let unresolvedSampleEvents: Int

        public init(
            url: URL,
            durationSec: Double,
            renderedSampleEvents: Int,
            skippedNoteEvents: Int,
            skippedSampleOffEvents: Int,
            unresolvedSampleEvents: Int = 0
        ) {
            self.url = url
            self.durationSec = durationSec
            self.renderedSampleEvents = renderedSampleEvents
            self.skippedNoteEvents = skippedNoteEvents
            self.skippedSampleOffEvents = skippedSampleOffEvents
            self.unresolvedSampleEvents = unresolvedSampleEvents
        }
    }

    #if canImport(AVFoundation)
    /// Per-pad audio + gain used by the renderer. Buffers must be
    /// pre-converted to the target engine format (44.1 kHz stereo
    /// float). The pack-based convenience overload does that
    /// conversion via `AVAudioConverter`; tests build the map
    /// directly with synthesized buffers.
    public struct RenderablePad {
        public let buffer: AVAudioPCMBuffer
        public let gainDb: Double
        public init(buffer: AVAudioPCMBuffer, gainDb: Double = 0) {
            self.buffer = buffer
            self.gainDb = gainDb
        }
    }
    #endif

    // MARK: - Config

    /// Target render sample rate. Fixed at 44.1 kHz to match the
    /// live engine + m4a-friendly encoder input.
    public static let sampleRate: Double = 44_100

    /// Voice-pool size. Sized to comfortably cover overlapping tails
    /// on the 4×4 pad grid without hitting LRU eviction for typical
    /// contributions.
    public static let maxVoices: Int = 16

    /// Encoder block. AVAudioFile handles the PCM → AAC conversion
    /// internally when we pass compressed settings.
    private static var m4aSettings: [String: Any] {
        [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: sampleRate,
            AVNumberOfChannelsKey: 2,
            AVEncoderBitRateKey: 192_000,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ]
    }

    public init() {}

    // MARK: - Render (buffers)

    #if canImport(AVFoundation)
    /// Pack-agnostic render over pre-loaded, format-normalized pad
    /// buffers keyed by padIdx alone — every event resolves against
    /// the same map regardless of its pack. This overload is what
    /// most tests drive so the audio path is exercised without
    /// needing pack files on disk.
    ///
    /// The render runs synchronously (fast — Apple documents manual
    /// rendering at many times real-time on Apple silicon), but the
    /// caller usually invokes it from a background `Task` so the UI
    /// stays live.
    public func render(
        timeline: LayerTimeline,
        pads: [Int: RenderablePad],
        outputURL: URL,
        tailSec: Double = 3.0
    ) throws -> RenderResult {
        try renderCore(
            timeline: timeline,
            padFor: { _, padIdx in pads[padIdx] },
            outputURL: outputURL,
            tailSec: tailSec
        )
    }

    /// Multi-pack render. Buffers are keyed by packId → padIdx; each
    /// sampleOn event looks up `packIdOverride ?? activePackId`, so
    /// hits land on the pack they were recorded against. Events whose
    /// pack has no entry are counted in `unresolvedSampleEvents`
    /// rather than borrowing another pack's audio.
    public func render(
        timeline: LayerTimeline,
        padsByPack: [String: [Int: RenderablePad]],
        outputURL: URL,
        tailSec: Double = 3.0
    ) throws -> RenderResult {
        try renderCore(
            timeline: timeline,
            padFor: { packId, padIdx in
                packId.flatMap { padsByPack[$0]?[padIdx] }
            },
            outputURL: outputURL,
            tailSec: tailSec
        )
    }

    /// Shared engine + render loop. `padFor` receives the event's
    /// resolved packId (`packIdOverride ?? timeline.activePackId`)
    /// and padIdx and returns the buffer to trigger, or nil to skip.
    private func renderCore(
        timeline: LayerTimeline,
        padFor: (String?, Int) -> RenderablePad?,
        outputURL: URL,
        tailSec: Double
    ) throws -> RenderResult {
        let sr = Self.sampleRate

        // Partition events. Only sampleOn renders; the rest are counted
        // so the UI can call out what was skipped.
        var sampleOnEvents: [LayerEvent] = []
        var skippedSampleOff = 0
        var skippedNotes = 0
        for e in timeline.events {
            switch e.kind {
            case .sampleOn:  sampleOnEvents.append(e)
            case .sampleOff: skippedSampleOff += 1
            case .noteOn, .noteOff: skippedNotes += 1
            }
        }
        guard !sampleOnEvents.isEmpty else {
            throw RenderError.noRenderableEvents
        }
        sampleOnEvents.sort { $0.songTimeSec < $1.songTimeSec }

        // 1. Build offline engine + graph.
        guard let format = AVAudioFormat(standardFormatWithSampleRate: sr,
                                         channels: 2) else {
            throw RenderError.engineStartFailed("no output format")
        }
        let engine = AVAudioEngine()

        // Bus mirror: voiceMixer → dry + reverb-wet → mainMixer.
        let voiceMixer = AVAudioMixerNode()
        let dry = AVAudioMixerNode()
        let reverb = AVAudioUnitReverb()
        reverb.loadFactoryPreset(.mediumHall)
        reverb.wetDryMix = 100
        let wet = AVAudioMixerNode()
        engine.attach(voiceMixer)
        engine.attach(dry)
        engine.attach(reverb)
        engine.attach(wet)
        engine.connect(voiceMixer, to: dry, format: format)
        engine.connect(voiceMixer, to: reverb, format: format)
        engine.connect(reverb, to: wet, format: format)
        engine.connect(dry, to: engine.mainMixerNode, format: format)
        engine.connect(wet, to: engine.mainMixerNode, format: format)
        dry.outputVolume = 1.0
        wet.outputVolume = 0.15

        // Voice pool.
        struct Voice {
            let player: AVAudioPlayerNode
            let mixer: AVAudioMixerNode
            var freeAtSample: AVAudioFramePosition
        }
        var voices: [Voice] = []
        voices.reserveCapacity(Self.maxVoices)
        for _ in 0..<Self.maxVoices {
            let player = AVAudioPlayerNode()
            let mixer = AVAudioMixerNode()
            engine.attach(player)
            engine.attach(mixer)
            engine.connect(player, to: mixer, format: format)
            engine.connect(mixer, to: voiceMixer, format: format)
            mixer.outputVolume = 1.0
            voices.append(Voice(player: player, mixer: mixer, freeAtSample: 0))
        }

        // 2. Enable manual rendering + start engine.
        do {
            try engine.enableManualRenderingMode(
                .offline, format: format, maximumFrameCount: 4096)
        } catch {
            throw RenderError.engineStartFailed(error.localizedDescription)
        }
        do { try engine.start() } catch {
            throw RenderError.engineStartFailed(error.localizedDescription)
        }
        defer { engine.stop() }
        for v in voices { v.player.play() }

        // 3. Schedule sampleOn events. Simple LRU-by-freeAt allocation:
        //    prefer a voice whose previous buffer has already finished
        //    at the event's start time; else steal the earliest-free
        //    voice (buffer overlap on the same node is the accepted
        //    v1 degradation — very rare with a 16-voice pool).
        var renderedCount = 0
        var unresolvedCount = 0
        var latestEndSample: AVAudioFramePosition = 0
        for event in sampleOnEvents {
            guard let padIdx = event.params.padIdx else { continue }
            let packId = event.params.packIdOverride ?? timeline.activePackId
            guard let padDef = padFor(packId, padIdx) else {
                unresolvedCount += 1
                continue
            }

            let startSample = AVAudioFramePosition(event.songTimeSec * sr)
            let bufFrames = AVAudioFramePosition(padDef.buffer.frameLength)
            let endSample = startSample + bufFrames
            if endSample > latestEndSample { latestEndSample = endSample }

            // Voice pick: first available; else earliest-free.
            var chosen = 0
            var minFree = voices[0].freeAtSample
            for i in voices.indices {
                if voices[i].freeAtSample <= startSample {
                    chosen = i
                    minFree = voices[i].freeAtSample
                    break
                }
                if voices[i].freeAtSample < minFree {
                    minFree = voices[i].freeAtSample
                    chosen = i
                }
            }
            let voice = voices[chosen]

            let velocity = Float(event.params.velocity ?? 1.0)
            let gainLinear = Float(pow(10.0, padDef.gainDb / 20.0))
            voice.mixer.outputVolume = max(0, min(2, gainLinear * velocity))

            let time = AVAudioTime(sampleTime: startSample, atRate: sr)
            voice.player.scheduleBuffer(
                padDef.buffer,
                at: time,
                options: [],
                completionHandler: nil
            )
            voices[chosen].freeAtSample = endSample
            renderedCount += 1
        }

        // 4. Compute total render frames = max(durationSec, last sample
        //    tail end) + user tailSec so the final voice's tail isn't
        //    truncated by the file cut.
        let durationBoundSec = max(timeline.durationSec, Double(latestEndSample) / sr)
        let totalSec = durationBoundSec + max(0, tailSec)
        let totalFrames = AVAudioFramePosition(totalSec * sr)

        // 5. Open output m4a.
        let audioFile: AVAudioFile
        do {
            audioFile = try AVAudioFile(
                forWriting: outputURL,
                settings: Self.m4aSettings
            )
        } catch {
            throw RenderError.writeFailed(error.localizedDescription)
        }

        // 6. Render loop.
        guard let renderBuf = AVAudioPCMBuffer(
            pcmFormat: engine.manualRenderingFormat,
            frameCapacity: 4096
        ) else {
            throw RenderError.engineStartFailed("could not allocate render buffer")
        }

        while engine.manualRenderingSampleTime < totalFrames {
            let remaining = totalFrames - engine.manualRenderingSampleTime
            let capped = min(AVAudioFramePosition(renderBuf.frameCapacity), remaining)
            let framesThisPass = AVAudioFrameCount(capped)
            do {
                let status = try engine.renderOffline(framesThisPass, to: renderBuf)
                switch status {
                case .success:
                    try audioFile.write(from: renderBuf)
                case .cannotDoInCurrentContext, .insufficientDataFromInputNode:
                    // Neither should happen in .offline mode with player-
                    // node inputs; break to avoid spinning if it does.
                    return RenderResult(
                        url: outputURL,
                        durationSec: Double(engine.manualRenderingSampleTime) / sr,
                        renderedSampleEvents: renderedCount,
                        skippedNoteEvents: skippedNotes,
                        skippedSampleOffEvents: skippedSampleOff,
                        unresolvedSampleEvents: unresolvedCount
                    )
                case .error:
                    throw RenderError.writeFailed("renderOffline returned .error")
                @unknown default:
                    break
                }
            } catch let e as RenderError {
                throw e
            } catch {
                throw RenderError.writeFailed(error.localizedDescription)
            }
        }

        return RenderResult(
            url: outputURL,
            durationSec: totalSec,
            renderedSampleEvents: renderedCount,
            skippedNoteEvents: skippedNotes,
            skippedSampleOffEvents: skippedSampleOff,
            unresolvedSampleEvents: unresolvedCount
        )
    }

    // MARK: - Render (packs)

    /// Convenience: load pad buffers from every supplied
    /// `ResolvedSamplePack`, format-converting to the engine's target
    /// format, then delegate to the multi-pack buffer renderer. Only
    /// pads the timeline's sampleOn events actually reference are
    /// loaded, so a two-pack take costs two packs' worth of I/O only
    /// in the worst case.
    ///
    /// Song-derived pads (`filename == nil`, backed by a `StemSlice`)
    /// slice their window out of `stemFiles[slice.stemRole]`. A stem
    /// that's missing from the map or unreadable degrades to a silent
    /// pad — the event counts as unresolved, matching live replay's
    /// padNotFound — rather than aborting the whole export.
    public func render(
        timeline: LayerTimeline,
        packs: [ResolvedSamplePack],
        stemFiles: [String: URL] = [:],
        outputURL: URL,
        tailSec: Double = 3.0
    ) throws -> RenderResult {
        guard let targetFormat = AVAudioFormat(
            standardFormatWithSampleRate: Self.sampleRate,
            channels: 2
        ) else {
            throw RenderError.engineStartFailed("no target format")
        }

        // (packId, padIdx) pairs the events reference — the load set.
        var referenced: [String: Set<Int>] = [:]
        for event in timeline.events where event.kind == .sampleOn {
            guard let padIdx = event.params.padIdx,
                  let packId = event.params.packIdOverride
                      ?? timeline.activePackId
            else { continue }
            referenced[packId, default: []].insert(padIdx)
        }

        var padsByPack: [String: [Int: RenderablePad]] = [:]
        for pack in packs {
            guard let wanted = referenced[pack.pack.packId] else { continue }
            var pads: [Int: RenderablePad] = [:]
            for padDef in pack.pack.pads where wanted.contains(padDef.padIdx) {
                if let url = pack.padFileURLs[padDef.padIdx] {
                    do {
                        let buf = try Self.loadBuffer(at: url, into: targetFormat)
                        pads[padDef.padIdx] = RenderablePad(
                            buffer: buf, gainDb: padDef.gainDb)
                    } catch {
                        throw RenderError.bufferLoadFailed(
                            padIdx: padDef.padIdx, path: url.path)
                    }
                } else if let slice = padDef.stemSlice,
                          let stemURL = stemFiles[slice.stemRole],
                          let buf = try? Self.loadBuffer(
                              at: stemURL, slice: slice, into: targetFormat) {
                    // try? — an unreadable stem mirrors a missing one
                    // (silent pad, event unresolved), the same
                    // degradation SampleScheduler.preloadPack applies.
                    pads[padDef.padIdx] = RenderablePad(
                        buffer: buf, gainDb: padDef.gainDb)
                }
            }
            padsByPack[pack.pack.packId] = pads
        }

        return try render(
            timeline: timeline,
            padsByPack: padsByPack,
            outputURL: outputURL,
            tailSec: tailSec
        )
    }

    /// Single-pack convenience — delegates to the multi-pack overload.
    /// Events resolved to a different pack are counted as unresolved,
    /// never rendered with this pack's audio.
    public func render(
        timeline: LayerTimeline,
        pack: ResolvedSamplePack,
        stemFiles: [String: URL] = [:],
        outputURL: URL,
        tailSec: Double = 3.0
    ) throws -> RenderResult {
        try render(
            timeline: timeline,
            packs: [pack],
            stemFiles: stemFiles,
            outputURL: outputURL,
            tailSec: tailSec
        )
    }

    // MARK: - Buffer loading

    /// Load an audio file — or, when `slice` is set, just its
    /// `[startSec, endSec]` window — into an `AVAudioPCMBuffer`
    /// matching `targetFormat`. Uses `AVAudioConverter` when the
    /// file's processing format differs from the target so
    /// `AVAudioPlayerNode` can consume it without a per-voice
    /// sample-rate step.
    ///
    /// Sliced buffers are peak-normalized to the same -1 dBFS target
    /// the live scheduler applies at preload, so a DNA chop exports
    /// at the loudness the user heard when they recorded it. Whole
    /// files skip normalization — curated pack one-shots ship
    /// pre-mastered near target, so it would be a ~no-op there.
    static func loadBuffer(
        at url: URL,
        slice: StemSlice? = nil,
        into targetFormat: AVAudioFormat
    ) throws -> AVAudioPCMBuffer {
        let file = try AVAudioFile(forReading: url)
        let srcFormat = file.processingFormat

        // Frame window. Same math as SampleScheduler.loadBuffer so a
        // chop exports the exact audio it plays live.
        let startFrame: AVAudioFramePosition
        let frameCount: AVAudioFrameCount
        if let slice {
            let sr = srcFormat.sampleRate
            startFrame = AVAudioFramePosition(max(0, slice.startSec) * sr)
            let endFrame = AVAudioFramePosition(
                max(slice.startSec, slice.endSec) * sr)
            let requested = max(0, endFrame - startFrame)
            let clipped = min(requested, max(0, file.length - startFrame))
            frameCount = AVAudioFrameCount(clipped)
        } else {
            startFrame = 0
            frameCount = AVAudioFrameCount(file.length)
        }
        guard frameCount > 0,
              let srcBuf = AVAudioPCMBuffer(
                  pcmFormat: srcFormat,
                  frameCapacity: frameCount
              )
        else {
            throw RenderError.writeFailed("no source buffer")
        }
        file.framePosition = startFrame
        try file.read(into: srcBuf, frameCount: frameCount)

        let outBuf: AVAudioPCMBuffer
        if srcFormat.isEqual(targetFormat) {
            outBuf = srcBuf
        } else {
            guard let converter = AVAudioConverter(from: srcFormat, to: targetFormat) else {
                throw RenderError.writeFailed("no converter")
            }
            let ratio = targetFormat.sampleRate / srcFormat.sampleRate
            // +32 frames of slack for the converter's internal state.
            let outCapacity = AVAudioFrameCount(Double(srcBuf.frameLength) * ratio) + 32
            guard let dstBuf = AVAudioPCMBuffer(
                pcmFormat: targetFormat,
                frameCapacity: outCapacity
            ) else {
                throw RenderError.writeFailed("no dst buffer")
            }
            var provided = false
            var convError: NSError?
            _ = converter.convert(to: dstBuf, error: &convError) { _, outStatus in
                if provided {
                    outStatus.pointee = .endOfStream
                    return nil
                }
                provided = true
                outStatus.pointee = .haveData
                return srcBuf
            }
            if let err = convError { throw err }
            outBuf = dstBuf
        }
        if slice != nil { Self.normalizePeak(outBuf) }
        return outBuf
    }

    /// Live-parity loudness for stem slices. Value + algorithm match
    /// `SampleScheduler.normalizeTargetPeak` / `normalizePeak` — keep
    /// them in sync or exports will sit at a different level than
    /// live playback.
    private static let normalizeTargetPeak: Float = 0.891  // -1 dBFS

    private static func normalizePeak(_ buf: AVAudioPCMBuffer) {
        guard let channels = buf.floatChannelData else { return }
        let frameCount = Int(buf.frameLength)
        let channelCount = Int(buf.format.channelCount)
        guard frameCount > 0, channelCount > 0 else { return }
        var peak: Float = 0
        for c in 0..<channelCount {
            let ptr = channels[c]
            for i in 0..<frameCount {
                let v = abs(ptr[i])
                if v > peak { peak = v }
            }
        }
        guard peak > 1e-6 else { return }
        let gain = normalizeTargetPeak / peak
        guard abs(gain - 1.0) > 0.01 else { return }
        for c in 0..<channelCount {
            let ptr = channels[c]
            for i in 0..<frameCount {
                ptr[i] *= gain
            }
        }
    }
    #else
    // Non-Apple platform stub. Not expected in production paths but
    // lets the file compile under `swift build` without AVFoundation.
    public func render(
        timeline: LayerTimeline,
        pads: [Int: Never],
        outputURL: URL,
        tailSec: Double = 3.0
    ) throws -> RenderResult {
        throw RenderError.unavailable
    }
    #endif
}
