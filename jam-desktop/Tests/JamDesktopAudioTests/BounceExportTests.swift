// BounceExportTests.swift
//
// Offline bounce pipeline against a generated WAV: segment loading
// converts to Float32 at the render rate, the service renders a
// capture's pad events, and repeat renders are bit-identical
// (SessionBounceRenderer's determinism guarantee, exercised through
// the desktop service).

import XCTest
import AVFoundation
import ToneForgeEngine
import JamDesktopCore
@testable import JamDesktopAudio

@MainActor
final class BounceExportTests: XCTestCase {

    private var tempDir: URL!
    private var stemURL: URL!

    override func setUp() async throws {
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: tempDir, withIntermediateDirectories: true)
        stemURL = tempDir.appendingPathComponent("stem.wav")
        try Self.writeSineWAV(to: stemURL, seconds: 1.0, sampleRate: 44_100)
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: tempDir)
    }

    /// 440 Hz mono sine, Float32 WAV.
    private static func writeSineWAV(
        to url: URL, seconds: Double, sampleRate: Double
    ) throws {
        let format = AVAudioFormat(
            commonFormat: .pcmFormatFloat32, sampleRate: sampleRate,
            channels: 1, interleaved: false)!
        let file = try AVAudioFile(
            forWriting: url, settings: format.settings,
            commonFormat: .pcmFormatFloat32, interleaved: false)
        let frames = AVAudioFrameCount(seconds * sampleRate)
        let buffer = AVAudioPCMBuffer(
            pcmFormat: format, frameCapacity: frames)!
        buffer.frameLength = frames
        let data = buffer.floatChannelData![0]
        for i in 0..<Int(frames) {
            data[i] = sinf(2 * .pi * 440 * Float(i) / Float(sampleRate)) * 0.5
        }
        try file.write(from: buffer)
    }

    private func fixtures() -> (
        capture: SessionCapture,
        assignments: [LaunchpadPad: PadAssignment],
        stemURLs: [String: URL]
    ) {
        let chop = Chop(
            idx: 0, startSec: 0, endSec: 0.25, durationSec: 0.25,
            kind: "chord", chordSymbol: nil, colorHint: nil)
        let assignments: [LaunchpadPad: PadAssignment] = [
            LaunchpadPad(row: 0, col: 0):
                PadAssignment(chop: chop, stem: "other")
        ]
        // Top-left pad = event (row 8, col 1).
        let capture = SessionCapture(
            sessionId: UUID(),
            songBackendId: "song-1",
            appMode: .sample,
            capturedAt: Date(timeIntervalSince1970: 0),
            tempoBpm: 120,
            events: [
                ContributionEvent(
                    source: .launchpad, kind: .padDown(row: 8, col: 1),
                    timestamp: 0.1, hostTime: 0),
                ContributionEvent(
                    source: .launchpad, kind: .padUp(row: 8, col: 1),
                    timestamp: 0.4, hostTime: 0),
            ],
            padMapping: [:]
        )
        return (capture, assignments, ["other": stemURL])
    }

    // MARK: - Segment loading

    func testLoadSegmentConvertsToRenderFormat() throws {
        let buffer = try ChopBufferLoader.loadSegment(
            url: stemURL, startSec: 0, endSec: 0.25, sampleRate: 48_000)
        XCTAssertEqual(buffer.format.sampleRate, 48_000)
        XCTAssertEqual(buffer.format.commonFormat, .pcmFormatFloat32)
        // 0.25 s at 48 kHz ≈ 12,000 frames (converter may differ by
        // a few frames at the edges).
        XCTAssertEqual(
            Double(buffer.frameLength), 12_000, accuracy: 128)
    }

    func testLoadSegmentRejectsEmptyRange() {
        XCTAssertThrowsError(try ChopBufferLoader.loadSegment(
            url: stemURL, startSec: 0.5, endSec: 0.5))
        XCTAssertThrowsError(try ChopBufferLoader.loadSegment(
            url: stemURL, startSec: 5, endSec: 6))
    }

    // MARK: - Bounce

    func testBounceRendersWAV() async throws {
        let (capture, assignments, stemURLs) = fixtures()
        let outDir = tempDir.appendingPathComponent("out1")
        try FileManager.default.createDirectory(
            at: outDir, withIntermediateDirectories: true)
        let result = try await SessionBounceService.bounce(
            session: capture, assignments: assignments,
            stemURLs: stemURLs, outputDirectory: outDir)
        XCTAssertEqual(result.renderedSampleEvents, 1)
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: result.url.path))
        // durationSec (0.4) + reverb tail.
        XCTAssertGreaterThan(result.durationSec, 0.4)
    }

    func testBounceIsDeterministic() async throws {
        let (capture, assignments, stemURLs) = fixtures()
        var datas: [Data] = []
        for name in ["a", "b"] {
            let outDir = tempDir.appendingPathComponent(name)
            try FileManager.default.createDirectory(
                at: outDir, withIntermediateDirectories: true)
            let result = try await SessionBounceService.bounce(
                session: capture, assignments: assignments,
                stemURLs: stemURLs, outputDirectory: outDir)
            datas.append(try Data(contentsOf: result.url))
        }
        XCTAssertEqual(datas[0], datas[1])
        XCTAssertFalse(datas[0].isEmpty)
    }

    func testBounceWithNoResolvablePadsThrows() async {
        let (capture, _, stemURLs) = fixtures()
        do {
            _ = try await SessionBounceService.bounce(
                session: capture, assignments: [:],
                stemURLs: stemURLs, outputDirectory: tempDir)
            XCTFail("expected noAssignedPads")
        } catch {
            // Expected.
        }
    }
}
