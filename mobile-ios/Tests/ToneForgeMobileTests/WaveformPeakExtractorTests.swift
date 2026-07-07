// WaveformPeakExtractorTests.swift
//
// Coverage for the on-device scrubber peaks (redesign Phase 3):
//   - requested bin count comes back exactly;
//   - non-silent input normalizes to peak 1.0;
//   - silence stays all-zero (no divide-by-silence);
//   - loud-then-quiet audio shapes the strip (first half tall,
//     second half short);
//   - summing across stems still normalizes to 1.0;
//   - unreadable files are skipped, not fatal;
//   - WaveformCache round-trips through waveform.json;
//   - scrubber downsampling preserves group maxima.

import XCTest
import AVFoundation
@testable import ToneForgeMobile

final class WaveformPeakExtractorTests: XCTestCase {

    private var dir: URL!

    override func setUpWithError() throws {
        dir = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "waveform-tests-\(UUID().uuidString)", isDirectory: true
            )
        try FileManager.default.createDirectory(
            at: dir, withIntermediateDirectories: true
        )
    }

    override func tearDownWithError() throws {
        if let dir { try? FileManager.default.removeItem(at: dir) }
        dir = nil
    }

    // MARK: - Fixtures

    /// Write a WAV whose first half is a sine at `loud` amplitude and
    /// whose second half is at `quiet` amplitude.
    private func writeWAV(
        name: String,
        loud: Float = 0.5,
        quiet: Float = 0.5,
        durationSec: Double = 0.5
    ) throws -> URL {
        let url = dir.appendingPathComponent("\(name).wav")
        try autoreleasepool {
            let sr = 44_100.0
            let format = AVAudioFormat(
                standardFormatWithSampleRate: sr, channels: 2
            )!
            let frames = AVAudioFrameCount(durationSec * sr)
            let buf = AVAudioPCMBuffer(
                pcmFormat: format, frameCapacity: frames
            )!
            buf.frameLength = frames
            let half = Int(frames) / 2
            if let ch = buf.floatChannelData {
                for c in 0..<2 {
                    for i in 0..<Int(frames) {
                        let amp = i < half ? loud : quiet
                        ch[c][i] = Float(
                            sin(2 * .pi * 440 * Double(i) / sr)
                        ) * amp
                    }
                }
            }
            let file = try AVAudioFile(
                forWriting: url, settings: format.settings
            )
            try file.write(from: buf)
        }
        return url
    }

    // MARK: - Extraction

    func testBinCountMatchesRequest() throws {
        let url = try writeWAV(name: "tone")
        let peaks = WaveformPeakExtractor.extractPeaks(
            stemURLs: [url], binCount: 64
        )
        XCTAssertEqual(peaks.count, 64)
    }

    func testNonSilentInputNormalizesToOne() throws {
        let url = try writeWAV(name: "tone", loud: 0.25, quiet: 0.25)
        let peaks = WaveformPeakExtractor.extractPeaks(
            stemURLs: [url], binCount: 64
        )
        XCTAssertEqual(peaks.max() ?? 0, 1.0, accuracy: 1e-6)
        XCTAssertGreaterThanOrEqual(peaks.min() ?? -1, 0)
    }

    func testSilenceStaysAllZero() throws {
        let url = try writeWAV(name: "silence", loud: 0, quiet: 0)
        let peaks = WaveformPeakExtractor.extractPeaks(
            stemURLs: [url], binCount: 32
        )
        XCTAssertEqual(peaks, [Float](repeating: 0, count: 32))
    }

    func testLoudThenQuietShapesTheStrip() throws {
        let url = try writeWAV(name: "shaped", loud: 0.8, quiet: 0.1)
        let peaks = WaveformPeakExtractor.extractPeaks(
            stemURLs: [url], binCount: 40
        )
        // First-half bins carry the loud sine (normalized to 1);
        // second-half bins sit around 0.1/0.8.
        let firstHalf = Array(peaks[0..<18])
        let secondHalf = Array(peaks[22..<40])
        XCTAssertGreaterThan(firstHalf.min() ?? 0, 0.5)
        XCTAssertLessThan(secondHalf.max() ?? 1, 0.3)
    }

    func testSummingAcrossStemsStillNormalizes() throws {
        let a = try writeWAV(name: "stemA", loud: 0.4, quiet: 0.4)
        let b = try writeWAV(name: "stemB", loud: 0.3, quiet: 0.3)
        let peaks = WaveformPeakExtractor.extractPeaks(
            stemURLs: [a, b], binCount: 32
        )
        XCTAssertEqual(peaks.count, 32)
        XCTAssertEqual(peaks.max() ?? 0, 1.0, accuracy: 1e-6)
    }

    func testUnreadableFileIsSkippedNotFatal() throws {
        let good = try writeWAV(name: "good")
        let bogus = dir.appendingPathComponent("not-audio.wav")
        try Data([0x00, 0x01, 0x02]).write(to: bogus)

        let peaks = WaveformPeakExtractor.extractPeaks(
            stemURLs: [bogus, good], binCount: 16
        )
        XCTAssertEqual(peaks.count, 16)
        XCTAssertEqual(peaks.max() ?? 0, 1.0, accuracy: 1e-6)
    }

    // MARK: - Cache

    func testCacheRoundTrips() {
        let cache = WaveformCache(root: dir)
        let peaks: [Float] = [0, 0.25, 0.5, 1.0]

        cache.save(peaks, analysisId: "abc-123")

        XCTAssertEqual(cache.load(analysisId: "abc-123"), peaks)
        XCTAssertEqual(
            cache.fileURL(for: "abc-123").lastPathComponent,
            "waveform.json"
        )
    }

    func testCacheMissReturnsNil() {
        let cache = WaveformCache(root: dir)
        XCTAssertNil(cache.load(analysisId: "never-saved"))
    }

    func testCorruptCacheReturnsNil() throws {
        let cache = WaveformCache(root: dir)
        let url = cache.fileURL(for: "corrupt")
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try Data("not json".utf8).write(to: url)

        XCTAssertNil(cache.load(analysisId: "corrupt"))
    }

    // MARK: - View downsampling

    func testDownsamplePreservesGroupMaxima() {
        let peaks: [Float] = [0.1, 0.9, 0.2, 0.3, 0.8, 0.1, 0.5, 0.6]
        XCTAssertEqual(
            WaveformScrubber.downsample(peaks, to: 4),
            [0.9, 0.3, 0.8, 0.6]
        )
    }

    func testDownsamplePassesThroughWhenAlreadySmall() {
        let peaks: [Float] = [0.1, 0.2]
        XCTAssertEqual(WaveformScrubber.downsample(peaks, to: 4), peaks)
        XCTAssertEqual(WaveformScrubber.downsample(peaks, to: 0), [])
    }
}
