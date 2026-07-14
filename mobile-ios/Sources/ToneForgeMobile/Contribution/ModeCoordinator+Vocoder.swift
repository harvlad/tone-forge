// ModeCoordinator+Vocoder.swift
//
// P5 vocoder capture: persists a finished take (conditioned +
// classified, source .vocoded so it is never uploaded) and builds the
// deterministic carrier program for each vocoder mode, degrading one
// step per missing audio source (stem → chord grid → drone) so a
// capture always sounds. Split from ModeCoordinator.swift.

import AVFoundation
import Foundation
import ToneForgeEngine

extension ModeCoordinator {

    // MARK: - Vocoder capture (P5)

    /// Finished vocoder take → conditioned → classified → saved
    /// (source .vocoded, purple, neverUpload enforced by the metadata
    /// init) → assigned → playable. Mirrors `saveMicCapture` but
    /// conditions the PROCESSED audio and stamps the mode used.
    @discardableResult
    public func saveVocoderTake(
        _ take: VocoderCaptureSession.Take, toGridPad gridRaw: Int
    ) async throws -> PadSampleMetadata {
        let rate = AudioEngine.canonicalSampleRate
        let processed = RecordingProcessor.process(
            take.processed, sampleRate: rate
        )
        guard !processed.samples.isEmpty else {
            throw MicCaptureError.silentCapture
        }
        let (cls, confidence) = HeuristicClassifier().classify(
            samples: processed.samples, sampleRate: rate
        )
        // Song-derived carriers (chord grid / stem) note their song;
        // the take is mic audio either way → never uploaded.
        let songId: String?
        switch take.mode {
        case .song, .stem:
            songId = app.currentBundle?.analysisId
        case .classic, .harmony, .texture:
            songId = nil
        }
        let meta = try await app.padSampleStore.save(
            samples: processed.samples,
            sampleRate: rate,
            metadata: PadSampleMetadata(
                source: .vocoded,
                classification: cls,
                confidence: confidence,
                durationSec: 0,   // authoritative values filled by the
                sampleRate: 0,    // store from the payload itself
                channels: 1,
                colorHint: Self.localColor(.vocoded),
                vocoderMode: take.mode.rawValue,
                sourceSongId: songId
            )
        )
        assignLocalSample(id: meta.id, toGridPad: gridRaw)
        return meta
    }

    /// Build the carrier program for one capture. Deterministic given
    /// the app state at arm time (loaded song, transport position,
    /// active pack); every missing audio source degrades one step —
    /// stem → chord grid → drone — so a capture always sounds.
    public func vocoderProgram(for mode: VocoderMode) async -> VocoderProgram {
        let rate = AudioEngine.canonicalSampleRate
        let dur = VocoderCaptureSession.maxDurationSec
        switch mode {
        case .classic:
            // Current chord voiced low (C3 octave); no chord → the
            // builder's own drone fallback.
            let notes = currentChordPitchClasses().sorted().map { 48 + $0 }
            return VocoderProgram(
                mode: .classic,
                carrier: VocoderCarriers.sawStack(
                    notes: notes, durationSec: dur, sampleRate: rate
                )
            )

        case .song:
            return VocoderProgram(
                mode: .song,
                carrier: VocoderCarriers.chordGrid(
                    spans: chordSpans(midiBase: 48),
                    durationSec: dur, sampleRate: rate
                )
            )

        case .stem:
            if let url = app.vocoderStemURL {
                let now = app.audioEngine.clock.nowSongSeconds
                let carrier = await Task
                    .detached(priority: .userInitiated) {
                        let source = Self.monoSamples(
                            url: url, fromSec: max(0, now),
                            maxSec: 16, targetRate: rate
                        )
                        return VocoderCarriers.loopedStem(
                            source, sampleRate: rate, durationSec: dur
                        )
                    }.value
                if carrier.contains(where: { $0 != 0 }) {
                    return VocoderProgram(mode: .stem, carrier: carrier)
                }
            }
            // No stem on disk yet → the song's chord carrier.
            return VocoderProgram(
                mode: .stem,
                carrier: VocoderCarriers.chordGrid(
                    spans: chordSpans(midiBase: 48),
                    durationSec: dur, sampleRate: rate
                )
            )

        case .harmony:
            // No spectral carrier — PSOLA voice-leads against the
            // chord grid (middle-C octave, the transform convention).
            return VocoderProgram(
                mode: .harmony,
                carrier: [],
                chordSpans: chordSpans(midiBase: 60)
            )

        case .texture:
            let source = textureCarrierSource()
            guard !source.isEmpty else {
                return VocoderProgram(
                    mode: .texture,
                    carrier: VocoderCarriers.sawStack(
                        notes: [], durationSec: dur, sampleRate: rate
                    )
                )
            }
            return VocoderProgram(
                mode: .texture,
                carrier: VocoderCarriers.texture(
                    source, sampleRate: rate, durationSec: dur
                )
            )
        }
    }

    /// Chord grid for the capture window: the loaded song's chords
    /// from the CURRENT transport position (span times re-based to
    /// capture-relative seconds), else the single sounding chord,
    /// else empty (the carrier builders drone / PSOLA falls back to
    /// nearest-tone).
    private func chordSpans(midiBase: Int) -> [VocoderCarriers.ChordSpan] {
        if let chords = app.currentBundle?.timeline.chords, !chords.isEmpty {
            let now = app.audioEngine.clock.nowSongSeconds
            var spans: [VocoderCarriers.ChordSpan] = []
            for chord in chords where chord.end > now {
                let pcs = Self.pitchClasses(for: chord.symbol).sorted()
                guard !pcs.isEmpty else { continue }
                spans.append(VocoderCarriers.ChordSpan(
                    startSec: max(0, chord.start - now),
                    midiNotes: pcs.map { midiBase + $0 }
                ))
            }
            if !spans.isEmpty { return spans }
        }
        let current = currentChordPitchClasses().sorted().map { midiBase + $0 }
        return current.isEmpty
            ? []
            : [VocoderCarriers.ChordSpan(startSec: 0, midiNotes: current)]
    }

    /// M5's carrier audio: the active pack's most texture-like pad
    /// that has a resident base buffer (textures, then pads, then
    /// anything). Empty when no pack audio is loaded.
    private func textureCarrierSource() -> [Float] {
        guard let active = app.activeSamplePack else { return [] }
        let pads = active.pack.pads.sorted { $0.padIdx < $1.padIdx }
        let ordered = pads.filter { $0.family == .textures }
            + pads.filter { $0.family == .pads }
            + pads
        for pad in ordered {
            guard let buffer = app.sampleScheduler.baseBuffer(
                packId: active.pack.packId, padIdx: pad.padIdx
            ) else { continue }
            let mono = Self.monoSamples(of: buffer)
            if mono.contains(where: { $0 != 0 }) { return mono }
        }
        return []
    }

    /// Decode up to `maxSec` of an audio file starting at `fromSec`
    /// (clamped so a position past the end still yields audio) into
    /// 48 kHz mono. Empty on any read failure.
    private nonisolated static func monoSamples(
        url: URL, fromSec: Double, maxSec: Double, targetRate: Double
    ) -> [Float] {
        guard let file = try? AVAudioFile(forReading: url) else { return [] }
        let nativeRate = file.processingFormat.sampleRate
        let want = AVAudioFrameCount(maxSec * nativeRate)
        var start = AVAudioFramePosition(fromSec * nativeRate)
        if file.length - start < AVAudioFramePosition(want) {
            start = max(0, file.length - AVAudioFramePosition(want))
        }
        file.framePosition = start
        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: file.processingFormat, frameCapacity: want
        ), (try? file.read(into: buffer, frameCount: want)) != nil
        else { return [] }
        let mono = monoSamples(of: buffer)
        return nativeRate == targetRate
            ? mono
            : MicRecorder.resample(mono, from: nativeRate, to: targetRate)
    }

    /// Channel-average mono copy of a PCM buffer.
    private nonisolated static func monoSamples(
        of buffer: AVAudioPCMBuffer
    ) -> [Float] {
        guard let channels = buffer.floatChannelData else { return [] }
        let frames = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)
        guard frames > 0, channelCount > 0 else { return [] }
        if channelCount == 1 {
            return Array(UnsafeBufferPointer(start: channels[0], count: frames))
        }
        var mono = [Float](repeating: 0, count: frames)
        for i in 0..<frames {
            var sum: Float = 0
            for ch in 0..<channelCount { sum += channels[ch][i] }
            mono[i] = sum / Float(channelCount)
        }
        return mono
    }
}
