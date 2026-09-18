// LaunchpadSectionGateTests.swift
//
// Section gate on the desktop pad surface ("Play only in" — iOS
// SampleScheduler parity, restored by Projects): a trigger while the
// playhead sits in a disallowed section is dropped silently; stopping
// a latched loop is NEVER gated; gaps between sections allow. Also
// pins the surface-setting mutation hook Projects auto-save rides on.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class LaunchpadSectionGateTests: XCTestCase {

    /// Mutable clock the controller reads through nowProvider.
    private final class Clock { var now: Double = 0 }

    private var clock: Clock!
    private var controller: LaunchpadController!
    private var triggers: [Int] = []
    private var releases: [Int] = []

    override func setUp() async throws {
        clock = Clock()
        let clock = self.clock!
        controller = LaunchpadController(nowProvider: { clock.now })
        triggers = []
        releases = []
        controller.onTrigger = { [weak self] _, a, _, _ in
            self?.triggers.append(a.chop.idx)
        }
        controller.onRelease = { [weak self] _, a in
            self?.releases.append(a.chop.idx)
        }
        controller.configure(bundle: bundle())
    }

    private func bundle() -> SongBundle {
        let chops = [
            Chop(idx: 0, startSec: 0, endSec: 1, durationSec: 1, kind: "chord"),
            Chop(idx: 1, startSec: 1, endSec: 2, durationSec: 1, kind: "chord"),
        ]
        return SongBundle(
            bundleVersion: 1,
            analysisId: "a1",
            meta: BundleMeta(
                title: "T", artist: "A", sourceUrl: "",
                durationSec: 120, tempoBpm: 120
            ),
            timeline: BundleTimeline(
                chords: [],
                sections: [
                    SectionEvent(start: 0, end: 10, label: "Verse"),
                    SectionEvent(start: 10, end: 20, label: "Chorus"),
                ],
                beats: [], downbeats: []
            ),
            stems: [],
            presets: ["harmonic": BundlePreset(
                stem: "other", sliceMode: "chord", chops: chops)]
        )
    }

    private var pad: LaunchpadPad { LaunchpadPad(row: 0, col: 0) }

    func testNilGateAllows() {
        controller.padDown(pad)
        XCTAssertEqual(triggers, [0])
    }

    func testAllowlistGatesByCurrentSection() {
        controller.sectionGate = ["Chorus"]
        clock.now = 5          // inside Verse — denied
        controller.padDown(pad)
        XCTAssertTrue(triggers.isEmpty)
        clock.now = 15         // inside Chorus — allowed
        controller.padDown(pad)
        XCTAssertEqual(triggers, [0])
    }

    func testEmptyGateDeniesInsideSectionsButAllowsGaps() {
        controller.sectionGate = []
        clock.now = 5          // inside Verse — deny all
        controller.padDown(pad)
        XCTAssertTrue(triggers.isEmpty)
        clock.now = 25         // past every section — musical gap allows
        controller.padDown(pad)
        XCTAssertEqual(triggers, [0])
    }

    func testLatchToggleOffIsNeverGated() {
        controller.playbackMode = .latch
        clock.now = 15
        controller.sectionGate = ["Chorus"]
        controller.padDown(pad)          // latch on (allowed)
        XCTAssertEqual(triggers, [0])
        clock.now = 5                    // now in a DENIED section
        controller.padDown(pad)          // re-tap = toggle OFF — must work
        XCTAssertEqual(releases, [0])
    }

    func testReplayArmIsGated() {
        controller.sectionGate = ["Chorus"]
        clock.now = 5
        controller.replayArm(0)
        XCTAssertTrue(triggers.isEmpty)
        clock.now = 15
        controller.replayArm(0)
        XCTAssertEqual(triggers, [0])
    }

    func testConfigureClearsGate() {
        controller.sectionGate = ["Chorus"]
        controller.configure(bundle: bundle())
        XCTAssertNil(controller.sectionGate)
    }

    func testSurfaceSettingHookFires() {
        var fired = 0
        controller.onSurfaceSettingChanged = { fired += 1 }
        controller.padCount = 16          // 1
        controller.padCount = 16          // unchanged — no fire
        controller.playbackMode = .latch  // 2
        controller.sectionGate = ["Verse"] // 3
        controller.sectionGate = ["Verse"] // unchanged — no fire
        XCTAssertEqual(fired, 3)
    }
}
