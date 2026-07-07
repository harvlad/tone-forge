// ModeCoordinatorRingingTests.swift
//
// `ModeCoordinator.ringingGridPads(from:)` — the UI-side mapping from
// the voice pool's ringing `SamplePadKey`s to on-screen grid pads
// (PadIndex rawValues). Contract:
//
//   - Only the ACTIVE pack's bound quadrant lights up; ringing keys
//     from other packs are excluded (the stop-all button covers
//     those).
//   - Pack padIdx p maps to grid row 8 - p/4, col p%4 + 1
//     (top-left 4×4 quadrant, rawValue = row*10 + col).
//   - Keys for padIdxs the pack doesn't bind are ignored.
//
// Fixture strategy: same hermetic runtime-tone packs as
// SampleSchedulerMultiPackTests. `activateSamplePack` only reads the
// pad files into buffers — no engine start needed, so these run on
// audio-less hosts too.

import XCTest
import AVFoundation
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class ModeCoordinatorRingingTests: XCTestCase {

    private var app: AppState!
    private var tmpDir: URL!

    override func setUp() async throws {
        try await super.setUp()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("ringing-grid-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: tmpDir, withIntermediateDirectories: true
        )
        app = AppState()
        // appMode restores from persisted settings — force the sample
        // layout so padBindings are the pack quadrant.
        app.modeCoordinator.setMode(.sample)
    }

    override func tearDown() async throws {
        if let dir = tmpDir { try? FileManager.default.removeItem(at: dir) }
        app = nil
        try await super.tearDown()
    }

    // MARK: - Fixtures

    private func writeTone(to url: URL, durationSec: Double = 0.1) throws {
        let sr = 44_100.0
        let format = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 2)!
        let frames = AVAudioFrameCount(durationSec * sr)
        let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
        buf.frameLength = frames
        if let ch = buf.floatChannelData {
            for c in 0..<2 {
                for i in 0..<Int(frames) {
                    ch[c][i] = Float(sin(2 * .pi * 440 * Double(i) / sr) * 0.3)
                }
            }
        }
        let file = try AVAudioFile(forWriting: url, settings: format.settings)
        try file.write(from: buf)
    }

    private func makePack(
        packId: String, padIdxs: [Int]
    ) throws -> ResolvedSamplePack {
        var padObjs: [SamplePad] = []
        var urls: [Int: URL] = [:]
        for idx in padIdxs {
            let url = tmpDir.appendingPathComponent("\(packId)-\(idx).caf")
            try writeTone(to: url)
            padObjs.append(SamplePad(
                padIdx: idx,
                name: "P\(idx)",
                family: .pads,
                filename: "\(packId)-\(idx).caf",
                chokeGroup: nil,
                loopPointSec: 0
            ))
            urls[idx] = url
        }
        let pack = SamplePack(
            packId: packId, name: packId, family: .pads, pads: padObjs
        )
        return ResolvedSamplePack(pack: pack, padFileURLs: urls)
    }

    private func key(_ packId: String, _ padIdx: Int) -> SamplePadKey {
        SamplePadKey(packId: packId, padIdx: padIdx)
    }

    // MARK: - Tests

    func testEmptyKeysReturnsEmpty() throws {
        let pack = try makePack(packId: "packA", padIdxs: [0])
        app.activateSamplePack(pack, stemFiles: [:])
        XCTAssertTrue(app.modeCoordinator.ringingGridPads(from: []).isEmpty)
    }

    func testNoActivePackReturnsEmpty() {
        // Never-activated AppState → no bindings → nothing lights.
        XCTAssertTrue(
            app.modeCoordinator
                .ringingGridPads(from: [key("packA", 0)])
                .isEmpty
        )
    }

    func testActivePackKeysMapToQuadrant() throws {
        // padIdx 0 → row 8, col 1 (raw 81); 5 → row 7, col 2 (72);
        // 15 → row 5, col 4 (54).
        let pack = try makePack(packId: "packA", padIdxs: [0, 5, 15])
        app.activateSamplePack(pack, stemFiles: [:])

        XCTAssertEqual(
            app.modeCoordinator.ringingGridPads(
                from: [key("packA", 0), key("packA", 15)]
            ),
            [81, 54]
        )
        XCTAssertEqual(
            app.modeCoordinator.ringingGridPads(from: [key("packA", 5)]),
            [72]
        )
    }

    func testOtherPackKeysExcluded() throws {
        // packA is active; a ringing voice left over in packB must
        // not light the grid (stop-all covers it instead).
        let a = try makePack(packId: "packA", padIdxs: [0])
        app.activateSamplePack(a, stemFiles: [:])

        XCTAssertTrue(
            app.modeCoordinator
                .ringingGridPads(from: [key("packB", 0)])
                .isEmpty
        )
        XCTAssertEqual(
            app.modeCoordinator.ringingGridPads(
                from: [key("packA", 0), key("packB", 0)]
            ),
            [81]
        )
    }

    func testUnboundPadIdxExcluded() throws {
        // The pack binds only padIdx 0 — a key for an idx it doesn't
        // carry maps to nothing.
        let pack = try makePack(packId: "packA", padIdxs: [0])
        app.activateSamplePack(pack, stemFiles: [:])

        XCTAssertTrue(
            app.modeCoordinator
                .ringingGridPads(from: [key("packA", 7)])
                .isEmpty
        )
    }

    func testBindingsFollowPackSwap() throws {
        // After activating packB, packA keys stop lighting the grid
        // and packB keys start — the quadrant follows the active pack.
        let a = try makePack(packId: "packA", padIdxs: [0])
        let b = try makePack(packId: "packB", padIdxs: [0])
        app.activateSamplePack(a, stemFiles: [:])
        app.activateSamplePack(b, stemFiles: [:])

        XCTAssertTrue(
            app.modeCoordinator
                .ringingGridPads(from: [key("packA", 0)])
                .isEmpty
        )
        XCTAssertEqual(
            app.modeCoordinator.ringingGridPads(from: [key("packB", 0)]),
            [81]
        )
    }
}
