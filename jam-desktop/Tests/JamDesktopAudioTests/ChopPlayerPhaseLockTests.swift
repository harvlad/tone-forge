// ChopPlayerPhaseLockTests.swift
//
// Guards the phase-locked join math in ChopPlayer without an audio
// device, via its pure statics (web padengine parity):
//
//   phaseLockFrames  — fold of the lock-lattice offset (pre-shift
//                      boundary − era anchor) into a start offset
//                      within the baked loop body; the desktop twin of
//                      `phase = ((boundary − anchor) % body + body) % body`.
//   loopProgressValue — the drawn playhead must ADD that phase, or it
//                      reports the position the voice would have had
//                      starting from frame 0 — visually desynced pads
//                      over audio that IS phase-locked (the web
//                      "playheads at different positions" bug).
//   regionFrameCount — body frames from ROUNDING the region length;
//                      trunc-per-edge landed on N or N+1 frames by the
//                      edges' fractional phase, and the ±1-frame
//                      mismatch against the tiled cycle walked
//                      phase-locked loops apart over minutes.

import XCTest
import AVFoundation
@testable import JamDesktopAudio

final class ChopPlayerPhaseLockTests: XCTestCase {

    // MARK: - phaseLockFrames

    func testZeroOffsetStartsAtBodyHead() {
        XCTAssertEqual(
            ChopPlayer.phaseLockFrames(
                offsetSeconds: 0, bodyFrames: 96_000, sampleRate: 48_000),
            0)
    }

    func testOffsetInsideBodyIsVerbatim() {
        // 1 s into a 2 s body at 48 kHz → 48 000 frames.
        XCTAssertEqual(
            ChopPlayer.phaseLockFrames(
                offsetSeconds: 1.0, bodyFrames: 96_000, sampleRate: 48_000),
            48_000)
    }

    func testOffsetFoldsModuloBody() {
        // 5 bars past the anchor of a 4-bar (8 s) body → 1 bar in.
        XCTAssertEqual(
            ChopPlayer.phaseLockFrames(
                offsetSeconds: 10.0, bodyFrames: 384_000, sampleRate: 48_000),
            96_000)
        // A whole number of bodies → phase 0 (restarts on its head).
        XCTAssertEqual(
            ChopPlayer.phaseLockFrames(
                offsetSeconds: 16.0, bodyFrames: 384_000, sampleRate: 48_000),
            0)
    }

    func testNegativeOffsetFoldsIntoBody() {
        // Transport seeked behind the era anchor: the double-mod still
        // lands in [0, body) instead of scheduling a negative offset.
        XCTAssertEqual(
            ChopPlayer.phaseLockFrames(
                offsetSeconds: -1.0, bodyFrames: 96_000, sampleRate: 48_000),
            48_000)
    }

    func testDegenerateArgsAreSafe() {
        XCTAssertEqual(
            ChopPlayer.phaseLockFrames(
                offsetSeconds: 1.0, bodyFrames: 0, sampleRate: 48_000), 0)
        XCTAssertEqual(
            ChopPlayer.phaseLockFrames(
                offsetSeconds: 1.0, bodyFrames: 96_000, sampleRate: 0), 0)
        XCTAssertEqual(
            ChopPlayer.phaseLockFrames(
                offsetSeconds: .infinity, bodyFrames: 96_000, sampleRate: 48_000), 0)
    }

    // MARK: - loopProgressValue (phase-aware playhead)

    func testProgressStartsAtThePhaseNotZero() {
        // Voice launched 1 s into a 2 s body: at launch (0 rendered
        // frames) the playhead must ALREADY read 0.5.
        XCTAssertEqual(
            ChopPlayer.loopProgressValue(
                renderedFrames: 0, phaseFrames: 48_000, bodyFrames: 96_000),
            0.5, accuracy: 1e-12)
    }

    func testProgressWrapsAcrossThePartialFirstPass() {
        // Past the partial first pass (body − phase rendered), the voice
        // is looping whole bodies: (rendered + phase) mod body keeps the
        // readout continuous across the head→loop buffer seam.
        XCTAssertEqual(
            ChopPlayer.loopProgressValue(
                renderedFrames: 48_000, phaseFrames: 48_000, bodyFrames: 96_000),
            0.0, accuracy: 1e-12)
        XCTAssertEqual(
            ChopPlayer.loopProgressValue(
                renderedFrames: 72_000, phaseFrames: 48_000, bodyFrames: 96_000),
            0.25, accuracy: 1e-12)
    }

    func testPhaseZeroMatchesLegacyReadout() {
        XCTAssertEqual(
            ChopPlayer.loopProgressValue(
                renderedFrames: 24_000, phaseFrames: 0, bodyFrames: 96_000),
            0.25, accuracy: 1e-12)
    }

    // MARK: - regionFrameCount (round the length, not per-edge trunc)

    func testFrameCountRoundsRegionLength() {
        // Edges chosen so trunc-per-edge disagrees with the rounded
        // length: start 0.9999999 s, end 3.0 s at 48 kHz.
        //   trunc(end·sr) − trunc(start·sr) = 144000 − 47999 = 96001
        //   round((end − start)·sr)         = 96000
        let n = ChopPlayer.regionFrameCount(
            startSec: 0.9999999, endSec: 3.0,
            sampleRate: 48_000, fileLength: 10_000_000)
        XCTAssertEqual(n, 96_000)
    }

    func testFrameCountClampsToFileRemainder() {
        let n = ChopPlayer.regionFrameCount(
            startSec: 1.0, endSec: 3.0,
            sampleRate: 48_000, fileLength: 100_000)
        XCTAssertEqual(n, 100_000 - 48_000)
    }

    func testFrameCountDegenerateRegionIsZero() {
        XCTAssertEqual(
            ChopPlayer.regionFrameCount(
                startSec: 2.0, endSec: 1.0,
                sampleRate: 48_000, fileLength: 100_000),
            0)
        XCTAssertEqual(
            ChopPlayer.regionFrameCount(
                startSec: 0, endSec: 1, sampleRate: 0, fileLength: 100_000),
            0)
    }
}
