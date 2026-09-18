// LaunchpadControlSurfaceTests.swift
//
// Pins the Launchpad Pro MK3 function-button map (D-036): the
// physical assignment table, each mapped button's action, and the LED
// state contract — headless, over the pure Core surface. The
// transport-level control-LED path (PadIndex gate bypass, cache,
// reconnect repaint) is pinned in USBLaunchpadTransportTests.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class LaunchpadControlSurfaceTests: XCTestCase {

    private typealias Button = LaunchpadProMK3Protocol.ControlButton
    private typealias Function = LaunchpadControlSurface.HardwareFunction

    // MARK: - Fixtures

    private final class FakeControlLights: ControlButtonLightTransport {
        var lights: [Int: LaunchpadLight] = [:]
        var frameCount = 0
        func setControlLights(_ frame: [Int: LaunchpadLight]) {
            frameCount += 1
            for (cc, light) in frame { lights[cc] = light }
        }
    }

    private var launchpad: LaunchpadController!
    private var arrangement: ArrangementController!
    private var surface: LaunchpadControlSurface!
    private var defaults: UserDefaults!
    private var suite: String!

    override func setUp() {
        super.setUp()
        suite = "control-surface-tests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)
        launchpad = LaunchpadController(nowProvider: { 0 })
        arrangement = ArrangementController(
            launchpad: launchpad,
            store: ArrangementStore(defaults: defaults)
        )
        surface = LaunchpadControlSurface(
            launchpad: launchpad, arrangement: arrangement
        )
        // Deterministic flashes: hold the end until the test asks.
        surface.scheduleFlashEnd = { [weak self] _, work in
            self?.pendingFlashEnds.append(work)
        }
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        pendingFlashEnds = []
        super.tearDown()
    }

    private typealias FlashEnd = @MainActor () -> Void
    private var pendingFlashEnds: [FlashEnd] = []
    private func endFlashes() {
        let ends = pendingFlashEnds
        pendingFlashEnds = []
        for end in ends { end() }
    }

    private func chop(
        _ idx: Int, category: String? = nil, score: Double = 0.5
    ) -> Chop {
        Chop(
            idx: idx, startSec: Double(idx), endSec: Double(idx) + 1,
            durationSec: 1, kind: "phrase",
            performanceScore: score, loopScore: score,
            category: category
        )
    }

    /// One pad per Layers-row category, best-first scores.
    private func mountCategoryKit() {
        launchpad.adoptAssignments([
            (chop: chop(0, category: "DRUMS"), stem: "drums"),
            (chop: chop(1, category: "BASS"), stem: "bass"),
            (chop: chop(2, category: "CHORDS"), stem: "other"),
            (chop: chop(3, category: "SYNTH"), stem: "synth"),
            (chop: chop(4, category: "LEAD"), stem: "other"),
            (chop: chop(5, category: "TEXTURE"), stem: "other"),
        ])
    }

    private func loadSections(_ count: Int) {
        arrangement.loadSong(
            analysisId: "",
            sections: (0..<count).map {
                ArrangementSectionInput(
                    type: "Section \($0)",
                    start: Double($0) * 10,
                    end: Double($0 + 1) * 10
                )
            }
        )
    }

    // MARK: - The assignment table

    /// The full approved map, CC by CC — a change here is a change to
    /// the physical instrument and must be a conscious decision
    /// (update D-036 with it).
    func testApprovedAssignmentTable() {
        let expectations: [(Int, Function?)] = [
            (10, .globalStop),                 // ○ Record/Capture MIDI
            (20, .playPause),                  // ▷ Play
            (30, .selectMode(.oneShot)),       // Fixed Length
            (40, .selectMode(.follow)),        // Quantise
            (50, .selectMode(.latch)),         // Duplicate
            (60, .loopLockToggle),             // Clear
            (70, nil), (80, nil),              // ▼ ▲ unmapped
            (90, nil),                         // Shift reserved
            (91, .gridSize(16)),               // ◄
            (92, .gridSize(64)),               // ►
            (93, .sequencerPanelToggle),       // Session
            (94, nil),                         // Note
            (95, .instantGroove),              // Chord
            (96, nil), (97, nil), (98, nil),   // Custom/Sequencer/Projects
            (99, nil),                         // logo
            (1, .recordToggle),                // Record Arm
            (2, .layerToggle(.drums)),         // Mute
            (3, .layerToggle(.bass)),          // Solo
            (4, .layerToggle(.chords)),        // Volume
            (5, .layerToggle(.synth)),         // Pan
            (6, .layerToggle(.lead)),          // Sends
            (7, .layerToggle(.texture)),       // Device
            (8, .stopAllPads),                 // Stop Clip
            (101, .patternSelect(0)), (108, .patternSelect(7)),
            (89, .sequencerPlayStop),          // > top scene arrow
            (79, .sectionJump(0)), (69, .sectionJump(1)),
            (59, .sectionJump(2)), (49, .sectionJump(3)),
            (39, .sectionJump(4)), (29, .sectionJump(5)),
            (19, .sectionJump(6)),
        ]
        for (cc, expected) in expectations {
            guard let button = LaunchpadProMK3Protocol.controlButton(
                forCC: UInt8(cc)) else {
                XCTFail("CC \(cc) did not parse to a control button")
                continue
            }
            XCTAssertEqual(
                LaunchpadControlSurface.function(for: button), expected,
                "CC \(cc) assignment drifted"
            )
            XCTAssertEqual(
                LaunchpadControlSurface.cc(for: button), cc,
                "CC \(cc) inverse mapping drifted"
            )
        }
    }

    // MARK: - Tier 1 actions

    func testPlayPauseAndGlobalStopRouteToHost() {
        var plays = 0
        var stops = 0
        surface.onPlayPause = { plays += 1 }
        surface.onGlobalStop = { stops += 1 }

        surface.handle(.left(row: 2), down: true)
        surface.handle(.left(row: 2), down: false)   // release: no-op
        surface.handle(.left(row: 1), down: true)

        XCTAssertEqual(plays, 1)
        XCTAssertEqual(stops, 1)
    }

    func testModeSelectSetsPlaybackModeAndLightsSelection() {
        surface.handle(.left(row: 3), down: true)
        XCTAssertEqual(launchpad.playbackMode, .oneShot)
        surface.handle(.left(row: 5), down: true)
        XCTAssertEqual(launchpad.playbackMode, .latch)
        surface.handle(.left(row: 4), down: true)
        XCTAssertEqual(launchpad.playbackMode, .follow)

        let frame = surface.controlLightFrame()
        XCTAssertEqual(frame[40], .solid(colorHint: 0xFFFFFF), "selected lit")
        XCTAssertEqual(frame[30], .solid(colorHint: 0x1E1E1E), "others dim")
        XCTAssertEqual(frame[50], .solid(colorHint: 0x1E1E1E))
    }

    func testRecordArmRoutesToHostAndShowsStateLEDs() {
        var toggles = 0
        var state = LaunchpadControlSurface.RecordState.idle
        surface.onRecordToggle = { toggles += 1 }
        surface.recordState = { state }

        surface.handle(.trackControl(col: 1), down: true)
        XCTAssertEqual(toggles, 1)

        XCTAssertEqual(surface.controlLightFrame()[1],
                       .solid(colorHint: 0x400808), "idle: dim red")
        state = .armed
        XCTAssertEqual(surface.controlLightFrame()[1],
                       .solid(colorHint: 0xFF8800), "armed: orange")
        state = .recording
        XCTAssertEqual(surface.controlLightFrame()[1],
                       .pulse(colorHint: 0xFF0000), "recording: red pulse")
    }

    func testStopClipStopsAllPadsAndFlashesAmber() {
        mountCategoryKit()
        launchpad.playbackMode = .latch
        launchpad.padDown(LaunchpadPad(row: 0, col: 0))
        XCTAssertFalse(launchpad.activePads.isEmpty)

        surface.handle(.trackControl(col: 8), down: true)
        XCTAssertTrue(launchpad.activePads.isEmpty, "Stop Clip silences pads")
        XCTAssertEqual(surface.controlLightFrame()[8],
                       .solid(colorHint: 0xFFBF00), "amber flash on press")

        endFlashes()
        XCTAssertEqual(surface.controlLightFrame()[8],
                       .solid(colorHint: 0x33230A), "flash reverts to idle")
    }

    func testSceneButtonsJumpToBlockStartAndLightState() {
        loadSections(3)
        var seeks: [Double] = []
        surface.onSectionJump = { seeks.append($0) }

        surface.handle(.right(row: 7), down: true)   // CC79 = block 0
        surface.handle(.right(row: 5), down: true)   // CC59 = block 2
        surface.handle(.right(row: 4), down: true)   // CC49 = block 3: absent
        XCTAssertEqual(seeks, [0, 20], "existing blocks seek; absent no-op")

        // LED: existing blocks lit, absent dark; the active block
        // (playhead inside it) pulses.
        arrangement.tick(time: 12, isPlaying: true)   // inside block 1
        let frame = surface.controlLightFrame()
        XCTAssertEqual(frame[79], .solid(colorHint: 0x404040))
        XCTAssertEqual(frame[69], .pulse(colorHint: 0xFFFFFF), "active pulses")
        XCTAssertEqual(frame[59], .solid(colorHint: 0x404040))
        XCTAssertEqual(frame[49], .off)
        XCTAssertEqual(frame[19], .off)
    }

    func testArrowsSelectGridSizeAndLightActiveSize() {
        XCTAssertEqual(launchpad.padCount, 64)
        surface.handle(.top(col: 1), down: true)     // ◄ = 16
        XCTAssertEqual(launchpad.padCount, 16)
        var frame = surface.controlLightFrame()
        XCTAssertEqual(frame[91], .solid(colorHint: 0xFFFFFF))
        XCTAssertEqual(frame[92], .solid(colorHint: 0x1E1E1E))

        surface.handle(.top(col: 2), down: true)     // ► = 64
        XCTAssertEqual(launchpad.padCount, 64)
        frame = surface.controlLightFrame()
        XCTAssertEqual(frame[91], .solid(colorHint: 0x1E1E1E))
        XCTAssertEqual(frame[92], .solid(colorHint: 0xFFFFFF))
    }

    func testSessionButtonTogglesSequencerPanel() {
        var open = false
        surface.onSequencerPanelToggle = { open.toggle() }
        surface.isSequencerPanelOpen = { open }

        surface.handle(.top(col: 3), down: true)
        XCTAssertTrue(open)
        XCTAssertEqual(surface.controlLightFrame()[93],
                       .solid(colorHint: 0xFFFFFF))
        surface.handle(.top(col: 3), down: true)
        XCTAssertFalse(open)
        XCTAssertEqual(surface.controlLightFrame()[93],
                       .solid(colorHint: 0x1E1E1E))
    }

    // MARK: - Tier 2 actions

    func testClearButtonTogglesLoopLock() {
        XCTAssertTrue(launchpad.loopLockEnabled)
        surface.handle(.left(row: 6), down: true)
        XCTAssertFalse(launchpad.loopLockEnabled)
        XCTAssertEqual(surface.controlLightFrame()[60],
                       .solid(colorHint: LaunchpadControlSurface.dimmed(0xF59E0B)))
        surface.handle(.left(row: 6), down: true)
        XCTAssertTrue(launchpad.loopLockEnabled)
        XCTAssertEqual(surface.controlLightFrame()[60],
                       .solid(colorHint: 0xF59E0B))
    }

    func testChordButtonFiresInstantGroove() {
        mountCategoryKit()
        surface.handle(.top(col: 5), down: true)
        // Instant Groove latches the best pad per core category.
        XCTAssertEqual(launchpad.playbackMode, .latch)
        XCTAssertFalse(launchpad.activePads.isEmpty)
        XCTAssertNotNil(launchpad.activeLayer(.drums))
        XCTAssertNotNil(launchpad.activeLayer(.bass))
    }

    /// Empty grid = the on-screen Groove button is disabled; the
    /// hardware Chord press must match — in particular it must NOT
    /// latch playbackMode (instantGroove's first side effect) when
    /// there's nothing to play, and must not flash.
    func testChordButtonIsInertOnEmptyGrid() {
        XCTAssertTrue(launchpad.assignments.isEmpty)
        XCTAssertEqual(launchpad.playbackMode, .follow)

        surface.handle(.top(col: 5), down: true)

        XCTAssertEqual(launchpad.playbackMode, .follow, "no mode flip")
        XCTAssertTrue(launchpad.activePads.isEmpty)
        XCTAssertTrue(pendingFlashEnds.isEmpty, "no press flash scheduled")
        XCTAssertEqual(surface.controlLightFrame()[95],
                       .solid(colorHint: 0x33230A), "LED stays idle amber")
    }

    func testTrackControlsToggleCategoryLayersWithCategoryLEDs() {
        mountCategoryKit()

        surface.handle(.trackControl(col: 2), down: true)   // drums on
        XCTAssertNotNil(launchpad.activeLayer(.drums))
        XCTAssertEqual(surface.controlLightFrame()[2],
                       .pulse(colorHint: 0xEF4444), "active layer pulses")

        surface.handle(.trackControl(col: 2), down: true)   // drums off
        XCTAssertNil(launchpad.activeLayer(.drums))
        XCTAssertEqual(surface.controlLightFrame()[2],
                       .solid(colorHint: LaunchpadControlSurface.dimmed(0xEF4444)),
                       "available layer dims")

        // vocal isn't on the physical row (6 free cells) — and an
        // empty category's cell goes dark.
        launchpad.setChops([], stem: nil, sliceMode: nil)
        XCTAssertEqual(surface.controlLightFrame()[2], .off)
    }

    func testTrackSelectChoosesPatternWithStateLEDs() {
        let a = UUID(), b = UUID()
        var selected: [Int] = []
        var current: UUID? = a
        var playing = false
        surface.patternIds = { [a, b] }
        surface.currentPatternId = { current }
        surface.isSequencerPlaying = { playing }
        surface.onPatternSelect = { selected.append($0) }

        surface.handle(.trackSelect(col: 2), down: true)
        surface.handle(.trackSelect(col: 5), down: true)   // no pattern 4
        XCTAssertEqual(selected, [1], "only stored patterns select")

        var frame = surface.controlLightFrame()
        XCTAssertEqual(frame[101], .solid(colorHint: 0xFFFFFF), "current bright")
        XCTAssertEqual(frame[102], .solid(colorHint: 0x202020), "stored dim")
        XCTAssertEqual(frame[103], .off)

        current = b
        playing = true
        frame = surface.controlLightFrame()
        XCTAssertEqual(frame[102], .pulse(colorHint: 0xFFFFFF),
                       "current + running pulses")
    }

    func testTopSceneArrowTogglesSequencerPlayback() {
        var toggles = 0
        var playing = false
        surface.onSequencerPlayStop = { toggles += 1; playing.toggle() }
        surface.isSequencerPlaying = { playing }

        surface.handle(.right(row: 8), down: true)
        XCTAssertEqual(toggles, 1)
        XCTAssertEqual(surface.controlLightFrame()[89],
                       .pulse(colorHint: 0x00FF00))
        surface.handle(.right(row: 8), down: true)
        XCTAssertEqual(surface.controlLightFrame()[89],
                       .solid(colorHint: 0x0A280A))
    }

    // MARK: - Reserved / unmapped

    func testReservedAndUnmappedButtonsDoNothing() {
        mountCategoryKit()
        var hostCalls = 0
        surface.onPlayPause = { hostCalls += 1 }
        surface.onGlobalStop = { hostCalls += 1 }
        surface.onRecordToggle = { hostCalls += 1 }
        surface.onSequencerPanelToggle = { hostCalls += 1 }
        surface.onSequencerPlayStop = { hostCalls += 1 }
        surface.onSectionJump = { _ in hostCalls += 1 }
        surface.onPatternSelect = { _ in hostCalls += 1 }

        let before = (launchpad.playbackMode, launchpad.padCount,
                      launchpad.loopLockEnabled, launchpad.activePads)
        for button: Button in [
            .shift, .logo,
            .left(row: 7), .left(row: 8),        // ▼ ▲
            .top(col: 4), .top(col: 6),          // Note, Custom
            .top(col: 7), .top(col: 8),          // Sequencer, Projects
        ] {
            surface.handle(button, down: true)
            surface.handle(button, down: false)
        }
        XCTAssertEqual(hostCalls, 0)
        XCTAssertEqual(before.0, launchpad.playbackMode)
        XCTAssertEqual(before.1, launchpad.padCount)
        XCTAssertEqual(before.2, launchpad.loopLockEnabled)
        XCTAssertEqual(before.3, launchpad.activePads)
    }

    // MARK: - LED frame shape

    /// The control frame must never address the grid: every key is a
    /// function-button CC, none a valid PadIndex — the separation that
    /// keeps the two LED surfaces from fighting.
    func testControlFrameNeverAddressesGridPads() {
        mountCategoryKit()
        loadSections(4)
        for cc in surface.controlLightFrame().keys {
            XCTAssertFalse(PadIndex(cc).isValid,
                           "CC \(cc) collides with a grid pad address")
        }
    }

    /// A repaint pushes the full frame through the transport seam
    /// (diffing is the transport cache's job).
    func testAttachAndRepaintPushFrames() {
        let lights = FakeControlLights()
        surface.attachLights(lights)
        XCTAssertEqual(lights.frameCount, 1, "attach paints immediately")
        XCTAssertEqual(lights.lights[20], .solid(colorHint: 0x0A280A))

        var playing = false
        surface.isTransportPlaying = { playing }
        playing = true
        surface.repaintControls()
        XCTAssertEqual(lights.lights[20], .pulse(colorHint: 0x00FF00))
    }
}
