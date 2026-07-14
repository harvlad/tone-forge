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
