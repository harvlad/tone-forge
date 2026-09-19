// TransportClockRenderClockTests.swift
//
// The transport is slaved to the AUDIO RENDER CLOCK (output-node sample
// time), not a free-running wall clock — otherwise the chord highlight
// drifts against the audio over a long song. And `audibleSongSeconds`
// subtracts the hardware output latency so the highlight doesn't LEAD
// the audible chord. These drive the injectable render-sample +
// output-latency seams manually so the tests stay hermetic (no engine).

import XCTest
@testable import ToneForgeMobile

final class TransportClockRenderClockTests: XCTestCase {

    private final class ManualHostTime: @unchecked Sendable {
        var ticks: UInt64 = 0
    }
    private final class ManualRender: @unchecked Sendable {
        var current: TransportClock.RenderSample?
    }

    /// Song time tracks the audio SAMPLE clock, ignoring the wall clock.
    func testSlavesToSampleTimeNotWallClock() {
        let host = ManualHostTime()
        let render = ManualRender()
        let c = TransportClock(hostTimeProvider: { host.ticks })
        c.attachRenderClock(
            sampleProvider: { render.current },
            outputLatencyProvider: { 0 }
        )
        render.current = .init(sampleTime: 0, sampleRate: 48_000)
        c.play()
        // Advance the sample clock by 1 s of frames; leave the wall clock
        // FROZEN. A wall-clock transport would report 0 here.
        render.current = .init(sampleTime: 48_000, sampleRate: 48_000)
        XCTAssertEqual(c.nowSongSeconds, 1.0, accuracy: 0.0001)
    }

    /// The wall clock drifting has no effect once slaved to sample time.
    func testWallClockDriftDoesNotMoveSongTime() {
        let host = ManualHostTime()
        let render = ManualRender()
        let c = TransportClock(hostTimeProvider: { host.ticks })
        c.attachRenderClock(sampleProvider: { render.current },
                            outputLatencyProvider: { 0 })
        render.current = .init(sampleTime: 1_000, sampleRate: 48_000)
        c.play()
        render.current = .init(sampleTime: 1_000 + 24_000, sampleRate: 48_000) // +0.5 s
        // Wall clock races ahead 100 s — must be ignored.
        host.ticks = UInt64(TransportClock.ticksPerSecond() * 100)
        XCTAssertEqual(c.nowSongSeconds, 0.5, accuracy: 0.0001)
    }

    /// audibleSongSeconds = rendered position − output latency; the raw
    /// nowSongSeconds keeps reporting the rendered (scheduling) position.
    func testAudibleSubtractsOutputLatency() {
        let render = ManualRender()
        let c = TransportClock(hostTimeProvider: { 0 })
        c.attachRenderClock(sampleProvider: { render.current },
                            outputLatencyProvider: { 0.2 })
        render.current = .init(sampleTime: 0, sampleRate: 48_000)
        c.play()
        render.current = .init(sampleTime: 96_000, sampleRate: 48_000)   // 2.0 s
        XCTAssertEqual(c.nowSongSeconds, 2.0, accuracy: 0.0001)
        XCTAssertEqual(c.audibleSongSeconds, 1.8, accuracy: 0.0001)
    }

    /// When stopped/paused there's no audio in flight, so audible == now.
    func testAudibleEqualsNowWhenNotPlaying() {
        let render = ManualRender()
        let c = TransportClock(hostTimeProvider: { 0 })
        c.attachRenderClock(sampleProvider: { render.current },
                            outputLatencyProvider: { 0.2 })
        XCTAssertEqual(c.audibleSongSeconds, c.nowSongSeconds, accuracy: 0.0001)
        XCTAssertEqual(c.audibleSongSeconds, 0.0, accuracy: 0.0001)
    }

    /// Engine not yet rendering at play() → wall-clock fallback, then the
    /// clock LAZILY adopts the sample anchor on the first valid read with
    /// song time continuous across the handoff.
    func testLazyAdoptsSampleAnchorMidSegment() {
        let host = ManualHostTime()
        let render = ManualRender()   // starts nil: engine not rendering
        let c = TransportClock(hostTimeProvider: { host.ticks })
        c.attachRenderClock(sampleProvider: { render.current },
                            outputLatencyProvider: { 0 })
        c.play()
        host.ticks = UInt64(TransportClock.ticksPerSecond() * 0.5)  // 0.5 s wall
        XCTAssertEqual(c.nowSongSeconds, 0.5, accuracy: 0.0001)     // fallback

        // Engine begins rendering: adopt at this instant (song time 0.5).
        render.current = .init(sampleTime: 1_000, sampleRate: 48_000)
        XCTAssertEqual(c.nowSongSeconds, 0.5, accuracy: 0.0001)     // continuous

        // Advance the sample clock 1 s; wall clock stays put — proves we
        // moved onto the sample clock.
        render.current = .init(sampleTime: 1_000 + 48_000, sampleRate: 48_000)
        XCTAssertEqual(c.nowSongSeconds, 1.5, accuracy: 0.0001)
    }

    /// Seeking while playing re-anchors on the sample clock cleanly.
    func testSeekWhilePlayingReanchorsOnSampleClock() {
        let render = ManualRender()
        let c = TransportClock(hostTimeProvider: { 0 })
        c.attachRenderClock(sampleProvider: { render.current },
                            outputLatencyProvider: { 0 })
        render.current = .init(sampleTime: 0, sampleRate: 48_000)
        c.play()
        render.current = .init(sampleTime: 48_000, sampleRate: 48_000)  // +1 s
        c.seek(to: 30)
        render.current = .init(sampleTime: 48_000 + 24_000, sampleRate: 48_000) // +0.5 s
        XCTAssertEqual(c.nowSongSeconds, 30.5, accuracy: 0.0001)
    }

    /// An engine restart resets the output sample clock (sampleTime jumps
    /// BACKWARD). The transport must NOT lurch backward — it holds position
    /// (via the wall clock) and re-anchors onto the fresh sample clock.
    func testEngineRestartSampleResetDoesNotJumpBackward() {
        let host = ManualHostTime()
        let render = ManualRender()
        let c = TransportClock(hostTimeProvider: { host.ticks })
        c.attachRenderClock(sampleProvider: { render.current },
                            outputLatencyProvider: { 0 })
        render.current = .init(sampleTime: 500_000, sampleRate: 48_000)
        c.play()
        render.current = .init(sampleTime: 500_000 + 96_000, sampleRate: 48_000) // +2 s
        // Keep the wall clock in lockstep so the fallback bridge is exact.
        host.ticks = UInt64(TransportClock.ticksPerSecond() * 2.0)
        XCTAssertEqual(c.nowSongSeconds, 2.0, accuracy: 0.0001)

        // Engine restarts: sample clock resets to a small value. The read
        // must stay at ~2 s (no backward lurch), then track forward from
        // the fresh anchor.
        render.current = .init(sampleTime: 128, sampleRate: 48_000)
        XCTAssertEqual(c.nowSongSeconds, 2.0, accuracy: 0.0001)
        render.current = .init(sampleTime: 128 + 48_000, sampleRate: 48_000) // +1 s
        XCTAssertEqual(c.nowSongSeconds, 3.0, accuracy: 0.0001)
    }

    /// With NO render clock attached the clock stays on the injected wall
    /// clock — the existing hermetic-test contract is unchanged.
    func testFallsBackToWallClockWithoutRenderSource() {
        let host = ManualHostTime()
        let c = TransportClock(hostTimeProvider: { host.ticks })
        c.play()
        host.ticks = UInt64(TransportClock.ticksPerSecond() * 2.0)
        XCTAssertEqual(c.nowSongSeconds, 2.0, accuracy: 0.0001)
        // No latency source → audible falls back to now.
        XCTAssertEqual(c.audibleSongSeconds, 2.0, accuracy: 0.0001)
    }
}
