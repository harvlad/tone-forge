// WaveformPeakExtractor.swift
//
// Client-side waveform peaks for the Play-tab scrubber. The backend
// bundle carries no peaks field, but every loaded song's stems are
// guaranteed local (AppState.downloadAndLoad), so the peak strip is
// computed on-device:
//
//   - each stem is read via AVAudioFile in chunks and reduced to
//     `binCount` max-abs bins (max across channels within a stem);
//   - stems are summed bin-wise (a bar's height reflects the mix,
//     not just the loudest stem);
//   - the result is normalized to peak 1.0. All-silent input stays
//     all-zero (no divide-by-silence blowup).
//
// Extraction is pure + synchronous; AppState runs it off-main and
// caches the result as waveform.json next to the stems
// (Caches/toneforge/stems/{analysisId}/), so it costs one pass per
// song per download — evicting the stems evicts the peaks with them.

import Foundation
import AVFoundation

enum WaveformPeakExtractor {

    /// ~2 px per bin on a full-width phone scrubber; the view
    /// downsamples further to fit whatever width it actually gets.
    static let defaultBinCount = 600

    /// Reduce a set of stem files to one normalized peak strip.
    /// Unreadable files are skipped (a bad stem shouldn't blank the
    /// whole scrubber — same policy as StemPlayer.load).
    static func extractPeaks(
        stemURLs: [URL],
        binCount: Int = defaultBinCount
    ) -> [Float] {
        guard binCount > 0 else { return [] }
        var sum = [Float](repeating: 0, count: binCount)
        for url in stemURLs {
            guard let bins = try? peaks(file: url, binCount: binCount) else {
                continue
            }
            for i in 0..<binCount {
                sum[i] += bins[i]
            }
        }
        guard let peak = sum.max(), peak > 0 else { return sum }
        return sum.map { $0 / peak }
    }

    /// Max-abs bins for a single audio file (max across channels).
    /// Bins partition the file's frames evenly, so bin i always maps
    /// to the same song-time window regardless of sample rate.
    static func peaks(file url: URL, binCount: Int) throws -> [Float] {
        var bins = [Float](repeating: 0, count: binCount)
        let file = try AVAudioFile(forReading: url)
        let totalFrames = Int(file.length)
        guard totalFrames > 0 else { return bins }

        let format = file.processingFormat
        let channelCount = Int(format.channelCount)
        let chunkFrames: AVAudioFrameCount = 65_536
        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: format, frameCapacity: chunkFrames
        ) else {
            return bins
        }

        var frameIndex = 0
        while frameIndex < totalFrames {
            try file.read(into: buffer)
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
                    binCount - 1, (frameIndex + i) * binCount / totalFrames
                )
                if m > bins[bin] { bins[bin] = m }
            }
            frameIndex += n
        }
        return bins
    }
}

// MARK: - Cache

/// waveform.json persistence beside the cached stems. Best-effort by
/// design: a missing/corrupt cache just means one re-extraction.
struct WaveformCache: Sendable {

    private let root: URL

    /// Injectable root for tests; production resolves to the same
    /// Caches/toneforge/stems dir BundleStore downloads into.
    init(root: URL? = nil) {
        if let root {
            self.root = root
        } else {
            let caches = FileManager.default.urls(
                for: .cachesDirectory, in: .userDomainMask
            )[0]
            self.root = caches
                .appendingPathComponent("toneforge", isDirectory: true)
                .appendingPathComponent("stems", isDirectory: true)
        }
    }

    func fileURL(for analysisId: String) -> URL {
        root.appendingPathComponent(analysisId, isDirectory: true)
            .appendingPathComponent("waveform.json", isDirectory: false)
    }

    func load(analysisId: String) -> [Float]? {
        guard let data = try? Data(contentsOf: fileURL(for: analysisId)) else {
            return nil
        }
        return try? JSONDecoder().decode([Float].self, from: data)
    }

    func save(_ peaks: [Float], analysisId: String) {
        let url = fileURL(for: analysisId)
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        guard let data = try? JSONEncoder().encode(peaks) else { return }
        try? data.write(to: url, options: .atomic)
    }
}
