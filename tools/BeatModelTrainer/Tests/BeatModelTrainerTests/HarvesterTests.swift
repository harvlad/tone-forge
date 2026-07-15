// HarvesterTests.swift
//
// Stage B (Swift) coverage for the E-GMD harvester. No dataset
// download: writes tiny synthetic wavs, runs `Harvester.run`, and
// asserts the emitted corrections CSV — including the parity guardrail
// that harvest features equal a direct `OnsetFeatures.extract` on the
// same slice bounds (the whole reason feature extraction is in Swift).

import XCTest
import AVFoundation
@testable import BeatModelTrainer
import ToneForgeEngine

final class HarvesterTests: XCTestCase {

    // MARK: - Helpers

    /// Write mono `samples` at `sampleRate` to a temp .wav, return URL.
    private func writeWAV(_ samples: [Float], sampleRate: Double) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("harv-\(UUID().uuidString).wav")
        let format = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: sampleRate, channels: 1, interleaved: false
        )!
        let file = try AVAudioFile(forWriting: url, settings: format.settings)
        let buf = AVAudioPCMBuffer(
            pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count))!
        buf.frameLength = AVAudioFrameCount(samples.count)
        for i in samples.indices { buf.floatChannelData![0][i] = samples[i] }
        try file.write(from: buf)
        return url
    }

    /// A short decaying noise+tone burst — realistic percussive slice.
    private func burst(count: Int, seed: UInt64 = 7) -> [Float] {
        var state = seed
        func rnd() -> Float {
            state = state &* 6364136223846793005 &+ 1442695040888963407
            return Float(Int32(truncatingIfNeeded: state >> 32)) / Float(Int32.max)
        }
        var x = [Float](repeating: 0, count: count)
        for i in 0..<count {
            let t = Float(i) / 48_000.0
            let env = expf(-40 * t)
            x[i] = (0.6 * rnd() + 0.4 * sinf(2 * .pi * 180 * t)) * env
        }
        return x
    }

    private func writeManifest(_ rows: [(String, Double, String)]) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("manifest-\(UUID().uuidString).csv")
        var text = "wav_path,onset_sec,role\n"
        for (wav, onset, role) in rows { text += "\(wav),\(onset),\(role)\n" }
        try text.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    /// Parse a harvest CSV into (header, rows-of-cells).
    private func parseCSV(_ url: URL) throws -> (header: [String], rows: [[String]]) {
        let text = try String(contentsOf: url, encoding: .utf8)
        let lines = text.split(whereSeparator: \.isNewline).map(String.init)
        let header = lines[0].split(separator: ",").map(String.init)
        let rows = lines.dropFirst().map { $0.split(separator: ",",
            omittingEmptySubsequences: false).map(String.init) }
        return (header, Array(rows))
    }

    // MARK: - Tests

    func testHarvestRoundTripSingleOnset() throws {
        // 0.10s @ 48k = 4800 samples < 140ms cap, so slice = whole buffer.
        let samples = burst(count: 4800)
        let wav = try writeWAV(samples, sampleRate: 48_000)
        let manifest = try writeManifest([(wav.path, 0.0, "snare")])
        let out = FileManager.default.temporaryDirectory
            .appendingPathComponent("out-\(UUID().uuidString).csv")

        Harvester.run(manifestPath: manifest.path, audioRoot: nil, outPath: out.path)

        let (header, rows) = try parseCSV(out)
        XCTAssertEqual(header,
            OnsetFeatures.featureNames + ["original", "corrected", "timestamp"])
        XCTAssertEqual(rows.count, 1)
        let row = rows[0]
        XCTAssertEqual(row[row.count - 2], "snare")   // corrected
        XCTAssertEqual(row[row.count - 3], "snare")   // original
        // All 7 features finite.
        for i in 0..<OnsetFeatures.featureNames.count {
            let v = Double(row[i])
            XCTAssertNotNil(v)
            XCTAssertTrue(v!.isFinite)
        }
    }

    func testParityWithOnsetFeaturesExtract() throws {
        // Native 48k mono → no resample → harvest slice is bit-identical
        // to the buffer, so CSV feature cols must equal a direct extract.
        let samples = burst(count: 4800)
        let wav = try writeWAV(samples, sampleRate: 48_000)
        let manifest = try writeManifest([(wav.path, 0.0, "kick")])
        let out = FileManager.default.temporaryDirectory
            .appendingPathComponent("out-\(UUID().uuidString).csv")

        Harvester.run(manifestPath: manifest.path, audioRoot: nil, outPath: out.path)

        // Whole buffer is the slice (start 0, len < 140ms cap).
        let expected = OnsetFeatures.extract(samples, sampleRate: 48_000).featureVector

        let (_, rows) = try parseCSV(out)
        XCTAssertEqual(rows.count, 1)
        for (i, exp) in expected.enumerated() {
            let got = Double(rows[0][i])!
            XCTAssertEqual(got, exp, accuracy: 1e-4,
                           "feature \(OnsetFeatures.featureNames[i]) drift")
        }
    }

    func testResamplePathProducesRow() throws {
        // 44.1k in → converter to 48k → still yields one usable row.
        let samples = burst(count: 4410)  // 0.10s @ 44.1k
        let wav = try writeWAV(samples, sampleRate: 44_100)
        let manifest = try writeManifest([(wav.path, 0.0, "closed_hat")])
        let out = FileManager.default.temporaryDirectory
            .appendingPathComponent("out-\(UUID().uuidString).csv")

        Harvester.run(manifestPath: manifest.path, audioRoot: nil, outPath: out.path)

        let (_, rows) = try parseCSV(out)
        XCTAssertEqual(rows.count, 1)
        XCTAssertEqual(rows[0][rows[0].count - 2], "closed_hat")
        // Duration ≈ resampled slice length (0.10s), capped at 140ms.
        let durIdx = OnsetFeatures.featureNames.firstIndex(of: "durationSec")!
        let dur = Double(rows[0][durIdx])!
        XCTAssertEqual(dur, 0.10, accuracy: 0.01)
    }

    func testAudioRootResolvesRelativePaths() throws {
        let samples = burst(count: 4800)
        let wav = try writeWAV(samples, sampleRate: 48_000)
        let root = wav.deletingLastPathComponent().path
        let name = wav.lastPathComponent
        let manifest = try writeManifest([(name, 0.0, "perc")])
        let out = FileManager.default.temporaryDirectory
            .appendingPathComponent("out-\(UUID().uuidString).csv")

        Harvester.run(manifestPath: manifest.path, audioRoot: root, outPath: out.path)

        let (_, rows) = try parseCSV(out)
        XCTAssertEqual(rows.count, 1)
        XCTAssertEqual(rows[0][rows[0].count - 2], "perc")
    }

    func testUnreadableFileSkippedNotFatal() throws {
        let manifest = try writeManifest([("/nope/missing.wav", 0.0, "kick")])
        let out = FileManager.default.temporaryDirectory
            .appendingPathComponent("out-\(UUID().uuidString).csv")

        Harvester.run(manifestPath: manifest.path, audioRoot: nil, outPath: out.path)

        let (header, rows) = try parseCSV(out)
        XCTAssertEqual(header.first, "centroidHz")   // header still written
        XCTAssertEqual(rows.count, 0)                // no rows, no crash
    }
}
