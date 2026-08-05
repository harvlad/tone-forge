// LinkReconcilerTests.swift
//
// Pure Ableton Link reconciliation math: stretch ratio + shortest
// phase nudge + lock tolerance.

import XCTest
@testable import ToneForgeEngine

final class LinkReconcilerTests: XCTestCase {

    // MARK: - Stretch ratio

    func testStretchRatioMatchesTempo() {
        // Link 140 over song 120 → speed up to 1.166…
        XCTAssertEqual(LinkReconciler.stretchRatio(linkBpm: 140, songBpm: 120)!,
                       140.0 / 120.0, accuracy: 1e-9)
        // Equal tempi → 1.0.
        XCTAssertEqual(LinkReconciler.stretchRatio(linkBpm: 120, songBpm: 120)!,
                       1.0, accuracy: 1e-9)
    }

    func testStretchRatioClamped() {
        // 240 over 60 = 4.0 → clamped to 2.0.
        XCTAssertEqual(LinkReconciler.stretchRatio(linkBpm: 240, songBpm: 60)!, 2.0, accuracy: 1e-9)
        // 30 over 120 = 0.25 → clamped to 0.5.
        XCTAssertEqual(LinkReconciler.stretchRatio(linkBpm: 30, songBpm: 120)!, 0.5, accuracy: 1e-9)
    }

    func testStretchRatioInvalid() {
        XCTAssertNil(LinkReconciler.stretchRatio(linkBpm: 0, songBpm: 120))
        XCTAssertNil(LinkReconciler.stretchRatio(linkBpm: 120, songBpm: 0))
    }

    // MARK: - Phase nudge

    func testPhaseNudgeForward() {
        // Link a quarter-bar ahead of us; bar = 4 * 0.5 = 2 s.
        // diff 0.25 bar → +0.5 s.
        let n = LinkReconciler.phaseNudgeSeconds(
            linkBarPhase: 0.5, songBarPhase: 0.25, beatsPerBar: 4, beatDuration: 0.5
        )!
        XCTAssertEqual(n, 0.5, accuracy: 1e-9)
    }

    func testPhaseNudgeTakesShortestPath() {
        // Link phase 0.1, song phase 0.9 → naive diff -0.8, wrapped to
        // +0.2 bar → +0.4 s (advance a little, not pull back most of a bar).
        let n = LinkReconciler.phaseNudgeSeconds(
            linkBarPhase: 0.1, songBarPhase: 0.9, beatsPerBar: 4, beatDuration: 0.5
        )!
        XCTAssertEqual(n, 0.2 * 2.0, accuracy: 1e-9)
    }

    func testPhaseNudgeInvalid() {
        XCTAssertNil(LinkReconciler.phaseNudgeSeconds(
            linkBarPhase: 0.5, songBarPhase: 0, beatsPerBar: 0, beatDuration: 0.5))
        XCTAssertNil(LinkReconciler.phaseNudgeSeconds(
            linkBarPhase: 0.5, songBarPhase: 0, beatsPerBar: 4, beatDuration: 0))
    }

    // MARK: - Lock tolerance

    func testPhaseLockWithinTolerance() {
        XCTAssertTrue(LinkReconciler.isPhaseLocked(linkBarPhase: 0.501, songBarPhase: 0.5))
        XCTAssertFalse(LinkReconciler.isPhaseLocked(linkBarPhase: 0.6, songBarPhase: 0.5))
        // Wrap-around near the bar boundary counts as locked.
        XCTAssertTrue(LinkReconciler.isPhaseLocked(linkBarPhase: 0.999, songBarPhase: 0.001))
    }
}
