// LaunchpadControlSurfaceTests.swift
//
// The iOS host layer for the D-036 function-button map: actions fire
// on press only, empty-grid/out-of-range guards hold, the unmapped
// sequencer trio stays inert, and the LED frame matches the desktop
// contract (selected-mode bright, loop-lock amber, record pulse,
// layer accents, section blocks). The CC → function table itself is
// pinned in the engine (LaunchpadControlMappingTests).

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
private final class FakeControlLights: ControlButtonLightTransport {
    var frames: [[Int: LaunchpadLight]] = []
    func setControlLights(_ frame: [Int: LaunchpadLight]) {
        frames.append(frame)
    }
    var last: [Int: LaunchpadLight] { frames.last ?? [:] }
}

@MainActor
final class LaunchpadControlSurfaceTests: XCTestCase {

    private var surface: LaunchpadControlSurface!
    private var lights: FakeControlLights!
    /// Flash-end callbacks captured from the injected scheduler.
    private var pendingFlashEnds: [@MainActor () -> Void] = []

    override func setUp() {
        super.setUp()
        surface = LaunchpadControlSurface()
        lights = FakeControlLights()
        pendingFlashEnds = []
        surface.scheduleFlashEnd = { [weak self] _, work in
            self?.pendingFlashEnds.append(work)
        }
    }

    private func press(_ button: LaunchpadProMK3Protocol.ControlButton) {
        surface.handle(button, down: true)
        surface.handle(button, down: false)
    }

    // MARK: - Actions

    func testActionsFireOnPressOnly() {
        var plays = 0
        surface.onPlayPause = { plays += 1 }
        surface.handle(.left(row: 2), down: false)
        XCTAssertEqual(plays, 0)
        surface.handle(.left(row: 2), down: true)
        XCTAssertEqual(plays, 1)
    }

    func testModeSelectMapsToSampleTriggerMode() {
        var selected: [SampleTriggerMode] = []
        surface.onSelectMode = { selected.append($0) }
        press(.left(row: 3))
        press(.left(row: 4))
        press(.left(row: 5))
        XCTAssertEqual(selected, [.oneShot, .follow, .latch])
    }

    func testGridSizeArrows() {
        var sizes: [Int] = []
        surface.onGridSize = { sizes.append($0) }
        press(.top(col: 1))
        press(.top(col: 2))
        XCTAssertEqual(sizes, [16, 64])
    }

    func testInstantGrooveInertOnEmptyGrid() {
        var grooves = 0
        surface.onInstantGroove = { grooves += 1 }
        surface.gridIsEmpty = { true }
        press(.top(col: 5))
        XCTAssertEqual(grooves, 0)
        XCTAssertTrue(pendingFlashEnds.isEmpty)

        surface.gridIsEmpty = { false }
        press(.top(col: 5))
        XCTAssertEqual(grooves, 1)
        XCTAssertEqual(pendingFlashEnds.count, 1)
    }

    func testStopAllFlashesAmberThenReverts() {
        surface.attachLights(lights)
        var stops = 0
        surface.onStopAllPads = { stops += 1 }
        press(.trackControl(col: 8))
        XCTAssertEqual(stops, 1)
        XCTAssertEqual(lights.last[8], .solid(colorHint: 0xFFBF00))

        // Injected flash end → dim amber again.
        pendingFlashEnds.removeFirst()()
        XCTAssertEqual(lights.last[8], .solid(colorHint: 0x33230A))
    }

    func testSectionJumpGuardsOutOfRange() {
        var jumps: [Int] = []
        surface.onSectionJump = { jumps.append($0) }
        surface.sectionCount = { 2 }
        press(.right(row: 7))   // section 0
        press(.right(row: 6))   // section 1
        press(.right(row: 5))   // section 2 — doesn't exist
        XCTAssertEqual(jumps, [0, 1])
    }

    func testLayerToggleGuardsEmptyCategory() {
        var toggles: [Int] = []
        surface.onLayerToggle = { toggles.append($0) }
        surface.layerActivity = { $0 == 1 ? .available : .empty }
        press(.trackControl(col: 2))   // layer 0 — empty, inert
        press(.trackControl(col: 3))   // layer 1 — available
        XCTAssertEqual(toggles, [1])
    }

    func testUnmappedSequencerTrioIsInert() {
        surface.attachLights(lights)
        let before = lights.frames.count
        // Session (panel), pattern select, sequencer play/stop: no iOS
        // sequencer panel — presses must not repaint or crash.
        surface.handle(.top(col: 3), down: true)
        surface.handle(.trackSelect(col: 1), down: true)
        surface.handle(.right(row: 8), down: true)
        XCTAssertEqual(lights.frames.count, before)
    }

    // MARK: - LED frame

    func testFrameTransportAndModeAndSize() {
        surface.isTransportPlaying = { true }
        surface.triggerMode = { .latch }
        surface.padCount = { 64 }
        let frame = surface.controlLightFrame()

        XCTAssertEqual(frame[20], .pulse(colorHint: 0x00FF00))
        XCTAssertEqual(frame[10], .solid(colorHint: 0x400808))
        XCTAssertEqual(frame[30], .solid(colorHint: 0x1E1E1E))
        XCTAssertEqual(frame[40], .solid(colorHint: 0x1E1E1E))
        XCTAssertEqual(frame[50], .solid(colorHint: 0xFFFFFF))
        XCTAssertEqual(frame[91], .solid(colorHint: 0x1E1E1E))
        XCTAssertEqual(frame[92], .solid(colorHint: 0xFFFFFF))
    }

    func testFrameLoopLockAndRecord() {
        surface.isLoopLocked = { true }
        surface.isRecording = { true }
        let frame = surface.controlLightFrame()
        XCTAssertEqual(frame[60], .solid(colorHint: 0xF59E0B))
        XCTAssertEqual(frame[1], .pulse(colorHint: 0xFF0000))

        surface.isLoopLocked = { false }
        surface.isRecording = { false }
        let idle = surface.controlLightFrame()
        XCTAssertEqual(idle[60], .solid(colorHint:
            LaunchpadControlSurface.dimmed(0xF59E0B)))
        XCTAssertEqual(idle[1], .solid(colorHint: 0x400808))
    }

    func testFrameLayersAndSections() {
        surface.layerActivity = { index in
            switch index {
            case 0:  return .active
            case 1:  return .available
            default: return .empty
            }
        }
        surface.layerAccent = { $0 == 0 ? 0xEF4444 : 0x22C55E }
        surface.sectionCount = { 3 }
        surface.activeSectionIndex = { 1 }
        let frame = surface.controlLightFrame()

        XCTAssertEqual(frame[2], .pulse(colorHint: 0xEF4444))
        XCTAssertEqual(frame[3], .solid(colorHint:
            LaunchpadControlSurface.dimmed(0x22C55E)))
        XCTAssertEqual(frame[4], LaunchpadLight.off)

        // Sections top→bottom: CC 79 = block 0, 69 = block 1 (active,
        // pulsing), 59 = block 2, 49 = beyond the song → off.
        XCTAssertEqual(frame[79], .solid(colorHint: 0x404040))
        XCTAssertEqual(frame[69], .pulse(colorHint: 0xFFFFFF))
        XCTAssertEqual(frame[59], .solid(colorHint: 0x404040))
        XCTAssertEqual(frame[49], LaunchpadLight.off)
    }

    func testFrameKeepsUnmappedSequencerButtonsDark() {
        let frame = surface.controlLightFrame()
        XCTAssertEqual(frame[93], LaunchpadLight.off)
        XCTAssertEqual(frame[89], LaunchpadLight.off)
        for cc in 101...108 {
            XCTAssertEqual(frame[cc], LaunchpadLight.off)
        }
    }

    func testAttachLightsRepaints() {
        XCTAssertTrue(lights.frames.isEmpty)
        surface.attachLights(lights)
        XCTAssertEqual(lights.frames.count, 1)
        XCTAssertFalse(lights.last.isEmpty)
    }
}
