// PerfFXSettingsTests.swift
//
// Pure mapping math for performance FX: filter cutoff curve, gater
// duty, flanger LFO, delay-throw time, stopper rate ramp, and clamps.

import XCTest
@testable import ToneForgeEngine

final class PerfFXSettingsTests: XCTestCase {

    // MARK: - Filter

    func testFilterCutoffLogSweep() {
        let f = PerfFilterConfig(type: .lowPass, minHz: 200, maxHz: 20_000)
        XCTAssertEqual(f.cutoffHz(x: 0), 200, accuracy: 1e-6)
        XCTAssertEqual(f.cutoffHz(x: 1), 20_000, accuracy: 1e-3)
        // Midpoint is the geometric mean (log sweep), not arithmetic.
        XCTAssertEqual(f.cutoffHz(x: 0.5), sqrt(200 * 20_000), accuracy: 1e-3)
    }

    func testFilterResonanceAndClamp() {
        let f = PerfFilterConfig(maxResonanceDb: 18)
        XCTAssertEqual(f.resonanceDb(y: 0), 0, accuracy: 1e-9)
        XCTAssertEqual(f.resonanceDb(y: 1), 18, accuracy: 1e-9)
        // Inverted range clamps so min < max always holds.
        let bad = PerfFilterConfig(minHz: 500, maxHz: 100).clamped()
        XCTAssertLessThan(bad.minHz, bad.maxHz)
    }

    // MARK: - Gater

    func testGaterDutyGate() {
        let g = PerfGaterConfig(subdivisionBeats: 0.5, duty: 0.5, depth: 1.0)
        XCTAssertEqual(g.gain(cellPhase: 0.0), 1.0, accuracy: 1e-9)   // open
        XCTAssertEqual(g.gain(cellPhase: 0.49), 1.0, accuracy: 1e-9)  // still open
        XCTAssertEqual(g.gain(cellPhase: 0.5), 0.0, accuracy: 1e-9)   // closed
        XCTAssertEqual(g.gain(cellPhase: 0.99), 0.0, accuracy: 1e-9)
    }

    func testGaterPartialDepthAndWrap() {
        let g = PerfGaterConfig(duty: 0.5, depth: 0.7)
        // Closed part drops to 1 - depth, not silence.
        XCTAssertEqual(g.gain(cellPhase: 0.8), 0.3, accuracy: 1e-9)
        // Phase wraps: 1.4 == 0.4 → open.
        XCTAssertEqual(g.gain(cellPhase: 1.4), 1.0, accuracy: 1e-9)
    }

    // MARK: - Flanger

    func testFlangerLFOStaysInRange() {
        let f = PerfFlangerConfig(depthMs: 4, baseMs: 3)
        // LFO phase 0 → sin 0 → mid of base..base+depth
        XCTAssertEqual(f.delayMs(lfoPhase: 0.0), 3 + 4 * 0.5, accuracy: 1e-9)
        // phase 0.25 → sin=1 → top
        XCTAssertEqual(f.delayMs(lfoPhase: 0.25), 3 + 4, accuracy: 1e-9)
        // phase 0.75 → sin=-1 → base
        XCTAssertEqual(f.delayMs(lfoPhase: 0.75), 3, accuracy: 1e-9)
    }

    // MARK: - Delay-throw

    func testDelayThrowTempoSync() {
        let d = PerfDelayThrowConfig(timeBeats: 0.75, feedback: 0.6)
        // beat = 0.5 s → 0.375 s echo
        XCTAssertEqual(d.timeSec(beatDuration: 0.5), 0.375, accuracy: 1e-9)
    }

    // MARK: - Stopper

    func testStopperRateRampToZero() {
        let s = PerfStopperConfig(brakeBeats: 1.0)   // brake over 1 beat
        let beat = 0.5
        XCTAssertEqual(s.rate(elapsedSec: 0, beatDuration: beat), 1.0, accuracy: 1e-9)
        XCTAssertEqual(s.rate(elapsedSec: beat, beatDuration: beat), 0.0, accuracy: 1e-9)
        // Monotonic decrease in between.
        let a = s.rate(elapsedSec: 0.1, beatDuration: beat)
        let b = s.rate(elapsedSec: 0.3, beatDuration: beat)
        XCTAssertGreaterThan(a, b)
        XCTAssertGreaterThan(b, 0)
    }

    // MARK: - State

    func testIdleState() {
        XCTAssertTrue(PerfFXState.idle.isIdle)
        var s = PerfFXState.idle
        s.gater = true
        XCTAssertFalse(s.isIdle)
    }
}
