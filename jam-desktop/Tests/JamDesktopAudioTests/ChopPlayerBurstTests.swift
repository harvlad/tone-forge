// ChopPlayerBurstTests.swift
//
// Same-pad hammering regression. Hardware measurement showed rapid
// down/up cycles on one pad exploding to 138/314/516 ms press→trigger
// (and 433 ms padUp→release) while single taps stayed at 2–11 ms. Root
// cause: AVAudioPlayerNode.play() blocks its caller for up to one
// render quantum (~10.7 ms measured at 512f/48k), and the press path
// paid it — plus a stop() — on EVERY press because the old claim
// policy stole the same slot per key and only ever examined the FIRST
// nil-key slot before growing the pool. At finger-drumming rates that
// serialized presses into a main-queue pileup.
//
// The fix is parked-voice rotation: warmUpPool() keeps pool nodes
// `play()`ing with an empty queue ("parked"), so a press only
// scheduleBuffer()s (starts at the next render cycle, no control
// call); a same-key retrigger claims a FRESH parked voice and the
// prior voice release-fades off the press path (web padengine
// behavior: re-trigger fades the old source and starts a new one).
// These tests pin the mechanics: zero play() calls on the press path
// after warm-up, bounded press cost, exactly one keyed voice across a
// retrigger, and the fade terminal re-parking drained voices.

import AVFoundation
import XCTest
import ToneForgeEngine
import JamDesktopCore
@testable import JamDesktopAudio

@MainActor
final class ChopPlayerBurstTests: XCTestCase {

    private func makeWAV(seconds: Double, sr: Double = 44_100) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("tf_hammer_\(UUID().uuidString).wav")
        let fmt = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 2)!
        let frames = AVAudioFrameCount(sr * seconds)
        let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: frames)!
        buf.frameLength = frames
        for c in 0..<2 {
            let ch = buf.floatChannelData![c]
            for i in 0..<Int(frames) {
                ch[i] = sinf(Float(i) * 0.031 + Float(c)) * 0.3
            }
        }
        let file = try AVAudioFile(forWriting: url, settings: fmt.settings)
        try file.write(from: buf)
        return url
    }

    private func makePlayer() throws -> (AVAudioEngine, ChopPlayer, URL) {
        let engine = AVAudioEngine()
        let player = ChopPlayer(avEngine: engine)
        let url = try makeWAV(seconds: 12)
        _ = engine.mainMixerNode
        try engine.start()
        return (engine, player, url)
    }

    private let chop = Chop(idx: 0, startSec: 1.0, endSec: 5.0, durationSec: 4.0,
                            kind: "chord", loopable: true, loopScore: 0.9)

    func testHammerRidesParkedVoicesWithNoPlayCallsOnPressPath() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        await player.load(stemURLs: ["other": url])
        let a = PadAssignment(chop: chop, stem: "other")
        // Production warm-up (session attach does both).
        await player.prewarm([(chop: chop, stem: "other")],
                             loopBarSeconds: 2.0, cycleSeconds: 4.0)
        await player.warmUpPool()
        XCTAssertEqual(player.parkedVoiceCount, 16, "warm-up parks the whole pool")
        XCTAssertEqual(player.immediatePlayCount, 0)

        // 12 zero-gap down/up cycles: worse than any human hammer. Each
        // press must claim a parked voice (12 < 16 available, so this is
        // deterministic) and never call play() on the press path.
        var worst = 0.0
        var total = 0.0
        for _ in 0..<12 {
            let t0 = CFAbsoluteTimeGetCurrent()
            player.trigger(a, afterSeconds: 0, loop: true,
                           crossfadeMs: ChopPlayer.defaultPadCrossfadeMs,
                           loopBarSeconds: 2.0, cycleSeconds: 4.0)
            let dt = (CFAbsoluteTimeGetCurrent() - t0) * 1000
            worst = max(worst, dt)
            total += dt
            player.release(a)
        }
        XCTAssertEqual(player.immediatePlayCount, 0,
                       "presses must ride parked voices — a press-path play() "
                       + "blocks up to a render quantum, the burst serializer")
        // Generous CI bounds: pre-fix every press blocked ~10.7 ms inside
        // play(); parked presses measure ~0.1 ms.
        XCTAssertLessThan(total / 12, 8.0, "mean press cost regressed")
        XCTAssertLessThan(worst, 30.0, "worst press cost regressed")
    }

    func testRetriggerRotatesVoicesKeepingExactlyOneKeyed() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        await player.load(stemURLs: ["other": url])
        let a = PadAssignment(chop: chop, stem: "other")
        await player.warmUpPool()

        player.trigger(a, afterSeconds: 0, loop: true, crossfadeMs: 15,
                       loopBarSeconds: 2.0, cycleSeconds: 4.0)
        XCTAssertEqual(player.soundingVoiceCount, 1)
        // Retrigger WITHOUT a release (sequencer/replay do this): the
        // prior voice must rotate out (fade, unkeyed) and exactly one
        // voice carries the key — the release path keys on it.
        player.trigger(a, afterSeconds: 0, loop: true, crossfadeMs: 15,
                       loopBarSeconds: 2.0, cycleSeconds: 4.0)
        XCTAssertEqual(player.soundingVoiceCount, 1,
                       "rotation keys exactly one voice; the prior fades unkeyed")
        player.release(a)
        XCTAssertEqual(player.soundingVoiceCount, 0)
    }

    func testFadeTerminalReparksDrainedVoices() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        await player.load(stemURLs: ["other": url])
        let a = PadAssignment(chop: chop, stem: "other")
        await player.warmUpPool()
        XCTAssertEqual(player.parkedVoiceCount, 16)

        player.trigger(a, afterSeconds: 0, loop: true, crossfadeMs: 15,
                       loopBarSeconds: 2.0, cycleSeconds: 4.0)
        XCTAssertEqual(player.parkedVoiceCount, 15, "the press drains one parked voice")
        player.release(a)

        // The 20 ms release fade's terminal stops AND re-parks the voice
        // (off the press path), so a sustained jam never starves the
        // parked pool. Poll generously for CI scheduling noise.
        for _ in 0..<100 where player.parkedVoiceCount < 16 {
            try? await Task.sleep(nanoseconds: 20_000_000)
        }
        XCTAssertEqual(player.parkedVoiceCount, 16,
                       "released voice must return to the parked pool")
    }
}
