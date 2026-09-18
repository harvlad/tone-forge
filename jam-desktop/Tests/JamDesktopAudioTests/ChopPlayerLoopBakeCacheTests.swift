// ChopPlayerLoopBakeCacheTests.swift
//
// Press-latency regression: every playback mode loops the voice now
// (the One-Shot/Follow gates included), so before the bake cache EVERY
// press paid the onset-scan file read + seam-crossfade bake in the
// touch path, and the FIRST press of each pad missed the prewarm cache
// entirely (the loop bake reads a shifted, extended region whose key
// prewarm never warmed) and paid the whole read+SRC — the hardware
// "pad press isn't immediate" bug. These tests pin:
//   1. a repeat loop trigger schedules from the bake cache (no re-bake),
//   2. prewarm bakes the exact loop variant the first press asks for,
//      deriving keys with the same math as the live trigger path.

import AVFoundation
import XCTest
import ToneForgeEngine
import JamDesktopCore
@testable import JamDesktopAudio

@MainActor
final class ChopPlayerLoopBakeCacheTests: XCTestCase {

    /// A tiny on-disk WAV so triggers have a real readable source.
    private func makeWAV(seconds: Double = 1.0) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("tf_bake_\(UUID().uuidString).wav")
        let sr = 44_100.0
        let fmt = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 1)!
        let frames = AVAudioFrameCount(sr * seconds)
        let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: frames)!
        buf.frameLength = frames
        let ch = buf.floatChannelData![0]
        for i in 0..<Int(frames) { ch[i] = sinf(Float(i) * 0.05) * 0.2 }
        let file = try AVAudioFile(forWriting: url, settings: fmt.settings)
        try file.write(from: buf)
        return url
    }

    func testRepeatFileLoopTriggerHitsBakeCache() throws {
        let engine = AVAudioEngine()
        let player = ChopPlayer(avEngine: engine)
        let url = try makeWAV(seconds: 0.5)
        defer { try? FileManager.default.removeItem(at: url) }
        _ = engine.mainMixerNode
        try engine.start()
        defer { engine.stop() }

        player.trigger(file: url, startSec: nil, endSec: nil, loop: true)
        XCTAssertEqual(player.loopBakeCount, 1, "first press bakes once")

        player.release(fileURL: url)
        player.trigger(file: url, startSec: nil, endSec: nil, loop: true)
        XCTAssertEqual(player.loopBakeCount, 1,
                       "a re-tap must schedule from the bake cache, not re-bake")
    }

    func testPrewarmBakesTheLoopVariantTheFirstPressUses() async throws {
        let engine = AVAudioEngine()
        let player = ChopPlayer(avEngine: engine)
        let url = try makeWAV(seconds: 1.0)
        defer { try? FileManager.default.removeItem(at: url) }
        _ = engine.mainMixerNode
        try engine.start()
        defer { engine.stop() }

        await player.load(stemURLs: ["other": url])
        let chop = Chop(
            idx: 0, startSec: 0.1, endSec: 0.6, durationSec: 0.5, kind: "chord")
        let barSeconds = 0.5

        await player.prewarm(
            [(chop: chop, stem: "other")],
            loopBarSeconds: barSeconds, cycleSeconds: 0)
        XCTAssertEqual(player.loopBakeCount, 1, "prewarm bakes the loop body")

        // The live path: same crossfade floor / bar snap / tile gate as
        // SessionController.onTrigger derives them.
        player.trigger(
            PadAssignment(chop: chop, stem: "other"),
            afterSeconds: 0,
            loop: true,
            crossfadeMs: ChopPlayer.defaultPadCrossfadeMs,
            loopBarSeconds: barSeconds,
            cycleSeconds: 0
        )
        XCTAssertEqual(player.loopBakeCount, 1,
                       "the first press must hit the prewarmed bake, not re-read+bake")
        XCTAssertEqual(player.soundingVoiceCount, 1)
    }
}
