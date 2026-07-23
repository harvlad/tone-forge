// PerformanceFXChainTests.swift
//
// Live-control intent math for the performance-FX chain, exercised
// headless (no bound AVAudioNodes). Confirms disengaged/no-timing
// return nil, engaged effects track the beat grid, and the stopper
// engage/release edges drive the rate sink.

import XCTest
@testable import ToneForgeEngine

@MainActor
final class PerformanceFXChainTests: XCTestCase {

    private func chain(bpm: Double = 120) -> PerformanceFXChain {
        let c = PerformanceFXChain()
        c.beatClock = BeatClock(tempoBpm: bpm)   // beat = 0.5 s, anchor 0
        return c
    }

    func testDisengagedReturnsNil() {
        let c = chain()
        XCTAssertNil(c.gaterGainIntent(now: 0.1))
        XCTAssertNil(c.flangerDelayMsIntent(now: 0.1))
        XCTAssertNil(c.stopperRateIntent(now: 0.1))
        XCTAssertNil(c.throwTimeSecIntent())
        XCTAssertFalse(c.needsModulation)
    }

    func testGaterTracksBeatGrid() {
        let c = chain()
        c.config.gater = PerfGaterConfig(subdivisionBeats: 0.5, duty: 0.5, depth: 1.0)
        c.setState(PerfFXState(gater: true), now: 0)
        // cell = 0.25 s. now=0 → phase 0 → open; now=0.2 → phase 0.8 → closed.
        XCTAssertEqual(c.gaterGainIntent(now: 0.0)!, 1.0, accuracy: 1e-9)
        XCTAssertEqual(c.gaterGainIntent(now: 0.2)!, 0.0, accuracy: 1e-9)
        XCTAssertTrue(c.needsModulation)
    }

    func testFlangerLFOTempoSynced() {
        let c = chain()
        c.config.flanger = PerfFlangerConfig(rateBeats: 2, depthMs: 4, feedback: 0.6, baseMs: 3)
        c.setState(PerfFXState(flanger: true), now: 0)
        // period = 2 beats = 1.0 s. now=0.25 → phase 0.25 → sin=1 → top (base+depth).
        XCTAssertEqual(c.flangerDelayMsIntent(now: 0.25)!, 7, accuracy: 1e-9)
    }

    func testThrowTempoSynced() {
        let c = chain()
        c.config.delayThrow = PerfDelayThrowConfig(timeBeats: 0.75, feedback: 0.6)
        c.setState(PerfFXState(delayThrow: true), now: 0)
        XCTAssertEqual(c.throwTimeSecIntent()!, 0.375, accuracy: 1e-9)  // 0.75 * 0.5 s
    }

    func testStopperEngageReleaseDrivesRateSink() {
        let c = chain()
        c.config.stopper = PerfStopperConfig(brakeBeats: 1.0)  // 0.5 s brake
        var rates: [Double] = []
        c.rateSink = { rates.append($0) }

        // Engage at now=10: rate starts at 1, decays to 0 over the beat.
        c.setState(PerfFXState(stopper: true), now: 10.0)
        XCTAssertEqual(c.stopperRateIntent(now: 10.0)!, 1.0, accuracy: 1e-9)
        XCTAssertEqual(c.stopperRateIntent(now: 10.5)!, 0.0, accuracy: 1e-9)

        // Release: sink snaps back to full speed.
        c.setState(PerfFXState(stopper: false), now: 10.3)
        XCTAssertEqual(rates.last!, 1.0, accuracy: 1e-9)
        XCTAssertNil(c.stopperRateIntent(now: 11.0))
    }

    func testNoTimingReturnsNilEvenEngaged() {
        let c = PerformanceFXChain()          // BeatClock() — no tempo
        c.setState(PerfFXState(gater: true, flanger: true), now: 0)
        XCTAssertNil(c.gaterGainIntent(now: 0.1))
        XCTAssertNil(c.flangerDelayMsIntent(now: 0.1))
    }
}
