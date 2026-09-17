// ChopPlayerFileReleaseTests.swift
//
// Regression: a borrow (and drumfile) pad plays its downloaded loop through a
// `.file(url)` voice, but `release(_:PadAssignment)` keyed `.chop(stem:idx:)`
// and never matched it — so a re-tap (Loop toggle-off) left the loop playing
// forever ("pad continues to play after I tap it off"). `release(fileURL:)`
// stops the file voice; this pins that a file voice is claimed on trigger and
// released on the matching stop, and that the chop-keyed release does NOT.

import AVFoundation
import XCTest
import ToneForgeEngine
import JamDesktopCore
@testable import JamDesktopAudio

@MainActor
final class ChopPlayerFileReleaseTests: XCTestCase {

    /// A tiny on-disk WAV so trigger(file:) has a real readable source.
    private func makeWAV() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("tf_release_\(UUID().uuidString).wav")
        let sr = 44_100.0
        let fmt = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 1)!
        let frames = AVAudioFrameCount(sr * 0.3)
        let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: frames)!
        buf.frameLength = frames
        let ch = buf.floatChannelData![0]
        for i in 0..<Int(frames) { ch[i] = sinf(Float(i) * 0.05) * 0.2 }
        let file = try AVAudioFile(forWriting: url, settings: fmt.settings)
        try file.write(from: buf)
        return url
    }

    func testFileLoopVoiceStopsOnFileRelease() throws {
        let engine = AVAudioEngine()
        let player = ChopPlayer(avEngine: engine)
        let url = try makeWAV()
        defer { try? FileManager.default.removeItem(at: url) }
        _ = engine.mainMixerNode           // realize the graph
        try engine.start()
        defer { engine.stop() }

        XCTAssertEqual(player.soundingVoiceCount, 0)
        player.trigger(file: url, startSec: nil, endSec: nil, loop: true)
        XCTAssertEqual(player.soundingVoiceCount, 1,
                       "a borrow file loop should claim a voice")

        player.release(fileURL: url)
        // IMMEDIATE accounting (iOS 77231913 fix #7 / web kit.js latch
        // branch): the slot frees at the tap even though the 20 ms
        // release fade is still draining — a latch toggle-off must not
        // read as "sounding" for the rest of the loop pass.
        XCTAssertEqual(player.soundingVoiceCount, 0,
                       "a re-tap must free the voice at the tap, not after the fade")
    }

    func testArmedQuantizedStartDiesOnRelease() throws {
        // A voice armed for a future boundary (quantized launch) that is
        // released before the boundary must be killed: the pendingPlay
        // work item is the cancel token (iOS pendingPlayItem twin) — on
        // the dispatch fallback the deferred play() never fires, on the
        // sample-accurate path player.stop() discards the scheduled
        // start. Without it, hold + quantize + a short tap produced a
        // phantom launch against a released pad.
        let engine = AVAudioEngine()
        let player = ChopPlayer(avEngine: engine)
        let url = try makeWAV()
        defer { try? FileManager.default.removeItem(at: url) }
        _ = engine.mainMixerNode
        try engine.start()
        defer { engine.stop() }

        player.trigger(file: url, startSec: nil, endSec: nil,
                       afterSeconds: 0.4, loop: true)
        XCTAssertEqual(player.soundingVoiceCount, 1, "armed voice claims its slot")
        player.release(fileURL: url)
        XCTAssertEqual(player.soundingVoiceCount, 0)

        // Sleep past the would-be boundary: the cancelled start must not
        // have revived the voice.
        let exp = expectation(description: "past the armed boundary")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { exp.fulfill() }
        wait(for: [exp], timeout: 2.0)
        XCTAssertEqual(player.soundingVoiceCount, 0,
                       "a released armed voice must never fire at its old boundary")
    }

    func testLoopingChopVoiceForceStopsOnRelease() async throws {
        // Desktop's force-release equivalent (iOS e8566e69): a Tap/Loop gate
        // forces the voice to LOOP even on a chop with NO intrinsic loop
        // points, and finger-lift must still stop it. ChopPlayer.release keys
        // on the sounding voice — never on the pad's intrinsic loop flags — so
        // there is no guard to bypass: a gate-forced looping voice on a
        // non-loopable chop is silenced on release. (iOS had to add a `force`
        // flag to defeat its padLoops guard; desktop needs none.)
        let engine = AVAudioEngine()
        let player = ChopPlayer(avEngine: engine)
        let url = try makeWAV()
        defer { try? FileManager.default.removeItem(at: url) }
        _ = engine.mainMixerNode
        try engine.start()
        defer { engine.stop() }
        await player.load(stemURLs: ["other": url])

        // A plain chop — no loopScore / loopPointSec: NOT intrinsically loopable.
        let chop = Chop(
            idx: 0, startSec: 0, endSec: 0.2, durationSec: 0.2, kind: "chord")
        let assignment = PadAssignment(chop: chop, stem: "other")

        // The gate forces the voice to loop (loop: true) so a hold sustains.
        player.trigger(assignment, afterSeconds: 0, loop: true)
        XCTAssertEqual(player.soundingVoiceCount, 1,
                       "a gate-forced loop must claim a live voice")

        player.release(assignment)
        XCTAssertEqual(player.soundingVoiceCount, 0,
                       "finger-lift force-stops the looping voice (no intrinsic-loop guard)")
    }

    func testOneShotFreesSlotOnNaturalEnd() throws {
        // Natural-end accounting (web source.onended, padengine.js:
        // 1242-1256; iOS .dataPlayedBack): a finished one-shot must not
        // hold its slot "sounding" forever — the free-run re-anchor
        // check reads soundingVoiceCount, and a dead jam that reads
        // live pins every later loop to a lattice nobody hears.
        let engine = AVAudioEngine()
        let player = ChopPlayer(avEngine: engine)
        let url = try makeWAV()   // 0.3 s of audio
        defer { try? FileManager.default.removeItem(at: url) }
        _ = engine.mainMixerNode
        try engine.start()
        defer { engine.stop() }

        player.trigger(file: url, startSec: nil, endSec: nil, loop: false)
        XCTAssertEqual(player.soundingVoiceCount, 1)

        // Poll (generously) until the played-back completion clears it.
        let deadline = Date().addingTimeInterval(3.0)
        while player.soundingVoiceCount != 0, Date() < deadline {
            RunLoop.main.run(until: Date().addingTimeInterval(0.05))
        }
        XCTAssertEqual(player.soundingVoiceCount, 0,
                       "a one-shot must free its slot when the audio has played out")
    }
}
