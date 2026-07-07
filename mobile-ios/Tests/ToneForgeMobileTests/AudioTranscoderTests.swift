// AudioTranscoderTests.swift
//
// Golden-file coverage for AudioTranscoder.
//
// Fixture policy (see DECISIONS.md):
//   - Inputs are tiny committed sine files (0.25 s) in Fixtures/.
//   - Lossless WAV inputs at 44.1 kHz compare BYTE-EXACT against the
//     committed golden output — PCM decode + channel downmix is
//     deterministic.
//   - The 48 kHz WAV (sample-rate conversion) and the m4a (lossy AAC
//     decode) are NOT bit-stable across OS releases, so they compare
//     with tolerance: length within ±64 frames, samples within
//     ±4 LSB after trimming to the common length.
//
// Record mode (regenerates all fixtures + goldens into the repo):
//   TONEFORGE_RECORD_FIXTURES=1 \
//   TONEFORGE_FIXTURE_DIR=$PWD/Tests/ToneForgeMobileTests/Fixtures \
//   swift test --filter AudioTranscoderTests/testRecordFixtures

import XCTest
@testable import ToneForgeMobile
#if canImport(AVFoundation)
import AVFoundation
#endif

final class AudioTranscoderTests: XCTestCase {

    // MARK: - Header (pure, no AVFoundation needed)

    func testWavHeaderIsCanonical44Bytes() {
        let header = AudioTranscoder.wavHeader(dataByteCount: 0x0000_1000)

        XCTAssertEqual(header.count, 44)
        XCTAssertEqual(Array(header[0..<4]), Array("RIFF".utf8))
        XCTAssertEqual(Array(header[8..<12]), Array("WAVE".utf8))
        XCTAssertEqual(Array(header[12..<16]), Array("fmt ".utf8))
        XCTAssertEqual(Array(header[36..<40]), Array("data".utf8))
        // chunkSize = 36 + dataSize, LE.
        XCTAssertEqual(header.leUInt32(at: 4), 36 + 0x1000)
        // fmt: PCM(1), mono, 44100 Hz, byteRate 88200, blockAlign 2, 16 bit.
        XCTAssertEqual(header.leUInt16(at: 20), 1)
        XCTAssertEqual(header.leUInt16(at: 22), 1)
        XCTAssertEqual(header.leUInt32(at: 24), 44_100)
        XCTAssertEqual(header.leUInt32(at: 28), 88_200)
        XCTAssertEqual(header.leUInt16(at: 32), 2)
        XCTAssertEqual(header.leUInt16(at: 34), 16)
        XCTAssertEqual(header.leUInt32(at: 40), 0x1000)
    }

    #if canImport(AVFoundation)

    // MARK: - Byte-exact goldens (lossless, no resample)

    func test44kMonoWavMatchesGoldenByteExact() throws {
        try assertByteExact(fixture: "sine-44k-mono", inputExt: "wav")
    }

    func test44kStereoWavMatchesGoldenByteExact() throws {
        try assertByteExact(fixture: "sine-44k-stereo", inputExt: "wav")
    }

    // MARK: - Tolerance goldens (SRC / lossy decode)

    func test48kStereoWavMatchesGoldenWithinTolerance() throws {
        try assertWithinTolerance(fixture: "sine-48k-stereo", inputExt: "wav")
    }

    func test44kStereoM4aMatchesGoldenWithinTolerance() throws {
        try assertWithinTolerance(fixture: "sine-44k-stereo-m4a", inputExt: "m4a")
    }

    // MARK: - Output invariants

    func testOutputIsCanonicalFormat() throws {
        let input = try fixtureURL("sine-44k-stereo", ext: "wav")
        let output = tempOutputURL()
        defer { try? FileManager.default.removeItem(at: output) }

        let result = try AudioTranscoder.transcodeToAnalysisWAV(input: input, output: output)

        // Independent parse via AVAudioFile: mono, 44.1k, and the
        // frame count the transcoder reported.
        let parsed = try AVAudioFile(forReading: output)
        XCTAssertEqual(parsed.processingFormat.sampleRate, 44_100)
        XCTAssertEqual(parsed.processingFormat.channelCount, 1)
        XCTAssertEqual(Int(parsed.length), result.frameCount)
        // 0.25 s fixture → duration within a millisecond.
        XCTAssertEqual(result.durationSec, 0.25, accuracy: 0.001)
        // File size = 44-byte header + 2 bytes per frame.
        let size = try XCTUnwrap(
            FileManager.default.attributesOfItem(atPath: output.path)[.size] as? Int
        )
        XCTAssertEqual(size, 44 + result.frameCount * 2)
        XCTAssertEqual(size, result.byteCount)
    }

    func testUnreadableInputThrows() {
        let junk = tempOutputURL()
        try? Data([0xde, 0xad, 0xbe, 0xef]).write(to: junk)
        defer { try? FileManager.default.removeItem(at: junk) }

        XCTAssertThrowsError(
            try AudioTranscoder.transcodeToAnalysisWAV(input: junk, output: tempOutputURL())
        )
    }

    // MARK: - Record mode

    /// Regenerates every input fixture + golden into
    /// `TONEFORGE_FIXTURE_DIR`, then fails loudly so a record run can
    /// never masquerade as a green CI run.
    func testRecordFixtures() throws {
        let env = ProcessInfo.processInfo.environment
        guard env["TONEFORGE_RECORD_FIXTURES"] == "1" else {
            throw XCTSkip("record mode disabled (set TONEFORGE_RECORD_FIXTURES=1)")
        }
        let dirPath = try XCTUnwrap(
            env["TONEFORGE_FIXTURE_DIR"], "TONEFORGE_FIXTURE_DIR must point at the Fixtures dir"
        )
        let dir = URL(fileURLWithPath: dirPath, isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

        let specs: [(name: String, ext: String, sampleRate: Double, channels: Int, aac: Bool)] = [
            ("sine-44k-mono", "wav", 44_100, 1, false),
            ("sine-44k-stereo", "wav", 44_100, 2, false),
            ("sine-48k-stereo", "wav", 48_000, 2, false),
            ("sine-44k-stereo-m4a", "m4a", 44_100, 2, true),
        ]
        for spec in specs {
            let input = dir.appendingPathComponent("input-\(spec.name).\(spec.ext)")
            try? FileManager.default.removeItem(at: input)
            try Self.writeSineFixture(
                to: input, sampleRate: spec.sampleRate, channels: spec.channels, aac: spec.aac
            )
            let golden = dir.appendingPathComponent("golden-\(spec.name).wav")
            try? FileManager.default.removeItem(at: golden)
            try AudioTranscoder.transcodeToAnalysisWAV(input: input, output: golden)
        }
        XCTFail("Recorded fixtures into \(dir.path) — commit them and re-run without record env.")
    }

    // MARK: - Helpers

    private func fixtureURL(_ name: String, ext: String, prefix: String = "input") throws -> URL {
        try XCTUnwrap(
            Bundle.module.url(
                forResource: "\(prefix)-\(name)", withExtension: ext, subdirectory: "Fixtures"
            ),
            "missing fixture \(prefix)-\(name).\(ext) — run record mode (see file header)"
        )
    }

    private func tempOutputURL() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("transcode-test-\(UUID().uuidString).wav")
    }

    private func assertByteExact(
        fixture: String, inputExt: String, file: StaticString = #filePath, line: UInt = #line
    ) throws {
        let input = try fixtureURL(fixture, ext: inputExt)
        let golden = try fixtureURL(fixture, ext: "wav", prefix: "golden")
        let output = tempOutputURL()
        defer { try? FileManager.default.removeItem(at: output) }

        try AudioTranscoder.transcodeToAnalysisWAV(input: input, output: output)

        let got = try Data(contentsOf: output)
        let want = try Data(contentsOf: golden)
        XCTAssertEqual(got, want, "output diverged from golden", file: file, line: line)
    }

    private func assertWithinTolerance(
        fixture: String,
        inputExt: String,
        maxFrameDelta: Int = 64,
        maxSampleDelta: Int = 4,
        file: StaticString = #filePath,
        line: UInt = #line
    ) throws {
        let input = try fixtureURL(fixture, ext: inputExt)
        let golden = try fixtureURL(fixture, ext: "wav", prefix: "golden")
        let output = tempOutputURL()
        defer { try? FileManager.default.removeItem(at: output) }

        try AudioTranscoder.transcodeToAnalysisWAV(input: input, output: output)

        let got = try Data(contentsOf: output).pcm16Samples()
        let want = try Data(contentsOf: golden).pcm16Samples()
        XCTAssertLessThanOrEqual(
            abs(got.count - want.count), maxFrameDelta,
            "length drifted more than \(maxFrameDelta) frames", file: file, line: line
        )
        let common = min(got.count, want.count)
        var worst = 0
        for i in 0..<common {
            worst = max(worst, abs(Int(got[i]) - Int(want[i])))
        }
        XCTAssertLessThanOrEqual(
            worst, maxSampleDelta,
            "sample delta exceeded ±\(maxSampleDelta) LSB", file: file, line: line
        )
    }

    /// Write a 0.25 s sine fixture. L = 440 Hz; R (if present) = 660 Hz.
    ///
    /// The whole write happens inside `autoreleasepool` — AVAudioFile
    /// only finalizes the container when the ObjC object is destroyed,
    /// and without the pool that happens too late for the immediate
    /// read-back in record mode.
    private static func writeSineFixture(
        to url: URL, sampleRate: Double, channels: Int, aac: Bool
    ) throws {
        try autoreleasepool {
            let settings: [String: Any]
            if aac {
                settings = [
                    AVFormatIDKey: kAudioFormatMPEG4AAC,
                    AVSampleRateKey: sampleRate,
                    AVNumberOfChannelsKey: channels,
                    AVEncoderBitRateKey: 96_000,
                ]
            } else {
                settings = [
                    AVFormatIDKey: kAudioFormatLinearPCM,
                    AVSampleRateKey: sampleRate,
                    AVNumberOfChannelsKey: channels,
                    AVLinearPCMBitDepthKey: 16,
                    AVLinearPCMIsFloatKey: false,
                    AVLinearPCMIsBigEndianKey: false,
                    AVLinearPCMIsNonInterleaved: false,
                ]
            }
            let audioFile = try AVAudioFile(forWriting: url, settings: settings)
            let format = audioFile.processingFormat
            let frames = AVAudioFrameCount(sampleRate * 0.25)
            guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames) else {
                throw AudioTranscodeError.converterInitFailed
            }
            buffer.frameLength = frames
            for ch in 0..<channels {
                let freq = ch == 0 ? 440.0 : 660.0
                guard let ptr = buffer.floatChannelData?[ch] else { continue }
                for i in 0..<Int(frames) {
                    ptr[i] = Float(sin(2 * Double.pi * freq * Double(i) / sampleRate) * 0.5)
                }
            }
            try audioFile.write(from: buffer)
        }
    }

    #endif
}

// MARK: - Byte helpers

private extension Data {
    func leUInt32(at offset: Int) -> UInt32 {
        self[offset..<offset + 4].withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) }.littleEndian
    }

    func leUInt16(at offset: Int) -> UInt16 {
        self[offset..<offset + 2].withUnsafeBytes { $0.loadUnaligned(as: UInt16.self) }.littleEndian
    }

    /// Int16 LE samples of a canonical transcoder WAV (data at byte 44).
    func pcm16Samples() -> [Int16] {
        let payload = dropFirst(44)
        var out = [Int16](repeating: 0, count: payload.count / 2)
        _ = out.withUnsafeMutableBytes { payload.copyBytes(to: $0) }
        return out.map(Int16.init(littleEndian:))
    }
}
