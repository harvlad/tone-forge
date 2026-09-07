// ChopPlayerLoopRegionTests.swift
//
// Guards the phase-lock snap decision in ChopPlayer.trigger without an
// audio device: constant-tempo chop regions get their length re-snapped
// to a whole number of bars (a slightly-off length drifts against the
// loop-lock grid), but analyzer-provided regions (loopScore != nil) are
// whole bars on the song's REAL local downbeats by construction and must
// play VERBATIM — re-snapping them cut the region short of the real
// downbeat, so the wrap landed in the pre-beat gap and the loop audibly
// paused (Doomsday drums: real 3 bars 7.570 s vs 7.545 s at constant
// BPM). The decision lives in the pure `ChopPlayer.loopRegionEndSec`.

import XCTest
import ToneForgeEngine
@testable import JamDesktopAudio

final class ChopPlayerLoopRegionTests: XCTestCase {

    private func chop(
        start: Double, end: Double, loopScore: Double? = nil
    ) -> Chop {
        Chop(
            idx: 0, startSec: start, endSec: end,
            durationSec: end - start, kind: "phrase", loopScore: loopScore
        )
    }

    func testConstantTempoRegionSnapsToWholeBars() {
        // 7.57 s region against a 2.515 s bar → 3 bars → 7.545 s.
        let end = ChopPlayer.loopRegionEndSec(
            chop: chop(start: 0, end: 7.570),
            loop: true, loopBarSeconds: 2.515
        )
        XCTAssertEqual(end, 3 * 2.515, accuracy: 1e-9)
    }

    func testAnalyzerRegionPlaysVerbatim() {
        // loopScore present = kit-builder region on real downbeats:
        // must NOT be re-snapped to the constant-tempo bar length.
        let end = ChopPlayer.loopRegionEndSec(
            chop: chop(start: 0, end: 7.570, loopScore: 0.9),
            loop: true, loopBarSeconds: 2.515
        )
        XCTAssertEqual(end, 7.570, accuracy: 1e-9)
    }

    func testNonLoopingTriggerIsVerbatim() {
        let end = ChopPlayer.loopRegionEndSec(
            chop: chop(start: 1.0, end: 2.3),
            loop: false, loopBarSeconds: 2.0
        )
        XCTAssertEqual(end, 2.3, accuracy: 1e-9)
    }

    func testZeroBarSecondsIsVerbatim() {
        // No tempo → no bar grid to snap to.
        let end = ChopPlayer.loopRegionEndSec(
            chop: chop(start: 0, end: 3.3),
            loop: true, loopBarSeconds: 0
        )
        XCTAssertEqual(end, 3.3, accuracy: 1e-9)
    }

    func testShortRegionSnapsUpToOneFullBar() {
        // A sub-bar region rounds UP to one whole bar, never zero.
        let end = ChopPlayer.loopRegionEndSec(
            chop: chop(start: 4.0, end: 4.4),
            loop: true, loopBarSeconds: 2.0
        )
        XCTAssertEqual(end, 6.0, accuracy: 1e-9)
    }
}
