// VocoderProgramBuilder.swift
//
// Builds the deterministic carrier program for each vocoder mode.
// Degrades one step per missing audio source (stem → chord grid →
// drone) so a capture always sounds.
//
// Desktop port of iOS ModeCoordinator+Vocoder's vocoderProgram().

import Foundation
import AVFoundation
import ToneForgeEngine

public struct VocoderProgramBuilder {

    private let bundle: SongBundle?
    private let stemURLs: [String: URL]
    private let currentPosition: Double
    private let currentChordPitchClasses: () -> [Int]

    public init(
        bundle: SongBundle?,
        stemURLs: [String: URL],
        currentPosition: Double,
        currentChordPitchClasses: @escaping () -> [Int]
    ) {
        self.bundle = bundle
        self.stemURLs = stemURLs
        self.currentPosition = currentPosition
        self.currentChordPitchClasses = currentChordPitchClasses
    }

    /// Build the carrier program for one capture.
    public func buildProgram(for mode: VocoderMode) async -> VocoderProgram {
        let rate: Double = 48_000
        let dur = StemSlice.maxChopDurationSec

        switch mode {
        case .classic:
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
            // Try to use vocals stem, fallback to others
            if let url = preferredStemURL() {
                let now = currentPosition
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
            // Fallback to chord grid
            return VocoderProgram(
                mode: .stem,
                carrier: VocoderCarriers.chordGrid(
                    spans: chordSpans(midiBase: 48),
                    durationSec: dur, sampleRate: rate
                )
            )

        case .harmony:
            // No spectral carrier — PSOLA voice-leads against chords
            return VocoderProgram(
                mode: .harmony,
                carrier: [],
                chordSpans: chordSpans(midiBase: 60)
            )

        case .texture:
            // Fallback to saw stack (no pack audio on desktop yet)
            return VocoderProgram(
                mode: .texture,
                carrier: VocoderCarriers.sawStack(
                    notes: [], durationSec: dur, sampleRate: rate
                )
            )
        }
    }

    /// Chord grid for the capture window: the loaded song's chords
    /// from the CURRENT transport position.
    private func chordSpans(midiBase: Int) -> [VocoderCarriers.ChordSpan] {
        guard let bundle else {
            // No song loaded — use current sounding chord
            let pitches = currentChordPitchClasses()
            if pitches.isEmpty { return [] }
            return [VocoderCarriers.ChordSpan(
                startSec: 0,
                midiNotes: pitches.sorted().map { midiBase + $0 }
            )]
        }

        let now = currentPosition
        let capDur = StemSlice.maxChopDurationSec
        var spans: [VocoderCarriers.ChordSpan] = []

        for chord in bundle.timeline.chords {
            let relStart = chord.start - now
            if relStart >= capDur { break }
            if relStart < 0 && chord.end <= now { continue }

            let pitches = Array(ChordVoicing.pitchClassSet(symbol: chord.symbol))
            spans.append(VocoderCarriers.ChordSpan(
                startSec: max(0, relStart),
                midiNotes: pitches.sorted().map { midiBase + $0 }
            ))
        }

        return spans
    }

    /// Prefer vocals stem for carrier, fallback to inst or drums.
    private func preferredStemURL() -> URL? {
        stemURLs["vocals"]
            ?? stemURLs["inst"]
            ?? stemURLs["drums"]
            ?? stemURLs.values.first
    }

    /// Load mono samples from a stem file.
    private static func monoSamples(
        url: URL, fromSec: Double, maxSec: Double, targetRate: Double
    ) -> [Float] {
        guard let file = try? AVAudioFile(forReading: url) else { return [] }
        let srcRate = file.fileFormat.sampleRate
        let startFrame = AVAudioFramePosition(fromSec * srcRate)
        let maxFrames = AVAudioFrameCount(maxSec * srcRate)
        let availableFrames = AVAudioFrameCount(max(0, file.length - startFrame))
        let framesToRead = min(maxFrames, availableFrames)
        guard framesToRead > 0 else { return [] }

        let format = file.processingFormat
        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: format, frameCapacity: framesToRead
        ) else { return [] }

        file.framePosition = startFrame
        guard (try? file.read(into: buffer, frameCount: framesToRead)) != nil,
              let data = buffer.floatChannelData
        else { return [] }

        let frames = Int(buffer.frameLength)
        let channels = Int(format.channelCount)

        // Mono-ize
        var mono = [Float](repeating: 0, count: frames)
        if channels == 1 {
            mono.withUnsafeMutableBufferPointer {
                $0.baseAddress!.update(from: data[0], count: frames)
            }
        } else {
            for i in 0..<frames {
                var sum: Float = 0
                for ch in 0..<channels { sum += data[ch][i] }
                mono[i] = sum / Float(channels)
            }
        }

        // Resample if needed
        if srcRate != targetRate {
            mono = resample(mono, from: srcRate, to: targetRate)
        }

        return mono
    }

    private static func resample(
        _ samples: [Float], from srcRate: Double, to dstRate: Double
    ) -> [Float] {
        guard srcRate > 0, dstRate > 0, !samples.isEmpty else { return [] }
        let ratio = srcRate / dstRate
        let outLen = Int(Double(samples.count) / ratio)
        var out = [Float](repeating: 0, count: outLen)
        for i in 0..<outLen {
            let srcPos = Double(i) * ratio
            let idx = Int(srcPos)
            let frac = Float(srcPos - Double(idx))
            let s0 = samples[min(idx, samples.count - 1)]
            let s1 = samples[min(idx + 1, samples.count - 1)]
            out[i] = s0 + frac * (s1 - s0)
        }
        return out
    }
}
