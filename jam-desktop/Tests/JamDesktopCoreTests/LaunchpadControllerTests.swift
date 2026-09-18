// LaunchpadControllerTests.swift
//
// Headless controller tests: chop-to-pad mapping, quantized triggers
// against a bundle timeline, slice-mode switches through a fake
// fetcher, LED frames via a fake transport, colorHint parsing.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class LaunchpadControllerTests: XCTestCase {

    // MARK: - Fixtures

    private final class FakeTransport: LaunchpadTransport {
        var connectionState: LaunchpadConnectionState { .onScreen }
        var onPadDown: ((LaunchpadPad) -> Void)?
        var onPadUp: ((LaunchpadPad) -> Void)?

        var lights: [LaunchpadPad: LaunchpadLight] = [:]
        var frameCount = 0

        func setLight(_ light: LaunchpadLight, at pad: LaunchpadPad) {
            lights[pad] = light
        }
        func setLights(_ frame: [LaunchpadPad: LaunchpadLight]) {
            frameCount += 1
            for (pad, light) in frame { lights[pad] = light }
        }
        func clearLights() { lights.removeAll() }
    }

    private struct FakeFetcher: LaunchpadChopsFetching {
        var chops: [Chop] = []
        var error: Error?

        func fetchChops(
            baseURL: URL, analysisId: String, stem: String?, sliceMode: String?
        ) async throws -> [Chop] {
            if let error { throw error }
            return chops
        }
    }

    private func chop(
        _ idx: Int, start: Double = 0, end: Double = 1,
        symbol: String? = nil, colorHint: String? = nil
    ) -> Chop {
        Chop(
            idx: idx, startSec: start, endSec: end,
            durationSec: end - start, kind: "chord",
            chordSymbol: symbol, colorHint: colorHint
        )
    }

    private func bundle(
        beats: [Double] = [], downbeats: [Double] = [],
        tempoBpm: Double? = nil,
        presets: [String: BundlePreset] = [:]
    ) -> SongBundle {
        SongBundle(
            bundleVersion: 1,
            analysisId: "a1",
            meta: BundleMeta(
                title: "T", artist: "A", sourceUrl: "",
                durationSec: 120, tempoBpm: tempoBpm
            ),
            timeline: BundleTimeline(
                chords: [], sections: [], beats: beats, downbeats: downbeats
            ),
            stems: [],
            presets: presets
        )
    }

    private func makeController(
        now: Double = 0, fetcher: FakeFetcher = FakeFetcher()
    ) -> LaunchpadController {
        LaunchpadController(nowProvider: { now }, fetcher: fetcher)
    }

    // MARK: - Grid mapping

    func testChopsMapRowMajorFromTopLeftInIdxOrder() {
        let controller = makeController()
        // Deliberately unsorted input; mapping must follow idx order.
        controller.setChops(
            [chop(8), chop(0), chop(7), chop(1)],
            stem: "other", sliceMode: "chord"
        )
        XCTAssertEqual(
            controller.assignments[LaunchpadPad(row: 0, col: 0)]?.chop.idx, 0)
        XCTAssertEqual(
            controller.assignments[LaunchpadPad(row: 0, col: 1)]?.chop.idx, 1)
        XCTAssertEqual(
            controller.assignments[LaunchpadPad(row: 0, col: 2)]?.chop.idx, 7)
        XCTAssertEqual(
            controller.assignments[LaunchpadPad(row: 0, col: 3)]?.chop.idx, 8)
        XCTAssertNil(controller.assignments[LaunchpadPad(row: 1, col: 0)])
        XCTAssertEqual(controller.assignments.count, 4)
        XCTAssertEqual(controller.stem, "other")
        XCTAssertEqual(controller.sliceMode, "chord")
    }

    func testGridCapsAtSixtyFourChops() {
        let controller = makeController()
        controller.setChops(
            (0..<80).map { chop($0) }, stem: "drums", sliceMode: "beat")
        XCTAssertEqual(controller.assignments.count, 64)
        // Slot 63 = bottom-right; idx 64+ dropped.
        XCTAssertEqual(
            controller.assignments[LaunchpadPad(row: 7, col: 7)]?.chop.idx, 63)
    }

    func testConfigurePrefersHarmonicPreset() {
        let controller = makeController()
        let presets = [
            "sections": BundlePreset(
                stem: "drums", sliceMode: "section", chops: [chop(0)]),
            "harmonic": BundlePreset(
                stem: "other", sliceMode: "chord", chops: [chop(0), chop(1)]),
        ]
        controller.configure(bundle: bundle(presets: presets))
        XCTAssertEqual(controller.stem, "other")
        XCTAssertEqual(controller.sliceMode, "chord")
        XCTAssertEqual(controller.assignments.count, 2)
    }

    // MARK: - Triggers

    // (testPadDownQuantizesToNextBeat removed: it asserted that a pad
    // quantizes to the next sub-bar BEAT — a behavior the Tap|Loop|Latch
    // model deliberately dropped. Tap now fires immediately (see
    // testTapFiresNowNotQuantizedWhileRolling); Loop/Latch bar-lock and
    // never sub-bar-quantize (testLoopNeverQuantizesToSubBarGrid,
    // testLoopLockRollingSnapsToRealDownbeatGrid). No coverage lost.)

    func testPadDownWithinGraceFiresImmediately() {
        var now = 0.0
        let controller = LaunchpadController(
            nowProvider: { now }, fetcher: FakeFetcher())
        controller.configure(bundle: bundle(
            beats: [0, 0.5, 1.0],
            presets: ["harmonic": BundlePreset(
                stem: "other", sliceMode: "chord", chops: [chop(0)])]
        ))
        controller.quantize = .quarter

        var fireAt: Double?
        controller.onTrigger = { _, _, t, _ in fireAt = t }

        now = 0.55  // 50 ms past the 0.5 beat — inside the grace window
        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt, 0.55)
    }

    func testQuantizeOffFiresAtPressTime() {
        let controller = makeController(now: 3.21)
        controller.setChops([chop(0)], stem: "other", sliceMode: "chord")

        var fireAt: Double?
        controller.onTrigger = { _, _, t, _ in fireAt = t }
        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt, 3.21)
    }

    func testUnassignedPadDoesNotTrigger() {
        let controller = makeController()
        controller.setChops([chop(0)], stem: "other", sliceMode: "chord")

        var fired = 0
        controller.onTrigger = { _, _, _, _ in fired += 1 }
        controller.padDown(LaunchpadPad(row: 5, col: 5))
        XCTAssertEqual(fired, 0)
        XCTAssertTrue(controller.activePads.isEmpty)
    }

    func testPadUpReleasesAndClearsActive() {
        let controller = makeController()
        controller.setChops([chop(0)], stem: "other", sliceMode: "chord")

        var released: [PadAssignment] = []
        controller.onRelease = { released.append($1) }

        let pad = LaunchpadPad(row: 0, col: 0)
        controller.padDown(pad)
        controller.padUp(pad)
        XCTAssertEqual(released.count, 1)
        XCTAssertEqual(released[0].chop.idx, 0)
        XCTAssertFalse(controller.activePads.contains(pad))
    }

    // MARK: - Loop arming / quantize regression net
    //
    // These pin the padDown arming matrix that keeps regressing during
    // audio work: tap = instant, loop+lock+rolling = the song's REAL bar
    // grid (extrapolated at tempo beyond either end), loop+lock+stopped =
    // wall-clock free-run grid, lock-off = the user's Quantize control
    // (sub-bar floored to .bar, .off = instant) — and EVERY loop launch
    // phase-joins its era (D-029). Song time comes from nowProvider; the
    // free-run grid runs on the injectable hostNowSeconds host clock.

    /// Loop-ready controller: tempo-carrying bundle with three chops on
    /// the top row, transport state + host clock injectable per test.
    private func loopController(
        tempoBpm: Double?, now: @escaping () -> Double
    ) -> LaunchpadController {
        let controller = LaunchpadController(
            nowProvider: now, fetcher: FakeFetcher())
        controller.configure(bundle: bundle(
            downbeats: [0, 2, 4],
            tempoBpm: tempoBpm,
            presets: ["harmonic": BundlePreset(
                stem: "other", sliceMode: "chord",
                chops: [chop(0), chop(1), chop(2)])]
        ))
        return controller
    }

    func testDefaultPlaybackModeIsFollowAndFiresImmediately() {
        var now = 1.23
        let controller = loopController(tempoBpm: 120, now: { now })
        XCTAssertEqual(controller.playbackMode, .follow)  // Follow is the new default

        var fireAt: Double?
        controller.onTrigger = { _, _, t, _ in fireAt = t }

        // Stopped transport: Follow is instant.
        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt, 1.23)
        // Rolling transport, quantize off: still instant.
        controller.isTransportPlaying = { true }
        now = 4.56
        controller.padDown(LaunchpadPad(row: 0, col: 1))
        XCTAssertEqual(fireAt, 4.56)
    }

    func testLoopLockRollingSnapsToRealDownbeatGrid() {
        // Rolling lock launches land on the song's REAL downbeats
        // ([0, 2, 4] here), NOT the constant-tempo k·bar lattice from
        // song 0 (100 BPM would put that at 4.8) — real first downbeats
        // are never at t=0 and real tempo wobbles, so the synthetic grid
        // armed pads off the beat (web _transportLaunchTime, iOS
        // nextLoopBoundary; the pre-D-029 desktop divergence).
        var now = 3.0
        let controller = loopController(tempoBpm: 100, now: { now })
        controller.playbackMode = .latch     // the quantized launch path
        XCTAssertTrue(controller.loopLockEnabled)  // lock is the default
        controller.isTransportPlaying = { true }

        var fireAt: Double?
        controller.onTrigger = { _, _, t, _ in fireAt = t }

        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt ?? -1, 4.0, accuracy: 1e-9)

        // 50 ms past the 4.0 downbeat — inside the 0.08 s grace, fires
        // NOW instead of waiting a whole bar.
        now = 4.05
        controller.padDown(LaunchpadPad(row: 0, col: 1))
        XCTAssertEqual(fireAt ?? -1, 4.05, accuracy: 1e-9)
    }

    func testLoopLockRollingExtrapolatesForwardPastLastDownbeat() {
        // Past the last analyzed downbeat (4.0) the bar grid extrapolates
        // FORWARD at tempo (bar 2.4 s at 100 BPM): boundaries 6.4, 8.8, …
        // A late-song press must land on-grid, not fire instantly off it
        // (web padengine.js:1047, iOS 77231913 fix #3).
        var now = 4.85
        let controller = loopController(tempoBpm: 100, now: { now })
        controller.playbackMode = .latch     // the quantized launch path
        controller.isTransportPlaying = { true }

        var fireAt: Double?
        controller.onTrigger = { _, _, t, _ in fireAt = t }

        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt ?? -1, 6.4, accuracy: 1e-9)

        // Within grace of an extrapolated boundary → now.
        now = 8.85
        controller.padDown(LaunchpadPad(row: 0, col: 1))
        XCTAssertEqual(fireAt ?? -1, 8.85, accuracy: 1e-9)
    }

    func testLoopLockRollingExtrapolatesBackwardBeforeFirstDownbeat() {
        // Deep-intro press: first analyzed downbeat at 10 s, tap at 3 s.
        // Waiting for grid[0] armed pads for the whole intro (the
        // "never-ending hourglass" — the analyzer's first downbeat on a
        // long ambient intro can sit 50+ s in). The grid extrapolates
        // BACKWARD from grid[0] at tempo (bar 2 s at 120 BPM): virtual
        // boundaries …, 4, 6, 8 converge exactly on the real 10 s
        // downbeat (web _transportLaunchTime backward branch, iOS
        // 38dc6e5e).
        var now = 3.0
        let controller = LaunchpadController(
            nowProvider: { now }, fetcher: FakeFetcher())
        controller.configure(bundle: bundle(
            downbeats: [10, 12],
            tempoBpm: 120,
            presets: ["harmonic": BundlePreset(
                stem: "other", sliceMode: "chord",
                chops: [chop(0), chop(1)])]
        ))
        controller.playbackMode = .latch     // the quantized launch path
        controller.isTransportPlaying = { true }

        var fireAt: Double?
        controller.onTrigger = { _, _, t, _ in fireAt = t }

        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt ?? -1, 4.0, accuracy: 1e-9)

        // Within grace of a virtual boundary → now.
        now = 6.05
        controller.padDown(LaunchpadPad(row: 0, col: 1))
        XCTAssertEqual(fireAt ?? -1, 6.05, accuracy: 1e-9)
    }

    func testLoopLockRollingPhaseIsBoundaryMinusAnchor() {
        // The join phase measures lattice BOUNDARIES from the era anchor
        // (the first loop's boundary) — never the onset-shifted start,
        // which would cancel the launch compensation and flam the pads.
        var now = 3.0
        let controller = loopController(tempoBpm: 100, now: { now })  // downbeats [0,2,4]
        controller.playbackMode = .latch     // the quantized launch path
        controller.isTransportPlaying = { true }

        var fireAt: Double?
        var phase: Double?
        controller.onTrigger = { _, _, t, p in fireAt = t; phase = p }

        // First loop anchors the era: real downbeat 4.0, phase 0.
        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt ?? -1, 4.0, accuracy: 1e-9)
        XCTAssertEqual(phase ?? -1, 0, accuracy: 1e-9)

        // Past the grid: boundary extrapolates to 4 + 2·2.4 = 8.8, and
        // phase = 8.8 − 4.0 = 4.8 (the audio layer folds this mod the
        // baked body).
        now = 8.0
        controller.padDown(LaunchpadPad(row: 0, col: 1))
        XCTAssertEqual(fireAt ?? -1, 8.8, accuracy: 1e-9)
        XCTAssertEqual(phase ?? -1, 4.8, accuracy: 1e-9)
    }

    func testLoopLockStoppedTransportFreeRunsOnAnchoredBarGrid() {
        // Transport STOPPED (song clock frozen): loops must not quantize
        // against the dead song grid. First press fires immediately and
        // anchors a wall-clock BAR grid (2 s at 120 BPM — a tap waits
        // ≤ 1 bar, never the full 8 s cycle); later presses queue to it.
        var now = 5.0
        var hostNow = 1000.0
        let controller = loopController(tempoBpm: 120, now: { now })  // bar 2 s
        controller.playbackMode = .latch     // the quantized launch path
        controller.isTransportPlaying = { false }
        controller.hostNowSeconds = { hostNow }

        var fireAt: Double?
        var phase: Double?
        controller.onTrigger = { _, _, t, p in fireAt = t; phase = p }
        let padA = LaunchpadPad(row: 0, col: 0)
        let padB = LaunchpadPad(row: 0, col: 1)
        let padC = LaunchpadPad(row: 0, col: 2)

        // First loop: instant, anchors the grid at hostNow = 1000, phase 0.
        controller.padDown(padA)
        XCTAssertEqual(fireAt ?? -1, 5.0, accuracy: 1e-9)
        XCTAssertEqual(phase ?? -1, 0, accuracy: 1e-9)

        // 3 s after the anchor = 1 s into bar 2: waits to the NEXT bar
        // (1 s away), not the cycle wrap 5 s away. The join phase is the
        // boundary's wall offset from the anchor: 2 bars = 4 s.
        hostNow = 1003.0
        now = 5.5
        controller.padDown(padB)
        XCTAssertEqual(fireAt ?? -1, 5.5 + 1.0, accuracy: 1e-9)
        XCTAssertEqual(phase ?? -1, 4.0, accuracy: 1e-9)

        // 50 ms after a bar boundary — inside the 0.08 s grace: NOW, on
        // the boundary 4 bars (8 s) after the anchor.
        hostNow = 1008.05
        now = 6.0
        controller.padDown(padC)
        XCTAssertEqual(fireAt ?? -1, 6.0, accuracy: 1e-9)
        XCTAssertEqual(phase ?? -1, 8.0, accuracy: 1e-9)
    }

    func testFreeRunReanchorsAfterAllPadsReleased() {
        // Releasing ALL pads abandons the free-run grid; the next loop
        // press fires immediately on a FRESH grid — BY DESIGN (a new
        // jam shouldn't wait on a grid nobody can hear).
        var now = 5.0
        var hostNow = 1000.0
        let controller = loopController(tempoBpm: 120, now: { now })  // bar 2 s
        // Latch (a quantized, latched loop) so a re-tap toggles the pad OFF —
        // the free-run re-anchor keys on activePads/silence, which Latch's
        // toggle-off empties. (One-Shot/Follow are instant hold gates — a
        // re-tap wouldn't toggle; neither exercises this quantized grid.)
        controller.playbackMode = .latch
        controller.isTransportPlaying = { false }
        controller.hostNowSeconds = { hostNow }

        var fireAt: Double?
        var phase: Double?
        controller.onTrigger = { _, _, t, p in fireAt = t; phase = p }
        let padA = LaunchpadPad(row: 0, col: 0)
        let padB = LaunchpadPad(row: 0, col: 1)

        controller.padDown(padA)               // anchor at 1000
        controller.padDown(padA)               // latch re-tap toggles it OFF
        XCTAssertTrue(controller.activePads.isEmpty)

        // Mid-old-bar press: would owe a wait on the stale grid; instead
        // it re-anchors, fires immediately, and the join phase resets to 0
        // (a stale era's phase would start the fresh jam mid-body).
        hostNow = 1003.7
        now = 6.0
        fireAt = nil
        controller.padDown(padA)
        XCTAssertEqual(fireAt ?? -1, 6.0, accuracy: 1e-9)
        XCTAssertEqual(phase ?? -1, 0, accuracy: 1e-9)

        // And the NEW anchor governs: 1 s into the fresh grid → 1 s wait
        // to its first bar, phase = 1 bar (the stale 1000-anchor would
        // have owed 2 − 0.7 = 1.3 s).
        hostNow = 1004.7
        now = 6.3
        controller.padDown(padB)
        XCTAssertEqual(fireAt ?? -1, 6.3 + 1.0, accuracy: 1e-9)
        XCTAssertEqual(phase ?? -1, 2.0, accuracy: 1e-9)
    }

    func testLoopLockNoTempoFallsBackToCycleGrid() {
        // No tempo → no bar length; the lock grid falls back to the full
        // loop cycle (8 s kit window) instead of never quantizing.
        var now = 5.0
        var hostNow = 1000.0
        let controller = loopController(tempoBpm: nil, now: { now })
        controller.playbackMode = .latch     // the quantized launch path
        controller.isTransportPlaying = { false }
        controller.hostNowSeconds = { hostNow }

        var fireAt: Double?
        controller.onTrigger = { _, _, t, _ in fireAt = t }
        controller.padDown(LaunchpadPad(row: 0, col: 0))   // anchors at 1000

        hostNow = 1003.0
        now = 5.5
        controller.padDown(LaunchpadPad(row: 0, col: 1))
        XCTAssertEqual(fireAt ?? -1, 5.5 + 5.0, accuracy: 1e-9)  // 8 − 3
    }

    func testLockOffQuantizeOffLoopFiresNowAndStillJoins() {
        // The converged web/iOS contract (padengine.js:1143 unquantized
        // launches; iOS 77231913 fix #6): lock off + quantize .off means a
        // loop starts NOW — but it still phase-JOINS the rolling era at
        // boundary = now, instead of restarting its bar 1 against the mix.
        // (Pre-D-029 desktop force-bar-quantized these AND skipped the
        // join — divergent on both counts.)
        var now = 0.7
        let controller = loopController(tempoBpm: 100, now: { now })
        controller.playbackMode = .latch     // the quantized launch path
        controller.loopLockEnabled = false
        controller.isTransportPlaying = { true }
        controller.quantize = .off

        var fireAt: Double?
        var phase: Double?
        controller.onTrigger = { _, _, t, p in fireAt = t; phase = p }

        // First loop: instant, and it ANCHORS the era (boundary 0.7).
        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt ?? -1, 0.7, accuracy: 1e-9)
        XCTAssertEqual(phase ?? -1, 0, accuracy: 1e-9)

        // Second loop two seconds on: instant again, joining mid-body at
        // phase = now − anchor (the audio layer folds mod the cycle).
        now = 2.7
        controller.padDown(LaunchpadPad(row: 0, col: 1))
        XCTAssertEqual(fireAt ?? -1, 2.7, accuracy: 1e-9)
        XCTAssertEqual(phase ?? -1, 2.0, accuracy: 1e-9)

        // The user's explicit Quantize control still applies with lock
        // off (.phrase, no sections → t).
        now = 3.1
        controller.quantize = .phrase
        controller.padDown(LaunchpadPad(row: 0, col: 2))
        XCTAssertEqual(fireAt ?? -1, 3.1, accuracy: 1e-9)
    }

    func testStoppedLockOffLoopIsInstantAndJoinsFreeRunEra() {
        // Transport stopped + lock off: instant start (no quantize against
        // a dead clock), but the join still measures the free-run era on
        // the HOST clock — an unquantized loop lands at the running
        // cycle position, not back at its bar 1.
        var now = 5.0
        var hostNow = 1000.0
        let controller = loopController(tempoBpm: 120, now: { now })
        controller.playbackMode = .latch     // the quantized launch path
        controller.loopLockEnabled = false
        controller.isTransportPlaying = { false }
        controller.hostNowSeconds = { hostNow }

        var fireAt: Double?
        var phase: Double?
        controller.onTrigger = { _, _, t, p in fireAt = t; phase = p }

        // First loop anchors the free-run era, phase 0.
        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt ?? -1, 5.0, accuracy: 1e-9)
        XCTAssertEqual(phase ?? -1, 0, accuracy: 1e-9)

        // 3.3 s of wall time later: instant, joining at 3.3 s into the era.
        hostNow = 1003.3
        now = 5.0   // song clock frozen
        controller.padDown(LaunchpadPad(row: 0, col: 1))
        XCTAssertEqual(fireAt ?? -1, 5.0, accuracy: 1e-9)
        XCTAssertEqual(phase ?? -1, 3.3, accuracy: 1e-9)
    }

    func testSoundingVoiceHoldsFreeRunAnchor() {
        // The free-run re-anchor keys on SILENCE, not on latched pads: a
        // one-shot still ringing after its pad left activePads must hold
        // the lattice (web keeps _lockAnchor while _voices.size > 0,
        // padengine.js:1154; iOS soundingPadKeys). With the provider
        // reporting a live voice, a press after all pads released still
        // queues to the OLD grid; once silence is real, it re-anchors.
        var now = 5.0
        var hostNow = 1000.0
        var voiceSounding = true
        let controller = loopController(tempoBpm: 120, now: { now })  // bar 2 s
        // Latch: re-tap toggles a pad off so this test can exercise the
        // silence-held free-run anchor across toggle-offs.
        controller.playbackMode = .latch
        controller.isTransportPlaying = { false }
        controller.hostNowSeconds = { hostNow }
        controller.isAnyVoiceSounding = { voiceSounding }

        var fireAt: Double?
        var phase: Double?
        controller.onTrigger = { _, _, t, p in fireAt = t; phase = p }
        let padA = LaunchpadPad(row: 0, col: 0)
        let padB = LaunchpadPad(row: 0, col: 1)

        controller.padDown(padA)               // anchors at 1000
        controller.padDown(padA)               // toggle OFF — activePads empty
        XCTAssertTrue(controller.activePads.isEmpty)

        // A voice still sounds: 1 s into bar 2 of the OLD grid → waits
        // 1 s to its next bar, phase 4 s (2 bars) — NO re-anchor.
        hostNow = 1003.0
        now = 6.0
        controller.padDown(padB)
        XCTAssertEqual(fireAt ?? -1, 6.0 + 1.0, accuracy: 1e-9)
        XCTAssertEqual(phase ?? -1, 4.0, accuracy: 1e-9)
        controller.padDown(padB)               // toggle OFF again

        // True silence now: the same press re-anchors and fires NOW.
        voiceSounding = false
        hostNow = 1005.0
        now = 7.0
        controller.padDown(padA)
        XCTAssertEqual(fireAt ?? -1, 7.0, accuracy: 1e-9)
        XCTAssertEqual(phase ?? -1, 0, accuracy: 1e-9)
    }

    // MARK: - 3-way One-Shot | Follow | Latch finger contracts
    //
    // The shipped iOS taxonomy (28fec22e, atop 37f851d6/d56dc351/f081d725/
    // e8566e69), ported: One-Shot = finger-drumming gate (fires NOW ignoring
    // quantize/lock even while rolling, FROM THE SAMPLE TOP/phase 0, loops-
    // while-held, releases on lift, retriggers each tap); Follow = the same
    // zero-latency gate but JOINS the shared clock phase (mid-body) so layered
    // pads lock — desktop's old "Tap", renamed; Latch = quantized TOGGLE (holds,
    // re-tap stops). Only Latch quantizes / drives the shared grid.

    func testFollowFiresNowNotQuantizedWhileRolling() {
        // Follow is always immediate: with the transport ROLLING and a Bar grid
        // set, Latch would arm to the next downbeat (2.0 here); Follow fires at
        // the press time (no bar-wait, no hourglass). iOS forceInstantLaunch —
        // the .bar force is Latch-only now.
        var now = 0.7
        let controller = loopController(tempoBpm: 100, now: { now })  // downbeats [0,2,4]
        XCTAssertEqual(controller.playbackMode, .follow)  // Follow is the default
        controller.quantize = .bar
        controller.isTransportPlaying = { true }

        var fireAt: Double?
        controller.onTrigger = { _, _, t, _ in fireAt = t }
        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt ?? -1, 0.7, accuracy: 1e-9)

        // One-Shot is instant too (the finger-drumming gate) — still fires NOW.
        controller.playbackMode = .oneShot
        now = 0.7
        controller.padDown(LaunchpadPad(row: 0, col: 1))
        XCTAssertEqual(fireAt ?? -1, 0.7, accuracy: 1e-9)

        // Latch over the same grid DOES arm to the downbeat (2.0) — proves the
        // instant fire is gate-specific, not a dead quantizer.
        controller.playbackMode = .latch
        now = 0.7
        controller.padDown(LaunchpadPad(row: 0, col: 2))
        XCTAssertEqual(fireAt ?? -1, 2.0, accuracy: 1e-9)
    }

    func testOneShotStartsFromZeroWhileFollowJoinsMidBody() {
        // The One-Shot mechanic (iOS forceZeroPhase, 28fec22e): both One-Shot
        // and Follow fire instantly, but One-Shot starts the voice at the
        // SAMPLE TOP (lockPhaseSeconds == 0, no lattice join) while Follow
        // JOINS the running era mid-body (phase = now − era anchor).
        var now = 1.0
        let controller = loopController(tempoBpm: 100, now: { now })  // downbeats [0,2,4]
        controller.isTransportPlaying = { true }

        var phase: Double?
        controller.onTrigger = { _, _, _, p in phase = p }

        // Latch establishes the rolling era: it quantizes to the 2.0 downbeat
        // and anchors the shared lattice there (phase 0 for the first launch).
        controller.playbackMode = .latch
        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(phase ?? -1, 0, accuracy: 1e-9)

        // Follow now lands mid-body: phase = now − anchor = 3.5 − 2.0 = 1.5
        // (the audio layer folds this mod the baked body so layered pads lock).
        controller.playbackMode = .follow
        now = 3.5
        controller.padDown(LaunchpadPad(row: 0, col: 1))
        XCTAssertEqual(phase ?? -1, 1.5, accuracy: 1e-9)

        // One-Shot over the SAME live era forces phase 0 — it retriggers from
        // the sample top instead of joining, the whole point of the mode.
        controller.playbackMode = .oneShot
        now = 4.2
        controller.padDown(LaunchpadPad(row: 0, col: 2))
        XCTAssertEqual(phase ?? -1, 0, accuracy: 1e-9)
    }

    func testGateModesAreHoldToPlayReleasesOnPadUp() {
        // One-Shot and Follow are both momentary HOLD-to-play gates: press
        // starts the voice, finger-lift RELEASES it immediately. They differ
        // only in start phase, never on release. A re-tap does NOT toggle off.
        for mode in [LaunchpadController.PadPlaybackMode.oneShot, .follow] {
            let controller = makeController()
            controller.setChops([chop(0)], stem: "other", sliceMode: "chord")
            controller.playbackMode = mode

            var releases = 0
            controller.onRelease = { _, _ in releases += 1 }
            let pad = LaunchpadPad(row: 0, col: 0)

            controller.padDown(pad)
            XCTAssertTrue(controller.activePads.contains(pad))
            controller.padUp(pad)
            XCTAssertEqual(releases, 1, "\(mode) is hold-to-play: padUp releases NOW")
            XCTAssertFalse(controller.activePads.contains(pad))
        }
    }

    func testPlaybackModeEnumContract() {
        // Case set/order, labels, and the two behavior axes (parity with iOS
        // SampleTriggerMode, 28fec22e).
        typealias Mode = LaunchpadController.PadPlaybackMode
        XCTAssertEqual(Mode.allCases, [.oneShot, .follow, .latch])
        XCTAssertEqual(Mode.oneShot.title, "One-Shot")
        XCTAssertEqual(Mode.follow.title, "Follow")
        XCTAssertEqual(Mode.latch.title, "Latch")
        // Only One-Shot starts from the sample top (phase 0).
        XCTAssertTrue(Mode.oneShot.startsFromZero)
        XCTAssertFalse(Mode.follow.startsFromZero)
        XCTAssertFalse(Mode.latch.startsFromZero)
        // Only Latch quantizes / drives the shared grid (the iOS rolls-clock
        // axis); One-Shot and Follow are zero-latency gates.
        XCTAssertFalse(Mode.oneShot.quantizesLaunch)
        XCTAssertFalse(Mode.follow.quantizesLaunch)
        XCTAssertTrue(Mode.latch.quantizesLaunch)
        // The voice force-loops in every mode (iOS loopOverride twin).
        XCTAssertTrue(Mode.oneShot.loops)
        XCTAssertTrue(Mode.follow.loops)
        XCTAssertTrue(Mode.latch.loops)
        // Only Latch is a toggle.
        XCTAssertTrue(Mode.latch.isToggle)
        XCTAssertFalse(Mode.follow.isToggle)
        XCTAssertFalse(Mode.oneShot.isToggle)
    }

    func testLegacyModeMigration() {
        // A raw value persisted before 28fec22e (tap|loop|latch) migrates onto
        // the new taxonomy rather than decoding to an unknown mode: retired
        // tap/loop fold onto Follow, latch stays latch, current cases round-trip.
        typealias Mode = LaunchpadController.PadPlaybackMode
        XCTAssertEqual(Mode.migratedFromLegacy("tap"), .follow)
        XCTAssertEqual(Mode.migratedFromLegacy("loop"), .follow)
        XCTAssertEqual(Mode.migratedFromLegacy("latch"), .latch)
        XCTAssertEqual(Mode.migratedFromLegacy("oneShot"), .oneShot)
        XCTAssertEqual(Mode.migratedFromLegacy("follow"), .follow)
        XCTAssertNil(Mode.migratedFromLegacy("bogus"))
    }

    func testLatchModeTogglesAndIgnoresPadUp() {
        // Latch = TOGGLE: padUp is a no-op (the voice keeps looping); a second
        // padDown (re-tap) stops it. This is desktop's original Loop behavior,
        // now correctly named Latch.
        let controller = makeController()
        controller.setChops([chop(0)], stem: "other", sliceMode: "chord")
        controller.loopLockEnabled = false     // fire immediately, no wait
        controller.playbackMode = .latch

        var releases = 0
        controller.onRelease = { _, _ in releases += 1 }
        let pad = LaunchpadPad(row: 0, col: 0)

        controller.padDown(pad)
        XCTAssertTrue(controller.activePads.contains(pad))
        controller.padUp(pad)                  // no-op for Latch
        XCTAssertTrue(controller.activePads.contains(pad),
                      "Latch holds through finger-lift")
        XCTAssertEqual(releases, 0)
        controller.padDown(pad)                // re-tap toggles OFF
        XCTAssertFalse(controller.activePads.contains(pad))
        XCTAssertEqual(releases, 1, "re-tap releases the latched loop")
    }

    // MARK: - Edit Mode gate

    func testEditAffordanceGate() {
        // Edit OFF = a FILLED pad arms no edit gesture (right-click radial
        // / hover "⋯" on desktop; iOS holdRadialEnabled twin). An EMPTY
        // pad keeps Add Sound in both modes — not a performance path.
        XCTAssertFalse(LaunchpadController.editAffordanceEnabled(
            editing: false, hasContent: true))
        XCTAssertTrue(LaunchpadController.editAffordanceEnabled(
            editing: true, hasContent: true))
        XCTAssertTrue(LaunchpadController.editAffordanceEnabled(
            editing: false, hasContent: false))
        XCTAssertTrue(LaunchpadController.editAffordanceEnabled(
            editing: true, hasContent: false))
    }

    func testLoopNeverQuantizesToSubBarGrid() {
        // LOOPS lock to the BAR grid, never individual beats (web parity):
        // a multi-bar loop snapped to a beat starts mid-bar — out of phase
        // with the song AND every other loop, though each is "on a beat"
        // (the "queued pads start at random times" bug). A sub-bar
        // Quantize control is upgraded to .bar for loop launches; the
        // beats grid stays available to one-shots.
        var now = 0.7
        let controller = LaunchpadController(
            nowProvider: { now }, fetcher: FakeFetcher())
        controller.configure(bundle: bundle(
            beats: [0, 0.6, 1.2, 1.8, 2.4],
            downbeats: [0, 2.4],
            tempoBpm: 100,
            presets: ["harmonic": BundlePreset(
                stem: "other", sliceMode: "chord",
                chops: [chop(0), chop(1)])]
        ))
        controller.playbackMode = .latch     // the quantized launch path
        controller.loopLockEnabled = false
        controller.isTransportPlaying = { true }
        controller.quantize = .quarter

        var fireAt: Double?
        var phase: Double?
        controller.onTrigger = { _, _, t, p in fireAt = t; phase = p }

        // Beat grid would owe 1.2; the loop must wait for the 2.4 downbeat.
        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt ?? -1, 2.4, accuracy: 1e-9)
        // First loop of the era: its boundary IS the anchor, so phase 0
        // (lock-off loops join too since D-029 — willLoop gates the join).
        XCTAssertEqual(phase ?? -1, 0, accuracy: 1e-9)

        // Follow is the zero-latency gate: it IGNORES the Quantize control and
        // fires NOW even while rolling (the beat grid would owe 1.2). This is
        // the deliberate iOS/desktop deviation from web (forceInstantLaunch).
        controller.playbackMode = .follow
        now = 0.7
        controller.padDown(LaunchpadPad(row: 0, col: 1))
        XCTAssertEqual(fireAt ?? -1, 0.7, accuracy: 1e-9)
    }

    func testLoopLengthSecondsSnapsKitWindowToWholeBars() {
        // 100 BPM: bar = 2.4 s, round(8 / 2.4) = 3 bars → 7.2 s.
        XCTAssertEqual(
            loopController(tempoBpm: 100, now: { 0 }).loopLengthSeconds,
            7.2, accuracy: 1e-9)
        // 120 BPM: bar = 2 s, 4 bars → exactly 8 s.
        XCTAssertEqual(
            loopController(tempoBpm: 120, now: { 0 }).loopLengthSeconds,
            8.0, accuracy: 1e-9)
        // No tempo: the raw 8 s kit window.
        XCTAssertEqual(
            loopController(tempoBpm: nil, now: { 0 }).loopLengthSeconds,
            8.0, accuracy: 1e-9)
    }

    // MARK: - Slice-mode switch

    func testLoadChopsSwapsGrid() async {
        let fetcher = FakeFetcher(
            chops: [chop(0, symbol: "Am"), chop(1, symbol: "F")])
        let controller = makeController(fetcher: fetcher)
        controller.configure(bundle: bundle(
            presets: ["harmonic": BundlePreset(
                stem: "other", sliceMode: "chord",
                chops: [chop(0), chop(1), chop(2)])]
        ))
        XCTAssertEqual(controller.assignments.count, 3)

        await controller.loadChops(
            stem: "drums", sliceMode: "beat",
            backend: URL(string: "http://localhost:8000")!
        )
        XCTAssertEqual(controller.assignments.count, 2)
        XCTAssertEqual(controller.stem, "drums")
        XCTAssertEqual(controller.sliceMode, "beat")
        XCTAssertNil(controller.fetchError)
        XCTAssertFalse(controller.isFetching)
    }

    func testLoadChopsErrorKeepsGridAndSurfacesMessage() async {
        struct Boom: LocalizedError {
            var errorDescription: String? { "boom" }
        }
        let fetcher = FakeFetcher(error: Boom())
        let controller = makeController(fetcher: fetcher)
        controller.configure(bundle: bundle(
            presets: ["harmonic": BundlePreset(
                stem: "other", sliceMode: "chord", chops: [chop(0)])]
        ))

        await controller.loadChops(
            stem: "drums", sliceMode: "beat",
            backend: URL(string: "http://localhost:8000")!
        )
        XCTAssertEqual(controller.fetchError, "boom")
        XCTAssertEqual(controller.assignments.count, 1)  // grid untouched
        XCTAssertEqual(controller.stem, "other")
    }

    func testLoadChopsWithoutSessionIsNoop() async {
        let fetcher = FakeFetcher(chops: [chop(0)])
        let controller = makeController(fetcher: fetcher)
        await controller.loadChops(
            stem: "drums", sliceMode: "beat",
            backend: URL(string: "http://localhost:8000")!
        )
        XCTAssertTrue(controller.assignments.isEmpty)
    }

    // MARK: - Chop edits

    private func edits(
        presetKey: String = "harmonic",
        boundary: ChopBoundaryEdit...
    ) -> ChopEdits {
        var edits = ChopEdits(presetKey: presetKey)
        for edit in boundary {
            edits.boundaryEdits[edit.chopIndex] = edit
        }
        return edits
    }

    private func harmonicBundle() -> SongBundle {
        bundle(presets: [
            "harmonic": BundlePreset(
                stem: "other", sliceMode: "chord",
                chops: [
                    chop(0, start: 0, end: 1, symbol: "Am"),
                    chop(1, start: 1, end: 2, symbol: "F"),
                    chop(2, start: 2, end: 3, symbol: "C"),
                ]
            )
        ])
    }

    func testConfigureRecordsPresetKey() {
        let controller = makeController()
        controller.configure(bundle: harmonicBundle())
        XCTAssertEqual(controller.presetKey, "harmonic")
    }

    func testConfigureFallbackRecordsChordPresetKey() {
        let controller = makeController()
        controller.configure(bundle: bundle(presets: [
            "melodic": BundlePreset(
                stem: "vocals", sliceMode: "chord", chops: [chop(0)]),
            "sections": BundlePreset(
                stem: "drums", sliceMode: "section", chops: [chop(0)]),
        ]))
        XCTAssertEqual(controller.presetKey, "melodic")
    }

    func testApplyEditsOverlaysBoundariesKeepingIdx() {
        let controller = makeController()
        controller.configure(bundle: harmonicBundle())

        controller.applyEdits(edits(boundary: ChopBoundaryEdit(
            chopIndex: 1,
            originalStart: 1, originalEnd: 2,
            editedStart: 1.2, editedEnd: 1.8
        )))

        let pad = LaunchpadPad(row: 0, col: 1)
        XCTAssertEqual(controller.assignments[pad]?.chop.idx, 1)
        XCTAssertEqual(controller.assignments[pad]?.chop.startSec, 1.2)
        XCTAssertEqual(controller.assignments[pad]?.chop.endSec, 1.8)
        XCTAssertEqual(controller.assignments[pad]?.chop.chordSymbol, "F")
        // Neighbors untouched.
        XCTAssertEqual(
            controller.assignments[LaunchpadPad(row: 0, col: 0)]?.chop.endSec, 1)
        XCTAssertEqual(
            controller.assignments[LaunchpadPad(row: 0, col: 2)]?.chop.startSec, 2)
    }

    func testApplyEditsNilRestoresBundleBoundaries() {
        let controller = makeController()
        controller.configure(bundle: harmonicBundle())
        controller.applyEdits(edits(boundary: ChopBoundaryEdit(
            chopIndex: 0,
            originalStart: 0, originalEnd: 1,
            editedStart: 0.25, editedEnd: 0.75
        )))

        controller.applyEdits(nil)

        let pad = LaunchpadPad(row: 0, col: 0)
        XCTAssertEqual(controller.assignments[pad]?.chop.startSec, 0)
        XCTAssertEqual(controller.assignments[pad]?.chop.endSec, 1)
        XCTAssertNil(controller.edits)
    }

    func testApplyEditsResolvesFromRawChopsNotCompounding() {
        let controller = makeController()
        controller.configure(bundle: harmonicBundle())
        let overlay = edits(boundary: ChopBoundaryEdit(
            chopIndex: 1,
            originalStart: 1, originalEnd: 2,
            editedStart: 1.2, editedEnd: 1.8
        ))

        controller.applyEdits(overlay)
        controller.applyEdits(overlay)

        let pad = LaunchpadPad(row: 0, col: 1)
        XCTAssertEqual(controller.assignments[pad]?.chop.startSec, 1.2)
        XCTAssertEqual(controller.assignments[pad]?.chop.endSec, 1.8)
    }

    func testSetChopsClearsPresetKeyAndEdits() {
        let controller = makeController()
        controller.configure(bundle: harmonicBundle())
        controller.applyEdits(edits(boundary: ChopBoundaryEdit(
            chopIndex: 0,
            originalStart: 0, originalEnd: 1,
            editedStart: 0.25, editedEnd: 0.75
        )))

        controller.setChops(
            [chop(0, start: 0, end: 1)], stem: "drums", sliceMode: "beat")

        XCTAssertNil(controller.presetKey)
        XCTAssertNil(controller.edits)
        XCTAssertEqual(
            controller.assignments[LaunchpadPad(row: 0, col: 0)]?.chop.startSec, 0)
    }

    // MARK: - Lights

    func testAttachPaintsFullFrameAndPressPulses() {
        let transport = FakeTransport()
        let controller = makeController()
        controller.setChops(
            [chop(0, colorHint: "#FF0000")], stem: "other", sliceMode: "chord")
        controller.attach(transport: transport)

        // Full 64-pad frame: one assigned solid, rest off.
        XCTAssertEqual(transport.lights.count, 64)
        let pad = LaunchpadPad(row: 0, col: 0)
        XCTAssertEqual(transport.lights[pad], .solid(colorHint: 0xFF0000))
        XCTAssertEqual(
            transport.lights[LaunchpadPad(row: 3, col: 3)], .off)

        controller.padDown(pad)
        XCTAssertEqual(transport.lights[pad], .pulse(colorHint: 0xFF0000))
        controller.padUp(pad)
        XCTAssertEqual(transport.lights[pad], .solid(colorHint: 0xFF0000))
    }

    func testTransportPadCallbacksRouteThroughController() {
        let transport = FakeTransport()
        let controller = makeController()
        controller.setChops([chop(0)], stem: "other", sliceMode: "chord")
        controller.attach(transport: transport)

        var fired = 0
        controller.onTrigger = { _, _, _, _ in fired += 1 }
        transport.onPadDown?(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fired, 1)
    }

    // MARK: - Stamped hardware presses (receive-thread clocks)

    private final class StampedFakeTransport: LaunchpadTransport, StampedPadTransport {
        var connectionState: LaunchpadConnectionState { .connected(deviceName: "LP") }
        var onPadDown: ((LaunchpadPad) -> Void)?
        var onPadUp: ((LaunchpadPad) -> Void)?
        var onPadDownStamped: ((LaunchpadPad, Double, UInt64) -> Void)?
        var onPadUpStamped: ((LaunchpadPad, Double, UInt64) -> Void)?
        var lights: [LaunchpadPad: LaunchpadLight] = [:]
        func setLight(_ light: LaunchpadLight, at pad: LaunchpadPad) { lights[pad] = light }
        func setLights(_ frame: [LaunchpadPad: LaunchpadLight]) {
            for (pad, light) in frame { lights[pad] = light }
        }
        func clearLights() { lights.removeAll() }
    }

    /// attach() prefers the stamped seam on transports that have it,
    /// and the press-time song stamp — not the controller clock at
    /// main-queue arrival — becomes the instant fire time. The main
    /// hop lags the press by 10–50 ms under UI load; quantize and
    /// phase joins must not inherit that.
    func testAttachWiresStampedSeamAndPressStampSetsFireTime() {
        let transport = StampedFakeTransport()
        let controller = makeController(now: 5.0)   // main-arrival clock
        controller.setChops([chop(0)], stem: "other", sliceMode: "chord")
        controller.attach(transport: transport)

        XCTAssertNotNil(transport.onPadDownStamped)
        XCTAssertNotNil(transport.onPadUpStamped)
        XCTAssertNil(transport.onPadDown, "stamped seam replaces legacy")

        var fired: [Double] = []
        controller.onTrigger = { _, _, fireAt, _ in fired.append(fireAt) }
        // Press stamped 40 ms before the hop delivered it.
        transport.onPadDownStamped?(LaunchpadPad(row: 0, col: 0), 4.96, 123)
        XCTAssertEqual(fired, [4.96], "instant fire uses the press stamp")

        var released = 0
        controller.onRelease = { _, _ in released += 1 }
        transport.onPadUpStamped?(LaunchpadPad(row: 0, col: 0), 4.99, 456)
        XCTAssertEqual(released, 1)
    }

    // MARK: - Display color (screen ↔ hardware LED parity)

    /// ONE color source for the on-screen tile and the hardware LED.
    /// They diverged: the LEDs painted raw backend colorHints while the
    /// screen painted musical-category colors — the "hardware colors
    /// don't match the screen" bug.
    func testDisplayColorHintPrefersRileyCategoryOverRawHint() {
        let controller = makeController()
        let riley = Chop(
            idx: 0, startSec: 0, endSec: 1, durationSec: 1,
            kind: "chord", colorHint: "#123456", contentType: "rhythm_loop"
        )
        controller.setChops([riley], stem: "other", sliceMode: "chord")
        let pad = LaunchpadPad(row: 0, col: 0)
        let assignment = controller.assignments[pad]!

        XCTAssertEqual(
            controller.displayColorHint(for: assignment, at: pad),
            UInt32(LaunchpadController.PadCategory.rhythm.colorHex),
            "Riley pads show their category accent, not the raw hint"
        )
    }

    func testDisplayColorHintFallsBackToRawHintForLegacyChops() {
        let controller = makeController()
        controller.setChops(
            [chop(0, colorHint: "#123456")], stem: "other", sliceMode: "chord")
        let pad = LaunchpadPad(row: 0, col: 0)
        let assignment = controller.assignments[pad]!
        XCTAssertEqual(controller.displayColorHint(for: assignment, at: pad), 0x123456)
    }

    /// Hardware LEDs paint the SAME category color the screen shows —
    /// through every state transition (idle solid, sounding pulse,
    /// released solid).
    func testHardwareLEDsPaintCategoryColorThroughPressCycle() {
        let transport = FakeTransport()
        let controller = makeController()
        let riley = Chop(
            idx: 0, startSec: 0, endSec: 1, durationSec: 1,
            kind: "chord", colorHint: "#FF0000", contentType: "lead_loop"
        )
        controller.setChops([riley], stem: "other", sliceMode: "chord")
        controller.attach(transport: transport)
        let pad = LaunchpadPad(row: 0, col: 0)
        let lead = UInt32(LaunchpadController.PadCategory.lead.colorHex)

        XCTAssertEqual(transport.lights[pad], .solid(colorHint: lead))
        controller.padDown(pad)
        XCTAssertEqual(transport.lights[pad], .pulse(colorHint: lead))
        controller.padUp(pad)
        XCTAssertEqual(transport.lights[pad], .solid(colorHint: lead))
    }

    /// Borrow pads carry no contentType; hardware must show the
    /// borrow STEM category — the screen-side fix (22bd58b6) alone
    /// left the LEDs painting the manifest's flat blue/amber source
    /// tint ("one color block" on hardware).
    func testBorrowPadsLightByStemCategoryOnHardware() {
        let transport = FakeTransport()
        let controller = makeController()
        controller.attach(transport: transport)
        let mount = LaunchpadController.BorrowMount(
            chop: chop(0, colorHint: "#3B82F6"),   // manifest source tint
            stem: "bass", sourceLabel: "Donor Song", source: .donor
        )
        controller.adoptBorrowAssignments([mount])

        let pad = controller.assignments.keys.first!
        XCTAssertEqual(
            transport.lights[pad],
            .solid(colorHint: UInt32(LaunchpadController.PadCategory.bass.colorHex)),
            "borrow LEDs color by stem category, not the source tint"
        )
    }

    // MARK: - Color hints

    func testParseColorHint() {
        XCTAssertEqual(
            LaunchpadController.parseColorHint("#A1B2C3"), 0xA1B2C3)
        XCTAssertEqual(
            LaunchpadController.parseColorHint("00FF00"), 0x00FF00)
        XCTAssertNil(LaunchpadController.parseColorHint(nil))
        XCTAssertNil(LaunchpadController.parseColorHint(""))
        XCTAssertNil(LaunchpadController.parseColorHint("#XYZ123"))
        XCTAssertNil(LaunchpadController.parseColorHint("#FFF"))
    }

    // MARK: - Borrow pad color-by-stem (guards the "all pads red" regression)

    // A borrow carries only a logical stem (no Riley contentType). The mount
    // once hard-coded stem "drums", so EVERY borrow pad read as drums-red.
    // fillColor now routes through this pure map; pin every branch so a
    // one-color borrow fails CI, not the user's eyes.
    func testBorrowCategoryByStem() {
        XCTAssertEqual(LaunchpadController.borrowCategory(forStem: "drums"), .drums)
        XCTAssertEqual(LaunchpadController.borrowCategory(forStem: "bass"), .bass)
        XCTAssertEqual(LaunchpadController.borrowCategory(forStem: "vocals"), .vocal)
        // "other" and anything unknown/nil fall to chords (harmonic in kit terms).
        XCTAssertEqual(LaunchpadController.borrowCategory(forStem: "other"), .chords)
        XCTAssertEqual(LaunchpadController.borrowCategory(forStem: "guitar"), .chords)
        XCTAssertEqual(LaunchpadController.borrowCategory(forStem: nil), .chords)
    }

    // The distinct-color guarantee itself: a mixed-stem borrow must NOT collapse
    // to a single category (the visible symptom of the bug).
    func testBorrowStemsProduceDistinctColors() {
        let cats: [LaunchpadController.PadCategory] =
            ["drums", "bass", "vocals", "other"]
                .map { LaunchpadController.borrowCategory(forStem: $0) }
        XCTAssertEqual(Set(cats.map(\.colorHex)).count, 4,
                       "four stems must yield four distinct pad colors")
    }
}
