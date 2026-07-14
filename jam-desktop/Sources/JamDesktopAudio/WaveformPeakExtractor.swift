// WaveformPeakExtractor.swift
//
// Max-abs peak bins for waveform display, adapted from the iOS
// extractor with one addition: a [startSec, endSec] window so the
// chop editor gets full-resolution peaks for just the chop's
// neighborhood instead of slicing a coarse whole-stem strip.
//
// Extraction is pure + synchronous; callers run it off the main
// actor (Task.detached) — a 3s window reads a few hundred KB.

import Foundation
import AVFoundation

public enum WaveformPeakExtractor {

    /// Max-abs bins (max across channels) for `url`, restricted to
    /// the [startSec, endSec] window (nil bounds = whole file),
    /// normalized to peak 1.0. All-silent input stays all-zero.
    public static func peaks(
        file url: URL,
        binCount: Int,
        startSec: Double? = nil,
        endSec: Double? = nil
    ) throws -> [Float] {
        var bins = [Float](repeating: 0, count: max(0, binCount))
        guard binCount > 0 else { return bins }

        let file = try AVAudioFile(forReading: url)
        let sampleRate = file.fileFormat.sampleRate
        let startFrame = AVAudioFramePosition(max(0, startSec ?? 0) * sampleRate)
        let endFrame = min(
            endSec.map { AVAudioFramePosition($0 * sampleRate) } ?? file.length,
            file.length
        )
        let windowFrames = Int(endFrame - startFrame)
        guard windowFrames > 0, startFrame < file.length else { return bins }

        let format = file.processingFormat
        let channelCount = Int(format.channelCount)
        let chunkFrames: AVAudioFrameCount = 65_536
        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: format, frameCapacity: chunkFrames
        ) else {
            return bins
        }

        file.framePosition = startFrame
        var frameIndex = 0
        while frameIndex < windowFrames {
            let remaining = AVAudioFrameCount(windowFrames - frameIndex)
            try file.read(into: buffer, frameCount: min(chunkFrames, remaining))
            let n = Int(buffer.frameLength)
            if n == 0 { break }
            guard let channels = buffer.floatChannelData else { break }
            for i in 0..<n {
                var m: Float = 0
                for c in 0..<channelCount {
                    let v = abs(channels[c][i])
                    if v > m { m = v }
                }
                let bin = min(
                    binCount - 1, (frameIndex + i) * binCount / windowFrames
                )
                if m > bins[bin] { bins[bin] = m }
            }
            frameIndex += n
        }

        guard let peak = bins.max(), peak > 0 else { return bins }
        return bins.map { $0 / peak }
    }
}
