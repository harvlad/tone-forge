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

    func testPadDownQuantizesToNextBeat() {
        var now = 0.0
        let controller = LaunchpadController(
            nowProvider: { now }, fetcher: FakeFetcher())
        controller.configure(bundle: bundle(
            beats: [0, 0.5, 1.0, 1.5],
            presets: ["harmonic": BundlePreset(
                stem: "other", sliceMode: "chord", chops: [chop(0)])]
        ))
        controller.quantize = .quarter
        // Quantize only applies while the transport rolls — a stopped
        // transport fires immediately (mobile parity). This test predates
        // that change and was failing for the wrong reason without it.
        controller.isTransportPlaying = { true }

        var fired: [(PadAssignment, Double)] = []
        controller.onTrigger = { fired.append(($1, $2)) }

        now = 0.7  // between beats, past the 80 ms grace of 0.5
        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fired.count, 1)
        XCTAssertEqual(fired[0].1, 1.0, accuracy: 1e-9)
        XCTAssertTrue(controller.activePads.contains(LaunchpadPad(row: 0, col: 0)))
    }

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
        controller.onTrigger = { fireAt = $2 }

        now = 0.55  // 50 ms past the 0.5 beat — inside the grace window
        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt, 0.55)
    }

    func testQuantizeOffFiresAtPressTime() {
        let controller = makeController(now: 3.21)
        controller.setChops([chop(0)], stem: "other", sliceMode: "chord")

        var fireAt: Double?
        controller.onTrigger = { fireAt = $2 }
        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt, 3.21)
    }

    func testUnassignedPadDoesNotTrigger() {
        let controller = makeController()
        controller.setChops([chop(0)], stem: "other", sliceMode: "chord")

        var fired = 0
        controller.onTrigger = { _, _, _ in fired += 1 }
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
    // audio work: tap = instant, loop+lock+rolling = shared loop-cycle
    // grid, loop+lock+stopped = wall-clock free-run grid, lock-off =
    // bar quantize. Song time comes from nowProvider; the free-run grid
    // runs on the injectable hostNowSeconds host clock.

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

    func testDefaultPlaybackModeIsTapAndFiresImmediately() {
        var now = 1.23
        let controller = loopController(tempoBpm: 120, now: { now })
        XCTAssertEqual(controller.playbackMode, .tap)

        var fireAt: Double?
        controller.onTrigger = { fireAt = $2 }

        // Stopped transport: tap is instant.
        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt, 1.23)
        // Rolling transport, quantize off: still instant.
        controller.isTransportPlaying = { true }
        now = 4.56
        controller.padDown(LaunchpadPad(row: 0, col: 1))
        XCTAssertEqual(fireAt, 4.56)
    }

    func testLoopLockRollingQuantizesToLoopCycleBoundary() {
        // 100 BPM → bar 2.4 s → 3 bars fit 8 s → L = 7.2 s. Using a
        // non-8 L proves the grid is multiples of loopLengthSeconds,
        // not a hardcoded 8 s.
        var now = 3.0
        let controller = loopController(tempoBpm: 100, now: { now })
        controller.playbackMode = .loop
        XCTAssertTrue(controller.loopLockEnabled)  // lock is the default
        controller.isTransportPlaying = { true }

        var fireAt: Double?
        controller.onTrigger = { fireAt = $2 }

        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt ?? -1, 7.2, accuracy: 1e-9)

        // 50 ms past the 7.2 boundary — inside the 0.12 s grace, fires
        // NOW instead of waiting a whole cycle.
        now = 7.25
        controller.padDown(LaunchpadPad(row: 0, col: 1))
        XCTAssertEqual(fireAt ?? -1, 7.25, accuracy: 1e-9)
    }

    func testLoopLockStoppedTransportFreeRunsOnAnchoredCycle() {
        // Transport STOPPED (song clock frozen): loops must not quantize
        // against the dead song grid. First press fires immediately and
        // anchors a wall-clock cycle; later presses queue to that anchor.
        var now = 5.0
        var hostNow = 1000.0
        let controller = loopController(tempoBpm: 120, now: { now })  // L = 8 s
        controller.playbackMode = .loop
        controller.isTransportPlaying = { false }
        controller.hostNowSeconds = { hostNow }

        var fireAt: Double?
        controller.onTrigger = { fireAt = $2 }
        let padA = LaunchpadPad(row: 0, col: 0)
        let padB = LaunchpadPad(row: 0, col: 1)
        let padC = LaunchpadPad(row: 0, col: 2)

        // First loop: instant, anchors the cycle at hostNow = 1000.
        controller.padDown(padA)
        XCTAssertEqual(fireAt ?? -1, 5.0, accuracy: 1e-9)

        // 3 s into the 8 s cycle: queues to the anchor cycle,
        // delay = L - intoCycle = 5 s.
        hostNow = 1003.0
        now = 5.5
        controller.padDown(padB)
        XCTAssertEqual(fireAt ?? -1, 5.5 + 5.0, accuracy: 1e-9)

        // 50 ms after the cycle wraps — inside the 0.08 s grace: NOW.
        hostNow = 1008.05
        now = 6.0
        controller.padDown(padC)
        XCTAssertEqual(fireAt ?? -1, 6.0, accuracy: 1e-9)
    }

    func testFreeRunReanchorsAfterAllPadsReleased() {
        // Releasing ALL pads abandons the free-run grid; the next loop
        // press fires immediately on a FRESH cycle — BY DESIGN (a new
        // jam shouldn't wait on a grid nobody can hear).
        var now = 5.0
        var hostNow = 1000.0
        let controller = loopController(tempoBpm: 120, now: { now })  // L = 8 s
        controller.playbackMode = .loop
        controller.isTransportPlaying = { false }
        controller.hostNowSeconds = { hostNow }

        var fireAt: Double?
        controller.onTrigger = { fireAt = $2 }
        let padA = LaunchpadPad(row: 0, col: 0)
        let padB = LaunchpadPad(row: 0, col: 1)

        controller.padDown(padA)               // anchor at 1000
        controller.padDown(padA)               // loop re-tap toggles it OFF
        XCTAssertTrue(controller.activePads.isEmpty)

        // Mid-old-cycle press: would owe a 4.3 s wait on the stale grid;
        // instead it re-anchors and fires immediately.
        hostNow = 1003.7
        now = 6.0
        fireAt = nil
        controller.padDown(padA)
        XCTAssertEqual(fireAt ?? -1, 6.0, accuracy: 1e-9)

        // And the NEW anchor governs: 2 s into the fresh cycle → 6 s wait
        // (the stale 1000-anchor would have owed 8 - 5.7 = 2.3 s).
        hostNow = 1005.7
        now = 6.3
        controller.padDown(padB)
        XCTAssertEqual(fireAt ?? -1, 6.3 + 6.0, accuracy: 1e-9)
    }

    func testLoopLockOffFallsBackToBarQuantize() {
        // Lock off + quantize .off in loop mode = the bar-quantize
        // fallback (single hits still land on a downbeat).
        var now = 0.7
        let controller = loopController(tempoBpm: 100, now: { now })
        controller.playbackMode = .loop
        controller.loopLockEnabled = false
        controller.isTransportPlaying = { true }
        controller.quantize = .off

        var fireAt: Double?
        controller.onTrigger = { fireAt = $2 }

        // Downbeats [0, 2, 4]: 0.7 (past the 0.08 s grace of 0) → 2.0,
        // NOT the 7.2 s loop-cycle boundary.
        controller.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fireAt ?? -1, 2.0, accuracy: 1e-9)

        // An explicit Quantize control wins over the bar fallback.
        now = 2.7
        controller.quantize = .phrase
        controller.padDown(LaunchpadPad(row: 0, col: 1))
        XCTAssertEqual(fireAt ?? -1, 2.7, accuracy: 1e-9)  // no sections → t
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
        controller.onTrigger = { _, _, _ in fired += 1 }
        transport.onPadDown?(LaunchpadPad(row: 0, col: 0))
        XCTAssertEqual(fired, 1)
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
}
