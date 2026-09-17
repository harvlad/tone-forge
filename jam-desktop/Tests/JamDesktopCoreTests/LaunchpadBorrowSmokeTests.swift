// LaunchpadBorrowSmokeTests.swift
//
// Headless end-to-end smoke of the Launchpad surface — the integration
// glue that the per-function unit tests don't exercise on their own, and
// that a desktop XCUITest WOULD cover if jam-desktop had an app/UI-test
// target (it's a pure SwiftPM package with no .xcodeproj, so XCUITest can't
// launch it; see jam-desktop/DECISIONS.md D-020). This runs the real
// LaunchpadController through a borrow adoption and asserts, against a fake
// transport, the properties a human would eyeball in Perform:
//
//   • the grid actually FILLS (borrow pads mount, non-empty), guarding the
//     "blank grid" failure mode;
//   • borrow pads are colored by their real STEM and are NOT all one color
//     (the "all pads red" regression), and carry a source-song label (the
//     data the hold-radial / source line render);
//   • a pad TOGGLES active on tap and OFF on re-tap (loop toggle), and Stop
//     clears every active pad and fires the hard voice-stop (the
//     "pad won't stop" regression).

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class LaunchpadBorrowSmokeTests: XCTestCase {

    private final class FakeTransport: LaunchpadTransport {
        var connectionState: LaunchpadConnectionState { .onScreen }
        var onPadDown: ((LaunchpadPad) -> Void)?
        var onPadUp: ((LaunchpadPad) -> Void)?
        var lights: [LaunchpadPad: LaunchpadLight] = [:]
        func setLight(_ light: LaunchpadLight, at pad: LaunchpadPad) {
            lights[pad] = light
        }
        func setLights(_ frame: [LaunchpadPad: LaunchpadLight]) {
            for (p, l) in frame { lights[p] = l }
        }
        func clearLights() { lights.removeAll() }
    }

    private struct FakeFetcher: LaunchpadChopsFetching {
        func fetchChops(
            baseURL: URL, analysisId: String, stem: String?, sliceMode: String?
        ) async throws -> [Chop] { [] }
    }

    private func borrowChop(_ idx: Int) -> Chop {
        Chop(
            idx: idx, startSec: 0, endSec: 2, durationSec: 2,
            kind: "phrase", sectionLabel: "Loop \(idx)",
            contentType: nil, performanceScore: Double(idx),
            loopable: true, loopScore: 1.0, assetId: "borrowfile:\(idx)")
    }

    /// One initial + one donor pad per core stem, exactly as
    /// SessionController.loadBorrowLoops builds them.
    private func mounts() -> [LaunchpadController.BorrowMount] {
        let stems = ["drums", "bass", "vocals", "other"]
        var out: [LaunchpadController.BorrowMount] = []
        var idx = 0
        for stem in stems {
            out.append(.init(chop: borrowChop(idx), stem: stem,
                             sourceLabel: "This song", source: .initial))
            idx += 1
        }
        for stem in stems {
            out.append(.init(chop: borrowChop(idx), stem: stem,
                             sourceLabel: "Donor song", source: .donor))
            idx += 1
        }
        return out
    }

    private func makeController() -> (LaunchpadController, FakeTransport) {
        let c = LaunchpadController(nowProvider: { 0 }, fetcher: FakeFetcher())
        let t = FakeTransport()
        c.attach(transport: t)
        return (c, t)
    }

    // MARK: - Grid fills + colors read by stem (not one block)

    func testBorrowGridFillsWithDistinctStemColors() {
        let (c, _) = makeController()
        c.playbackMode = .latch      // borrow pads latch (loop until re-tapped)
        c.adoptBorrowAssignments(mounts())

        // Grid is non-empty — the failure a user sees as a blank grid.
        XCTAssertFalse(c.assignments.isEmpty, "borrow grid must mount pads")
        XCTAssertEqual(c.assignments.count, 8)

        // Every mounted pad carries a source-song label (the source line +
        // the hold-radial's identity depend on it).
        for pad in c.assignments.keys {
            XCTAssertNotNil(c.sourceLabel(for: pad),
                            "every borrow pad needs a source-song label")
        }

        // Color-by-stem: the four stems must map to four distinct categories,
        // NOT collapse to one (the "all pads red" regression). Uses the same
        // pure map fillColor now calls.
        let cats = Set(["drums", "bass", "vocals", "other"].map {
            LaunchpadController.borrowCategory(forStem: $0).colorHex
        })
        XCTAssertEqual(cats.count, 4)
    }

    // MARK: - Tap toggles active; Stop clears everything

    func testPadTogglesActiveAndStopClears() {
        let (c, _) = makeController()
        c.playbackMode = .latch            // borrow pads latch (toggle on re-tap)
        c.loopLockEnabled = false          // fire immediately, no quantize wait
        c.adoptBorrowAssignments(mounts())

        let pad = c.assignments.keys.sorted {
            ($0.row, $0.col) < ($1.row, $1.col)
        }.first!

        // Tap → active.
        c.padDown(pad)
        XCTAssertTrue(c.activePads.contains(pad), "tap should activate the pad")

        // Re-tap → stops (latch toggle). This is the "pad won't stop on re-tap"
        // regression guard at the controller level.
        c.padDown(pad)
        XCTAssertFalse(c.activePads.contains(pad),
                       "re-tap should toggle the latched loop off")

        // Re-activate two pads, then Stop clears all + fires hard voice-stop.
        var hardStopped = false
        c.onStopAllVoices = { hardStopped = true }
        let pads = Array(c.assignments.keys.prefix(2))
        pads.forEach { c.padDown($0) }
        XCTAssertFalse(c.activePads.isEmpty)
        c.stopAllPads()
        XCTAssertTrue(c.activePads.isEmpty, "Stop must clear every active pad")
        XCTAssertTrue(hardStopped, "Stop must fire the hard voice-stop")
    }
}
