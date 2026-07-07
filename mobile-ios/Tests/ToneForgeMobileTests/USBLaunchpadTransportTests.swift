// USBLaunchpadTransportTests.swift
//
// Hardware transport behaviour over FakeMIDIInterface: hot-plug, mode
// SysEx, vel-0 release, pre-hop event stamps, LED diffing/batching,
// suspend/resume, and the underpower heuristic.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class USBLaunchpadTransportTests: XCTestCase {

    private var midi: FakeMIDIInterface!
    private var events: [ContributionEvent] = []
    private var now: Date = Date(timeIntervalSince1970: 1_000_000)

    override func setUp() {
        super.setUp()
        midi = FakeMIDIInterface()
        events = []
        now = Date(timeIntervalSince1970: 1_000_000)
    }

    private func makeTransport() -> USBLaunchpadTransport {
        let transport = USBLaunchpadTransport(
            midi: midi,
            nowProvider: { (song: 42.5, host: 999) },
            dateProvider: { self.now }
        )
        transport.onContribution = { [weak self] in self?.events.append($0) }
        return transport
    }

    /// The transport's receive handler hops to main via
    /// DispatchQueue.main.async; drain that queue before asserting.
    private func drainMainQueue() {
        let exp = expectation(description: "main queue drained")
        DispatchQueue.main.async { exp.fulfill() }
        wait(for: [exp], timeout: 1.0)
    }

    // MARK: - Hot-plug

    func testStartsNotConnectedWithoutDevice() {
        let transport = makeTransport()
        XCTAssertEqual(transport.connectionState, .notConnected)
        XCTAssertTrue(midi.sent.isEmpty)
    }

    func testPlugInConnectsSelectsMIDIPortAndEntersProgrammerMode() {
        let transport = makeTransport()
        midi.plugInLaunchpad()

        XCTAssertEqual(
            transport.connectionState,
            .connected(deviceName: "Launchpad Pro MK3")
        )
        // Input bound to the MIDI interface, not DAW/DIN.
        XCTAssertEqual(midi.connectedInputs, [FakeMIDIInterface.launchpadMIDI])
        // First send = Programmer Mode select.
        XCTAssertEqual(midi.sent.first?.sysex, LaunchpadProMK3Protocol.enterProgrammerMode)
        XCTAssertEqual(midi.sent.first?.endpoint, FakeMIDIInterface.launchpadMIDI)
        // Then the full 64-pad redraw as ONE SysEx message:
        // 6 header + 1 command + 64×5 RGB specs + F7 = 328 bytes.
        XCTAssertEqual(midi.sent.count, 2)
        XCTAssertEqual(midi.sent[1].sysex.count, 328)
    }

    func testDAWAndDINOnlyPortsDoNotConnect() {
        let transport = makeTransport()
        midi.fakeSources = [FakeMIDIInterface.launchpadDAW, FakeMIDIInterface.launchpadDIN]
        midi.fakeDestinations = midi.fakeSources
        midi.onSetupChanged?()

        XCTAssertEqual(transport.connectionState, .notConnected)
        XCTAssertTrue(midi.connectedInputs.isEmpty)
    }

    func testUnplugDisconnects() {
        let transport = makeTransport()
        midi.plugInLaunchpad()
        midi.unplugLaunchpad()

        XCTAssertEqual(transport.connectionState, .notConnected)
        XCTAssertTrue(midi.connectedInputs.isEmpty)
    }

    // MARK: - Pad input

    func testNoteOnPublishesPadDownWithPrehopStamps() {
        let transport = makeTransport()
        var downs: [LaunchpadPad] = []
        transport.onPadDown = { downs.append($0) }
        midi.plugInLaunchpad()

        // Bottom-left pad, note 11, full velocity, driver stamp 12345.
        midi.receive([.noteOn(channel: 0, note: 11, velocity: 127)], hostTime: 12345)
        drainMainQueue()

        XCTAssertEqual(events.count, 1)
        XCTAssertEqual(events[0].source, .launchpad)
        XCTAssertEqual(events[0].kind, .padDown(row: 1, col: 1))
        XCTAssertEqual(events[0].velocity, 1.0)
        XCTAssertEqual(events[0].timestamp, 42.5)          // nowProvider song-seconds
        XCTAssertEqual(events[0].hostTime, 12345)          // packet stamp wins
        XCTAssertFalse(events[0].isReplay)
        // Legacy callback converts to LaunchpadPad (row 0 = top).
        XCTAssertEqual(downs, [LaunchpadPad(row: 7, col: 0)])
    }

    func testVelocityScalesAndZeroPacketStampFallsBack() {
        let transport = makeTransport()
        midi.plugInLaunchpad()

        midi.receive([.noteOn(channel: 0, note: 88, velocity: 64)], hostTime: 0)
        drainMainQueue()

        XCTAssertEqual(events.count, 1)
        XCTAssertEqual(events[0].kind, .padDown(row: 8, col: 8))
        XCTAssertEqual(events[0].velocity, 64.0 / 127.0, accuracy: 1e-9)
        XCTAssertEqual(events[0].hostTime, 999)            // nowProvider host fallback
        _ = transport
    }

    func testVelocityZeroAndNoteOffBothRelease() {
        let transport = makeTransport()
        var ups: [LaunchpadPad] = []
        transport.onPadUp = { ups.append($0) }
        midi.plugInLaunchpad()

        // The device sends Note On velocity 0 for release (PDF p.6);
        // a real Note Off must behave identically.
        midi.receive([
            .noteOn(channel: 0, note: 45, velocity: 100),
            .noteOn(channel: 0, note: 45, velocity: 0),
            .noteOff(channel: 0, note: 45, velocity: 64),
        ])
        drainMainQueue()

        XCTAssertEqual(events.map(\.kind), [
            .padDown(row: 4, col: 5),
            .padUp(row: 4, col: 5),
            .padUp(row: 4, col: 5),
        ])
        XCTAssertEqual(ups.count, 2)
    }

    func testNonGridNotesAndOtherChannelsIgnored() {
        let transport = makeTransport()
        midi.plugInLaunchpad()

        midi.receive([
            .noteOn(channel: 1, note: 11, velocity: 127),  // wrong channel
            .noteOn(channel: 0, note: 9, velocity: 127),   // not a grid note
            .noteOn(channel: 0, note: 90, velocity: 127),  // not a grid note
        ])
        drainMainQueue()

        XCTAssertTrue(events.isEmpty)
        _ = transport
    }

    func testControlButtonsForward() {
        let transport = makeTransport()
        var buttons: [(LaunchpadProMK3Protocol.ControlButton, Bool)] = []
        transport.onControlButton = { buttons.append(($0, $1)) }
        midi.plugInLaunchpad()

        midi.receive([
            .controlChange(channel: 0, controller: 90, value: 127),
            .controlChange(channel: 0, controller: 90, value: 0),
            .controlChange(channel: 0, controller: 91, value: 127),
        ])
        drainMainQueue()

        XCTAssertEqual(buttons.count, 3)
        XCTAssertEqual(buttons[0].0, .shift)
        XCTAssertTrue(buttons[0].1)
        XCTAssertEqual(buttons[1].0, .shift)
        XCTAssertFalse(buttons[1].1)
        XCTAssertEqual(buttons[2].0, .top(col: 1))
        XCTAssertTrue(events.isEmpty)   // CCs never become pad events
    }

    // MARK: - LEDs

    func testLightFrameIsDiffedAndBatched() {
        let transport = makeTransport()
        midi.plugInLaunchpad()
        midi.sent.removeAll()

        let a = LaunchpadPad(row: 0, col: 0)   // top-left → PadIndex 81
        let b = LaunchpadPad(row: 7, col: 7)   // bottom-right → PadIndex 18
        transport.setLights([
            a: .solid(colorHint: 0xFF8800),
            b: .solid(colorHint: 0x00FF00),
        ])
        // Two changed pads → ONE batched SysEx (7 + 2×5 + 1 bytes).
        XCTAssertEqual(midi.sent.count, 1)
        XCTAssertEqual(midi.sent[0].sysex.count, 18)

        // Unchanged frame → nothing sent.
        transport.setLights([a: .solid(colorHint: 0xFF8800)])
        XCTAssertEqual(midi.sent.count, 1)

        // One pad changes → one more message with only that pad.
        transport.setLight(.off, at: a)
        XCTAssertEqual(midi.sent.count, 2)
        XCTAssertEqual(
            midi.sent[1].sysex,
            LaunchpadProMK3Protocol.sysExHeader + [0x03, 0x03, 81, 0, 0, 0, 0xF7]
        )
    }

    func testPulseMapsToNearestPaletteEntry() {
        let transport = makeTransport()
        midi.plugInLaunchpad()
        midi.sent.removeAll()

        transport.setLight(
            .pulse(colorHint: 0x00FF00),
            at: LaunchpadPad(row: 7, col: 0)   // bottom-left → PadIndex 11
        )
        XCTAssertEqual(
            midi.sent.map(\.sysex),
            [LaunchpadProMK3Protocol.ledPulse(
                pad: PadIndex.at(row: 1, col: 1),
                palette: LaunchpadProMK3Protocol.paletteGreen
            )]
        )
    }

    func testRedrawOnConnectReplaysCachedFrame() {
        let transport = makeTransport()
        // Light a pad while unplugged: cached, nothing sent.
        transport.setLight(
            .solid(colorHint: 0xFF0000), at: LaunchpadPad(row: 0, col: 0)
        )
        XCTAssertTrue(midi.sent.isEmpty)

        midi.plugInLaunchpad()
        // Programmer mode + one full redraw containing the cached red
        // pad (PadIndex 81 → RGB 0x7F 0 0 after the >>1 shift).
        XCTAssertEqual(midi.sent.count, 2)
        let redraw = midi.sent[1].sysex
        XCTAssertEqual(redraw.count, 328)
        let padSpec: [UInt8] = [0x03, 81, 0x7F, 0x00, 0x00]
        XCTAssertTrue(redraw.indices.dropLast(4).contains { i in
            Array(redraw[i..<i + 5]) == padSpec
        })
    }

    // MARK: - Suspend / resume (lifecycle)

    func testSuspendSendsLiveModeAndResumeRestores() {
        let transport = makeTransport()
        midi.plugInLaunchpad()
        midi.sent.removeAll()

        transport.suspend()
        XCTAssertEqual(midi.sent.map(\.sysex), [LaunchpadProMK3Protocol.enterLiveMode])

        // LED writes while suspended are cached, not sent.
        transport.setLight(
            .solid(colorHint: 0x0000FF), at: LaunchpadPad(row: 7, col: 7)
        )
        XCTAssertEqual(midi.sent.count, 1)

        transport.resume()
        XCTAssertEqual(midi.sent[1].sysex, LaunchpadProMK3Protocol.enterProgrammerMode)
        XCTAssertEqual(midi.sent[2].sysex.count, 328)   // full resync
        // The suspended-time blue pad (PadIndex 18) is in the redraw.
        let padSpec: [UInt8] = [0x03, 18, 0x00, 0x00, 0x7F]
        let redraw = midi.sent[2].sysex
        XCTAssertTrue(redraw.indices.dropLast(4).contains { i in
            Array(redraw[i..<i + 5]) == padSpec
        })
    }

    // MARK: - Underpower heuristic

    func testConnectionFlappingRaisesUnderpower() {
        let transport = makeTransport()
        midi.plugInLaunchpad()                 // flap 1
        XCTAssertFalse(transport.underpowerSuspected)

        now = now.addingTimeInterval(2)
        midi.unplugLaunchpad()                 // flap 2
        XCTAssertFalse(transport.underpowerSuspected)

        now = now.addingTimeInterval(2)
        midi.plugInLaunchpad()                 // flap 3 inside 10 s
        XCTAssertTrue(transport.underpowerSuspected)
    }

    func testSlowReplugDoesNotRaiseUnderpower() {
        let transport = makeTransport()
        midi.plugInLaunchpad()
        now = now.addingTimeInterval(60)
        midi.unplugLaunchpad()
        now = now.addingTimeInterval(60)
        midi.plugInLaunchpad()
        XCTAssertFalse(transport.underpowerSuspected)
    }

    // MARK: - AppState LED mirroring

    func testLaunchpadFrameConversionFromPadVisuals() {
        var visuals = [PadVisual](repeating: .off, count: 64)
        visuals[0] = PadVisual(colorHint: 0xFF0000, isBright: true)    // row 1, col 1 (bottom-left)
        visuals[63] = PadVisual(colorHint: 0x008800, isBright: false)  // row 8, col 8 (top-right)

        let frame = AppState.launchpadFrame(from: visuals)

        XCTAssertEqual(frame.count, 64)
        // Bright pads keep the exact hint; PadIndex row 1 = Launchpad row 7.
        XCTAssertEqual(frame[LaunchpadPad(row: 7, col: 0)], .solid(colorHint: 0xFF0000))
        // Dim pads scale ×0.4: 0x88 (136) → 54 (0x36).
        XCTAssertEqual(frame[LaunchpadPad(row: 0, col: 7)], .solid(colorHint: 0x003600))
        // colorHint 0 → off.
        XCTAssertEqual(frame[LaunchpadPad(row: 0, col: 0)], LaunchpadLight.off)
        // Wrong-sized input is rejected.
        XCTAssertTrue(AppState.launchpadFrame(from: []).isEmpty)
    }

    func testSendFailureWhileConnectedRaisesUnderpower() {
        let transport = makeTransport()
        midi.plugInLaunchpad()
        XCTAssertFalse(transport.underpowerSuspected)

        midi.sendSucceeds = false
        transport.setLight(
            .solid(colorHint: 0xFFFFFF), at: LaunchpadPad(row: 0, col: 0)
        )
        XCTAssertTrue(transport.underpowerSuspected)
    }
}
