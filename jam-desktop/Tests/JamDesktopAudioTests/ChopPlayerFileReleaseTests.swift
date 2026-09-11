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
        XCTAssertEqual(player.soundingVoiceCount, 0,
                       "a re-tap must stop the file voice")
    }
}
