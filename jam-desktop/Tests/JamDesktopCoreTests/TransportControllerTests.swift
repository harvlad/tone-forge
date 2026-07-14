// TransportControllerTests.swift
//
// Headless transport semantics with a scripted clock and a recording
// audio sink: tempo clamp (0.5…1.0, mirrors session/transport.py),
// loop wrap on tick, end-of-song pause, seek clamping, and the
// discrete-change signal the bridge throttling relies on.

import XCTest
@testable import JamDesktopCore

@MainActor
final class TransportControllerTests: XCTestCase {

    private final class FakeClock: SongClock {
        var nowSongSeconds: Double = 0
    }

    private final class RecordingSink: TransportAudioSink {
        var events: [String] = []
        func play(atSongSeconds seconds: Double) { events.append("play@\(seconds)") }
        func pause() { events.append("pause") }
        func seek(toSongSeconds seconds: Double) { events.append("seek@\(seconds)") }
        func setPlaybackRate(_ rate: Double) { events.append("rate@\(rate)") }
    }

    private var clock: FakeClock!
    private var sink: RecordingSink!
    private var transport: TransportController!

    override func setUp() async throws {
        clock = FakeClock()
        sink = RecordingSink()
        transport = TransportController()
        transport.clock = clock
        transport.audio = sink
        transport.durationSeconds = 100
    }

    // MARK: - Tempo clamp

    func testTempoClampsToHalfThroughFull() {
        transport.setTempo(0.3)
        XCTAssertEqual(transport.tempoPct, 0.5)
        transport.setTempo(1.7)
        XCTAssertEqual(transport.tempoPct, 1.0)
        transport.setTempo(0.75)
        XCTAssertEqual(transport.tempoPct, 0.75)
        XCTAssertEqual(sink.events, ["rate@0.5", "rate@1.0", "rate@0.75"])
    }

    func testTempoNoOpWhenUnchanged() {
        transport.setTempo(1.0)  // already 1.0
        XCTAssertTrue(sink.events.isEmpty)
    }

    // MARK: - Play / pause / seek

    func testPlayPauseRoundTripCapturesClockPosition() {
        transport.play()
        XCTAssertTrue(transport.isPlaying)
        clock.nowSongSeconds = 7.5
        transport.pause()
        XCTAssertFalse(transport.isPlaying)
        XCTAssertEqual(transport.positionSeconds, 7.5)
        XCTAssertEqual(sink.events, ["play@0.0", "pause"])
    }

    func testSeekClampsIntoSong() {
        transport.seek(to: -5)
        XCTAssertEqual(transport.positionSeconds, 0)
        transport.seek(to: 250)
        XCTAssertEqual(transport.positionSeconds, 100)
        transport.seek(to: 42)
        XCTAssertEqual(transport.positionSeconds, 42)
    }

    func testPlayIsIdempotent() {
        transport.play()
        transport.play()
        XCTAssertEqual(sink.events, ["play@0.0"])
    }

    // MARK: - Loop

    func testInvalidLoopIgnored() {
        transport.setLoop(LoopRegion(inSeconds: 10, outSeconds: 10))
        XCTAssertNil(transport.loop)
        transport.setLoop(LoopRegion(inSeconds: 10, outSeconds: 5))
        XCTAssertNil(transport.loop)
    }

    func testTickWrapsAtLoopOut() {
        transport.setLoop(LoopRegion(inSeconds: 10, outSeconds: 20))
        transport.play()
        clock.nowSongSeconds = 15
        transport.tick()
        XCTAssertEqual(transport.positionSeconds, 15)
        clock.nowSongSeconds = 20.01
        transport.tick()
        XCTAssertEqual(transport.positionSeconds, 10)
        XCTAssertTrue(sink.events.contains("seek@10.0"))
        XCTAssertTrue(transport.isPlaying)
    }

    func testClearLoopStopsWrapping() {
        transport.setLoop(LoopRegion(inSeconds: 10, outSeconds: 20))
        transport.clearLoop()
        transport.play()
        clock.nowSongSeconds = 25
        transport.tick()
        XCTAssertEqual(transport.positionSeconds, 25)
        XCTAssertFalse(sink.events.contains("seek@10.0"))
    }

    // MARK: - End of song

    func testTickPausesAtEndOfSong() {
        transport.play()
        clock.nowSongSeconds = 100.5
        transport.tick()
        XCTAssertFalse(transport.isPlaying)
        XCTAssertEqual(transport.positionSeconds, 100)
        XCTAssertEqual(sink.events.last, "pause")
    }

    func testTickIsNoOpWhilePaused() {
        clock.nowSongSeconds = 33
        transport.tick()
        XCTAssertEqual(transport.positionSeconds, 0)
    }

    // MARK: - Discrete-change signal

    func testDiscreteChangesFireCallback() {
        var count = 0
        transport.onDiscreteChange = { count += 1 }
        transport.play()          // 1
        transport.setTempo(0.8)   // 2
        transport.seek(to: 5)     // 3
        transport.setLoop(LoopRegion(inSeconds: 1, outSeconds: 2))  // 4
        transport.clearLoop()     // 5
        transport.pause()         // 6
        XCTAssertEqual(count, 6)
    }

    func testLoopWrapTickDoesNotFireDiscreteChange() {
        // Wraps are continuous playback, not user intents — the
        // bridge's throttled position stream covers them.
        transport.setLoop(LoopRegion(inSeconds: 0, outSeconds: 10))
        transport.play()
        var count = 0
        transport.onDiscreteChange = { count += 1 }
        clock.nowSongSeconds = 10.5
        transport.tick()
        XCTAssertEqual(count, 0)
    }

    // MARK: - Recorder hooks (P4 gap markers)

    func testOnPauseFiresOnUserPauseAndEndOfSong() {
        var pauses = 0
        transport.onPause = { pauses += 1 }
        transport.play()
        transport.pause()          // user pause
        XCTAssertEqual(pauses, 1)
        transport.play()
        clock.nowSongSeconds = 100.5
        transport.tick()           // end-of-song auto pause
        XCTAssertEqual(pauses, 2)
    }

    func testOnSeekFiresWithFromAndTo() {
        var seeks: [(Double, Double)] = []
        transport.onSeek = { seeks.append(($0, $1)) }
        transport.seek(to: 30)
        XCTAssertEqual(seeks.count, 1)
        XCTAssertEqual(seeks[0].0, 0)
        XCTAssertEqual(seeks[0].1, 30)
        transport.seek(to: 250)    // clamps to duration
        XCTAssertEqual(seeks.count, 2)
        XCTAssertEqual(seeks[1].0, 30)
        XCTAssertEqual(seeks[1].1, 100)
    }

    func testOnSeekFiresOnLoopWrap() {
        var seeks: [(Double, Double)] = []
        transport.setLoop(LoopRegion(inSeconds: 10, outSeconds: 20))
        transport.play()
        transport.onSeek = { seeks.append(($0, $1)) }
        clock.nowSongSeconds = 20.5
        transport.tick()
        XCTAssertEqual(seeks.count, 1)
        XCTAssertEqual(seeks[0].0, 20.5)
        XCTAssertEqual(seeks[0].1, 10)
    }
}
