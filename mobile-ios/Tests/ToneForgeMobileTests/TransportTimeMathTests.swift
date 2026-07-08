// TransportTimeMathTests.swift
//
// D-022 practice speed: the shared song-time → host-time delay math
// used by SampleScheduler (quantized pad hits) and Metronome
// (clicks). At rate r, a song-time delta of Δ spans Δ / r wall-clock
// seconds. Also pins the metronome-alignment property at 0.5x: a
// click one beat ahead in song-time sits two beat-durations out in
// host time — exactly where a 1/4-quantized pad hit would land.

import XCTest
@testable import ToneForgeMobile

final class TransportTimeMathTests: XCTestCase {

    func testScaledDelayAtFullRate() {
        XCTAssertEqual(
            TransportTimeMath.scaledDelaySeconds(
                targetSong: 10.5, nowSong: 10.0, rate: 1.0),
            0.5, accuracy: 1e-9)
    }

    func testScaledDelayAtHalfRateDoubles() {
        XCTAssertEqual(
            TransportTimeMath.scaledDelaySeconds(
                targetSong: 10.5, nowSong: 10.0, rate: 0.5),
            1.0, accuracy: 1e-9)
    }

    func testScaledDelayNegativeForPastTargets() {
        XCTAssertEqual(
            TransportTimeMath.scaledDelaySeconds(
                targetSong: 9.0, nowSong: 10.0, rate: 0.5),
            -2.0, accuracy: 1e-9)
    }

    func testHostDelayTicksScalesWithRate() {
        let tps = 1_000_000_000.0
        let full = TransportTimeMath.hostDelayTicks(
            targetSong: 1.0, nowSong: 0, rate: 1.0, ticksPerSecond: tps)
        let half = TransportTimeMath.hostDelayTicks(
            targetSong: 1.0, nowSong: 0, rate: 0.5, ticksPerSecond: tps)
        XCTAssertEqual(full, UInt64(tps))
        XCTAssertEqual(half, UInt64(2.0 * tps))
    }

    func testHostDelayTicksNilForNegligibleDelay() {
        let tps = 1_000_000_000.0
        // ≤ 1 ms wall-clock → schedule immediately (at: nil).
        XCTAssertNil(TransportTimeMath.hostDelayTicks(
            targetSong: 0.0005, nowSong: 0, rate: 1.0, ticksPerSecond: tps))
        XCTAssertNil(TransportTimeMath.hostDelayTicks(
            targetSong: 0, nowSong: 0, rate: 1.0, ticksPerSecond: tps))
        XCTAssertNil(TransportTimeMath.hostDelayTicks(
            targetSong: -1, nowSong: 0, rate: 1.0, ticksPerSecond: tps))
    }

    func testHalfRateShrinksTheImmediateWindowInSongTime() {
        let tps = 1_000_000_000.0
        // 0.8 ms of song-time is 1.6 ms wall-clock at 0.5x — past the
        // 1 ms immediate boundary, so it gets a real schedule time.
        XCTAssertNotNil(TransportTimeMath.hostDelayTicks(
            targetSong: 0.0008, nowSong: 0, rate: 0.5, ticksPerSecond: tps))
    }

    func testZeroRateDoesNotDivideByZero() {
        let d = TransportTimeMath.scaledDelaySeconds(
            targetSong: 1.0, nowSong: 0, rate: 0)
        XCTAssertTrue(d.isFinite)
        XCTAssertGreaterThan(d, 0)
    }

    /// Metronome alignment at 0.5x (plan Phase 3): with a 120 BPM
    /// grid, beat N+1 is 0.5 song-seconds ahead; at half speed its
    /// click must be scheduled 1.0 wall-clock second out — the same
    /// host time SampleScheduler computes for a pad hit quantized to
    /// that beat, so click and hit stay sample-adjacent.
    func testClickAndQuantizedHitShareHostTimeAtHalfRate() {
        let tps = TransportClock.ticksPerSecond()
        let grid = MetronomeGrid(bpm: 120, beatsPerBar: 4)
        let nowSong = grid.songTime(ofBeat: 8)          // on beat 8
        let target = grid.songTime(ofBeat: 9)           // next beat
        let clickTicks = TransportTimeMath.hostDelayTicks(
            targetSong: target, nowSong: nowSong,
            rate: 0.5, ticksPerSecond: tps)
        let hitTicks = TransportTimeMath.hostDelayTicks(
            targetSong: target, nowSong: nowSong,
            rate: 0.5, ticksPerSecond: tps)
        XCTAssertEqual(clickTicks, hitTicks)
        XCTAssertEqual(
            Double(clickTicks ?? 0) / tps, 1.0, accuracy: 0.0001)
    }
}
