// SessionBounceRenderer.swift
//
// Offline bounce of a SessionCapture to an audio file (P6). Replays
// the captured events against the same ModeRouter the live app uses
// and mixes the result through a mirror of the live P1 bus topology:
//
//   samplePads ──> chopBus(gains.chop) ─┐
//   synth      ──> voiceBus(gains.voice)─┤
//                                        v
//                              sharedBus(gains.layer)
//                                 │            │
//                        dry(gains.dry)   DeterministicReverb
//                                 │            │
//                                 │       wet(gains.wet)
//                                 v            v
//                               main  <── songAudio (attested only,
//                                          direct — bypasses shared,
//                                          mirroring StemPlayer)
//
// DESIGN DECISION — pure-Swift mixer, not AVAudioEngine manual
// rendering. The P6 hard gate is that rendering the same session ten
// times produces BIT-IDENTICAL WAV files. AVAudioUnitReverb fails
// that gate: an offline manual-rendering graph fed the identical
// buffer produced different sample bytes in 4 of 5 repeat renders
// (measured on macOS 15 / Xcode; the AU appears to randomise its
// internal modulation). Rather than keep the engine and bolt a
// deterministic wet path onto it, the whole mix is done here in
// plain Float arrays: sample events are buffer adds at integer frame
// offsets, the synth part is pre-rendered sample-accurately with
// WavetableSynth (pure DSP, no RNG), and the "graph" reduces to the
// gain math above with DeterministicReverb (fixed FDN, no RNG) on
// the wet branch. Every operation is ordered scalar arithmetic, so
// bit-identity holds by construction — and is still verified 10× in
// SessionBounceRendererTests. AVFoundation is used only at the
// edges: AVAudioPCMBuffer inputs and AVAudioFile output.
//
// Reverb seconds: the live AudioEngine quantises reverbSeconds to
// the nearest AVAudioUnitReverb factory preset (presetForSeconds:
// <0.8 smallRoom, <1.4 mediumRoom, <2.2 largeRoom, <2.8 mediumHall,
// <3.6 largeHall, else cathedral). The FDN has a continuous T60
// parameter, so `gains.reverbSeconds` drives the tail length
// DIRECTLY — a continuous superset of the preset table rather than a
// re-quantisation of it.
//
// Semantics mirrored from LayerOfflineRenderer (legacy m4a export):
//   - one-shot bounce: `.releaseSample` is counted skipped, never
//     rendered (release fades are inaudible for the short percussive
//     material sessions capture).
//   - events with timestamp < 0 (count-in region) are skipped.
//   - `.gap` markers and unresolvable pads are skipped.
//
// Buffer contract: `padBuffers` and `songAudio` must be Float32 PCM
// at the render sample rate (48 kHz canonical), mono or stereo.
// Mono is sent to both channels. Buffers at other rates are NOT
// resampled — callers convert up front (AVAudioConverter), exactly
// as the legacy renderer's pack loader does.

import Foundation
import AVFoundation

/// Output container for a bounce.
public enum BounceFormat: String, Sendable {
    /// Float32 PCM WAV — the bit-identical path.
    case wav
    /// AAC 256 kb/s in an .m4a container. Encoder output is NOT
    /// guaranteed bit-identical run-to-run; use for sharing only.
    case m4aAAC256
}

/// The bus gains of the live P1 graph, snapshotted for the bounce.
/// Defaults match the app's shipped mix.
public struct BounceGains: Sendable, Equatable {
    /// Synth (voice) bus level.
    public var voice: Float
    /// Sample (chop) bus level.
    public var chop: Float
    /// Shared contribution-bus level (0…2; clamped when applied).
    public var layer: Float
    /// Dry branch level into the main mix.
    public var dry: Float
    /// Wet (reverb) branch level into the main mix.
    public var wet: Float
    /// Reverb tail length in seconds — the FDN's T60 (see header).
    public var reverbSeconds: Double

    public init(
        voice: Float = 0.9,
        chop: Float = 0.55,
        layer: Float = 1.0,
        dry: Float = 0.9,
        wet: Float = 0.3,
        reverbSeconds: Double = 2.0
    ) {
        self.voice = voice
        self.chop = chop
        self.layer = layer
        self.dry = dry
        self.wet = wet
        self.reverbSeconds = reverbSeconds
    }
}

/// Offline session → audio-file renderer. Stateless; one static
/// entry point so callers can't hold a half-configured renderer.
public struct SessionBounceRenderer {

    // MARK: - Errors + results

    public enum RenderError: Error, Equatable, LocalizedError {
        /// `includeOriginalSong` requested without the user having
        /// accepted the rights attestation. Thrown before ANY other
        /// work (even when songAudio is nil) so the gate can't be
        /// probed around.
        case attestationRequired
        /// Nothing would sound: no resolvable sample triggers, no
        /// synth note-ons, no scheduled song. Thrown before the
        /// output file is created.
        case noRenderableEvents
        /// Output file creation or write failed.
        case writeFailed(String)

        public var errorDescription: String? {
            switch self {
            case .attestationRequired:
                return "Including the original song requires the rights attestation."
            case .noRenderableEvents:
                return "Session has no renderable events."
            case .writeFailed(let msg):
                return "Writing bounce failed: \(msg)"
            }
        }
    }

    /// Post-render report. Skip counts let the UI call out what the
    /// bounce dropped ("2 count-in hits were not included").
    public struct Result: Sendable, Equatable {
        public let url: URL
        /// Total rendered length including the tail, seconds.
        public let durationSec: Double
        /// Sample triggers actually scheduled into the mix.
        public let renderedSampleEvents: Int
        /// Synth note-ons applied to the offline synth track.
        public let renderedNoteEvents: Int
        /// Count-in events (t < 0), releases, gaps, and triggers
        /// whose pad had no buffer. Synth note-offs are neither
        /// rendered nor skipped — they shape the note track.
        public let skippedEvents: Int

        public init(
            url: URL,
            durationSec: Double,
            renderedSampleEvents: Int,
            renderedNoteEvents: Int,
            skippedEvents: Int
        ) {
            self.url = url
            self.durationSec = durationSec
            self.renderedSampleEvents = renderedSampleEvents
            self.renderedNoteEvents = renderedNoteEvents
            self.skippedEvents = skippedEvents
        }
    }

    // MARK: - Entry point

    /// Render `session` to `outputDirectory/<sessionId>.<ext>`.
    /// Synchronous; callers wrap in a background Task. Overwrites an
    /// existing file at the destination.
    public static func bounceSession(
        _ session: SessionCapture,
        padBuffers: [Int: AVAudioPCMBuffer],
        layout: any GridLayoutProviding,
        gains: BounceGains = BounceGains(),
        synthParams: WavetableSynthParams = WavetableSynthParams(),
        songAudio: AVAudioPCMBuffer? = nil,
        includeOriginalSong: Bool = false,
        attestationAccepted: Bool = false,
        format: BounceFormat = .wav,
        outputDirectory: URL,
        sampleRate: Double = 48_000,
        tailSec: Double = 3.0
    ) throws -> Result {
        // 1. Attestation tripwire FIRST — before touching events or
        //    disk, and regardless of whether songAudio was supplied.
        if includeOriginalSong && !attestationAccepted {
            throw RenderError.attestationRequired
        }
        let songScheduled = includeOriginalSong && songAudio != nil

        // 2. Resolve events through the same router the live app
        //    uses so the bounce hears exactly what the performer
        //    played (mode semantics included).
        struct SampleTrigger {
            let frame: Int
            let buffer: AVAudioPCMBuffer
            let gain: Float   // velocity, clamped 0…1
        }
        struct NoteEvent {
            let frame: Int
            let order: Int    // stable tiebreak for equal frames
            let isOn: Bool
            let midi: Int
            let velocity: Float
        }

        var triggers: [SampleTrigger] = []
        var noteEvents: [NoteEvent] = []
        var skipped = 0
        var noteOnCount = 0

        for event in session.events {
            // Count-in region: never rendered.
            if event.timestamp < 0 {
                skipped += 1
                continue
            }
            let frame = Int((event.timestamp * sampleRate).rounded())
            switch ModeRouter.resolve(event, mode: session.appMode, layout: layout) {
            case .triggerSample(let padIdx):
                // Missing / non-float buffers can't sound: skipped.
                guard let buf = padBuffers[padIdx],
                      buf.floatChannelData != nil,
                      buf.frameLength > 0 else {
                    skipped += 1
                    continue
                }
                let velocity = Float(min(max(event.velocity, 0), 1))
                triggers.append(
                    SampleTrigger(frame: frame, buffer: buf, gain: velocity))
            case .releaseSample:
                // One-shot bounce semantics (see header).
                skipped += 1
            case .synthNoteOn(let midi, let velocity, _):
                noteEvents.append(NoteEvent(
                    frame: frame, order: noteEvents.count, isOn: true,
                    midi: midi, velocity: Float(min(max(velocity, 0), 1))))
                noteOnCount += 1
            case .synthNoteOff(let midi):
                noteEvents.append(NoteEvent(
                    frame: frame, order: noteEvents.count, isOn: false,
                    midi: midi, velocity: 0))
            case .padSynthNote:
                // Jam in Key notes voice on the mobile PadSynth,
                // which has no offline render path yet — jam grid
                // presses don't land in bounces (Phase 7 v1).
                skipped += 1
            case .none:
                skipped += 1
            }
        }

        // 3. Renderability gate — BEFORE any file is created.
        guard !triggers.isEmpty || noteOnCount > 0 || songScheduled else {
            throw RenderError.noRenderableEvents
        }

        // 4. Total length: cover the last sample tail, the session's
        //    own event span, and the full song (when scheduled), plus
        //    the caller's tail so reverb/synth releases ring out.
        var lastSampleEnd = 0
        for t in triggers {
            lastSampleEnd = max(lastSampleEnd, t.frame + Int(t.buffer.frameLength))
        }
        let sessionFrames = Int((max(0, session.durationSec) * sampleRate).rounded())
        let songFrames = songScheduled ? Int(songAudio!.frameLength) : 0
        let baseFrames = max(lastSampleEnd, max(sessionFrames, songFrames))
        let totalFrames = max(1, baseFrames + Int((max(0, tailSec) * sampleRate).rounded()))

        // 5. Mix. sharedL/R is the contribution bus (chop + voice).
        var sharedL = [Float](repeating: 0, count: totalFrames)
        var sharedR = [Float](repeating: 0, count: totalFrames)

        // 5a. Sample triggers: buffer adds at integer offsets. Adds
        //     happen in captured-event order — a fixed order, so the
        //     Float accumulation is deterministic.
        for t in triggers {
            addBuffer(t.buffer, at: t.frame, gain: t.gain * gains.chop,
                      intoL: &sharedL, intoR: &sharedR)
        }

        // 5b. Offline synth track: pre-render the whole note part
        //     sample-accurately (see renderSynthTrack), then mix at
        //     the voice-bus gain.
        if noteOnCount > 0 {
            let boundaries = noteEvents
                .sorted { ($0.frame, $0.order) < ($1.frame, $1.order) }
                .map {
                    SynthBoundary(
                        frame: $0.frame, isOn: $0.isOn,
                        midi: $0.midi, velocity: $0.velocity)
                }
            let (synthL, synthR) = renderSynthTrack(
                events: boundaries,
                params: synthParams,
                sampleRate: sampleRate,
                totalFrames: totalFrames
            )
            let v = gains.voice
            for i in 0..<totalFrames {
                sharedL[i] += synthL[i] * v
                sharedR[i] += synthR[i] * v
            }
        }

        // 5c. Shared-bus (layer) gain, clamped to the live control's
        //     0…2 range.
        let layer = min(max(gains.layer, 0), 2)
        if layer != 1 {
            for i in 0..<totalFrames {
                sharedL[i] *= layer
                sharedR[i] *= layer
            }
        }

        // 5d. Main mix = dry + wet branches. The reverb is skipped
        //     entirely at wet == 0 (identical output either way —
        //     0 × wet is exactly 0 — just cheaper).
        var mainL = [Float](repeating: 0, count: totalFrames)
        var mainR = [Float](repeating: 0, count: totalFrames)
        let dry = gains.dry
        for i in 0..<totalFrames {
            mainL[i] = sharedL[i] * dry
            mainR[i] = sharedR[i] * dry
        }
        if gains.wet > 0 {
            let reverb = DeterministicReverb(
                sampleRate: sampleRate, reverbSeconds: gains.reverbSeconds)
            let (wetL, wetR) = reverb.process(left: sharedL, right: sharedR)
            let wet = gains.wet
            for i in 0..<totalFrames {
                mainL[i] += wetL[i] * wet
                mainR[i] += wetR[i] * wet
            }
        }

        // 5e. Original song: direct to main at unity, frame 0 —
        //     mirrors the live StemPlayer → mainMixer routing that
        //     bypasses sharedBus (and therefore the reverb).
        if songScheduled, let song = songAudio {
            addBuffer(song, at: 0, gain: 1.0, intoL: &mainL, intoR: &mainR)
        }

        // 6. Write the file.
        let ext = format == .wav ? "wav" : "m4a"
        let url = outputDirectory
            .appendingPathComponent(session.sessionId.uuidString)
            .appendingPathExtension(ext)
        do {
            try FileManager.default.createDirectory(
                at: outputDirectory, withIntermediateDirectories: true)
            if FileManager.default.fileExists(atPath: url.path) {
                try FileManager.default.removeItem(at: url)
            }
        } catch {
            throw RenderError.writeFailed(error.localizedDescription)
        }

        let settings: [String: Any]
        switch format {
        case .wav:
            settings = [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: sampleRate,
                AVNumberOfChannelsKey: 2,
                AVLinearPCMBitDepthKey: 32,
                AVLinearPCMIsFloatKey: true,
                AVLinearPCMIsNonInterleaved: false,
            ]
        case .m4aAAC256:
            settings = [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: sampleRate,
                AVNumberOfChannelsKey: 2,
                AVEncoderBitRateKey: 256_000,
            ]
        }

        do {
            let file = try AVAudioFile(
                forWriting: url,
                settings: settings,
                commonFormat: .pcmFormatFloat32,
                interleaved: false
            )
            try writeChunked(
                left: mainL, right: mainR,
                sampleRate: sampleRate, to: file)
        } catch let e as RenderError {
            throw e
        } catch {
            throw RenderError.writeFailed(error.localizedDescription)
        }

        return Result(
            url: url,
            durationSec: Double(totalFrames) / sampleRate,
            renderedSampleEvents: triggers.count,
            renderedNoteEvents: noteOnCount,
            skippedEvents: skipped
        )
    }

    // MARK: - Mixing helpers

    /// Add `buf` into the target arrays starting at `frame`, scaled
    /// by `gain`. Mono buffers feed both channels; the second channel
    /// of stereo(+) buffers feeds the right. Honors the buffer's
    /// stride so interleaved Float32 buffers also read correctly.
    /// Frames past the end of the target are dropped (callers size
    /// the mix to cover every trigger, so this only trims songAudio
    /// longer than the render — which cannot happen by construction).
    private static func addBuffer(
        _ buf: AVAudioPCMBuffer,
        at frame: Int,
        gain: Float,
        intoL: inout [Float],
        intoR: inout [Float]
    ) {
        guard let ch = buf.floatChannelData else { return }
        let bufFrames = Int(buf.frameLength)
        guard bufFrames > 0, frame >= 0, frame < intoL.count else { return }
        let n = min(bufFrames, intoL.count - frame)
        let stride = buf.stride
        let left = ch[0]
        let right = buf.format.channelCount > 1 ? ch[1] : ch[0]
        for i in 0..<n {
            intoL[frame + i] += left[i * stride] * gain
            intoR[frame + i] += right[i * stride] * gain
        }
    }

    /// Write the mixed arrays to `file` in 4096-frame chunks (bounds
    /// the AVAudioPCMBuffer staging allocation; AVAudioFile appends).
    private static func writeChunked(
        left: [Float],
        right: [Float],
        sampleRate: Double,
        to file: AVAudioFile
    ) throws {
        guard let fmt = AVAudioFormat(
            standardFormatWithSampleRate: sampleRate, channels: 2
        ), let chunk = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: 4096) else {
            throw RenderError.writeFailed("could not allocate write buffer")
        }
        let total = left.count
        var written = 0
        while written < total {
            let n = min(4096, total - written)
            chunk.frameLength = AVAudioFrameCount(n)
            let dstL = chunk.floatChannelData![0]
            let dstR = chunk.floatChannelData![1]
            for i in 0..<n {
                dstL[i] = left[written + i]
                dstR[i] = right[written + i]
            }
            try file.write(from: chunk)
            written += n
        }
    }
}

// MARK: - Synth track rendering (concrete)

extension SessionBounceRenderer {

    /// One resolved synth note boundary. Internal so the concrete
    /// track renderer below can live outside the giant entry point.
    fileprivate struct SynthBoundary {
        let frame: Int
        let isOn: Bool
        let midi: Int
        let velocity: Float
    }

    /// Render the whole synth part into one stereo track BEFORE the
    /// mix: note events (sorted ascending by frame, stable within
    /// equal frames) partition the timeline into segments; each
    /// segment is rendered with WavetableSynth.render (which
    /// OVERWRITES — every frame range is written exactly once), and
    /// the pending noteOn/noteOff queue is fed exactly at segment
    /// boundaries so attacks land on their captured frame. Segments
    /// render in ≤ 4096-frame blocks: the synth's internal scratch
    /// caps a single render call at 8192 frames, and 4096 matches
    /// the live engine's callback ceiling.
    fileprivate static func renderSynthTrack(
        events: [SynthBoundary],
        params: WavetableSynthParams,
        sampleRate: Double,
        totalFrames: Int
    ) -> ([Float], [Float]) {
        var left = [Float](repeating: 0, count: totalFrames)
        var right = [Float](repeating: 0, count: totalFrames)
        let synth = WavetableSynth(sampleRate: sampleRate)
        synth.setParams(params)  // drained at the first render call

        // Render [from, to) into the big arrays at the same offsets.
        func renderSegment(from: Int, to: Int) {
            var cursor = from
            while cursor < to {
                let n = min(4096, to - cursor)
                left.withUnsafeMutableBufferPointer { lp in
                    right.withUnsafeMutableBufferPointer { rp in
                        synth.render(
                            left: lp.baseAddress! + cursor,
                            right: rp.baseAddress! + cursor,
                            frames: n
                        )
                    }
                }
                cursor += n
            }
        }

        var cursor = 0
        for e in events {
            let f = min(max(e.frame, 0), totalFrames)
            if f > cursor {
                renderSegment(from: cursor, to: f)
                cursor = f
            }
            // Queued now; WavetableSynth drains pending events at the
            // start of the NEXT render call — i.e. exactly frame f.
            if e.isOn {
                synth.noteOn(midi: e.midi, velocity: e.velocity)
            } else {
                synth.noteOff(midi: e.midi)
            }
        }
        renderSegment(from: cursor, to: totalFrames)
        return (left, right)
    }
}
