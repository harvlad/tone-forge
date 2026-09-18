// USBLaunchpadTransportTests.swift
//
// Hardware transport behaviour over FakeMIDIInterface, ported from
// the mobile suite: hot-plug, mode SysEx, vel-0 release, pre-hop
// event stamps, LED diffing/batching, suspend/resume, and the
// underpower heuristic. (The mobile AppState LED-mirroring test has
// no desktop counterpart — the panel reads controller state instead.)

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

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

    /// StampedPadTransport: when the stamped callbacks are set they
    /// REPLACE the legacy ones and carry the receive-thread clocks
    /// (song seconds + packet host time) with the pad, so the
    /// controller can quantize against the true press instant.
    func testStampedPadCallbacksCarryReceiveThreadClocks() {
        let transport = makeTransport()
        var downs: [(pad: LaunchpadPad, song: Double, host: UInt64)] = []
        var ups: [(pad: LaunchpadPad, song: Double, host: UInt64)] = []
        var legacyCalls = 0
        transport.onPadDown = { _ in legacyCalls += 1 }
        transport.onPadUp = { _ in legacyCalls += 1 }
        transport.onPadDownStamped = { downs.append(($0, $1, $2)) }
        transport.onPadUpStamped = { ups.append(($0, $1, $2)) }
        midi.plugInLaunchpad()

        midi.receive([.noteOn(channel: 0, note: 11, velocity: 127)], hostTime: 777)
        midi.receive([.noteOn(channel: 0, note: 11, velocity: 0)], hostTime: 888)
        drainMainQueue()

        XCTAssertEqual(downs.count, 1)
        XCTAssertEqual(downs[0].pad, LaunchpadPad(row: 7, col: 0))
        XCTAssertEqual(downs[0].song, 42.5)     // receive-thread stamp
        XCTAssertEqual(downs[0].host, 777)      // packet host stamp
        XCTAssertEqual(ups.count, 1)
        XCTAssertEqual(ups[0].host, 888)
        XCTAssertEqual(legacyCalls, 0, "stamped callbacks replace legacy")
    }

    /// The receive-thread pad-up tap fires the instant a release
    /// message decodes — BEFORE the main hop (no queue drain here!) —
    /// and only for releases, never presses. This is the seam the
    /// zero-latency audio release rides; the stamped padUp still
    /// follows on main.
    func testFastPadUpTapFiresOnReceiveThreadBeforeMainHop() {
        final class PadBox: @unchecked Sendable {
            var pads: [LaunchpadPad] = []
        }
        let transport = makeTransport()
        let box = PadBox()
        transport.setFastPadUpTap { pad in box.pads.append(pad) }
        midi.plugInLaunchpad()

        // FakeMIDIInterface invokes the receive handler synchronously,
        // so anything visible WITHOUT draining the main queue happened
        // pre-hop.
        midi.receive([
            .noteOn(channel: 0, note: 11, velocity: 127),   // press: no tap
            .noteOn(channel: 0, note: 11, velocity: 0),     // vel-0 release
            .noteOff(channel: 0, note: 45, velocity: 64),   // real Note Off
            .noteOn(channel: 1, note: 11, velocity: 0),     // wrong channel
            .noteOff(channel: 0, note: 9, velocity: 0),     // not a grid note
        ])

        XCTAssertEqual(box.pads, [
            LaunchpadPad(row: 7, col: 0),   // note 11
            LaunchpadPad(row: 4, col: 4),   // note 45
        ], "tap fires pre-hop, releases only, grid notes only")
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

    /// The category accent colors must land on hue-faithful palette
    /// entries when a sounding pad pulses — with only the six PDF
    /// anchors, pink/purple pulsed RED/BLUE and slate pulsed blue, so
    /// the hardware visibly disagreed with the on-screen grid.
    func testCategoryColorsPulseOnHueFaithfulPaletteEntries() {
        typealias LP = LaunchpadProMK3Protocol
        // vocal pink → magenta (was: red)
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0xEC4899), LP.paletteMagenta)
        // fx / stab purple → magenta (was: blue)
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0xA855F7), LP.paletteMagenta)
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0x8B5CF6), LP.paletteMagenta)
        // rhythm blue → cyan-blue (was: pure blue)
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0x3B82F6), LP.paletteCyanBlue)
        // sample slate → white (was: blue-ish)
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0x64748B), LP.paletteWhite)
        // The PDF-cited primaries keep their entries.
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0xFF0000), LP.paletteRed)
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0x00FF00), LP.paletteGreen)
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0x0000FF), LP.paletteBlue)
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

    /// Flaps = torn-down links, NOT successful connects: three
    /// unplug/replug cycles inside 10 s raise the banner.
    func testConnectionFlappingRaisesUnderpower() {
        let transport = makeTransport()
        midi.plugInLaunchpad()                 // connect — not a flap
        XCTAssertFalse(transport.underpowerSuspected)

        for cycle in 1...3 {
            now = now.addingTimeInterval(1)
            midi.unplugLaunchpad()             // flap N
            if cycle < 3 {
                XCTAssertFalse(transport.underpowerSuspected,
                               "cycle \(cycle) must not raise the banner yet")
            }
            now = now.addingTimeInterval(1)
            midi.plugInLaunchpad()
        }
        XCTAssertTrue(transport.underpowerSuspected)
    }

    /// A single physical plug-in makes CoreMIDI fire a BURST of
    /// setup-change notifications (device + entity + endpoint
    /// appearances). A stable connection must survive the burst with
    /// no banner and no reconnect churn — counting successful
    /// connects as flaps false-fired the banner on a healthy cable.
    func testStableConnectSurvivesSetupNotificationBurst() {
        let transport = makeTransport()
        midi.plugInLaunchpad()
        midi.onSetupChanged?()
        midi.onSetupChanged?()
        midi.onSetupChanged?()

        XCTAssertFalse(transport.underpowerSuspected)
        XCTAssertEqual(midi.connectedInputs, [FakeMIDIInterface.launchpadMIDI],
                       "one physical device = ONE input connection")
        XCTAssertEqual(
            transport.connectionState,
            .connected(deviceName: "Launchpad Pro MK3")
        )
    }

    /// During enumeration the endpoint's display name resolves late
    /// ("LPProMK3 MIDI" → "Launchpad Pro MK3 LPProMK3 MIDI") while its
    /// ref stays put. A property-only change is the same device — no
    /// reconnect, no flap.
    func testEndpointRenameDuringEnumerationDoesNotReconnect() {
        let transport = makeTransport()
        let bare = MIDIEndpoint(
            ref: FakeMIDIInterface.launchpadMIDI.ref,
            name: "LPProMK3 MIDI", displayName: "LPProMK3 MIDI"
        )
        midi.fakeSources = [bare]
        midi.fakeDestinations = [bare]
        midi.onSetupChanged?()
        XCTAssertEqual(midi.connectedInputs.count, 1)

        midi.fakeSources = [FakeMIDIInterface.launchpadMIDI]
        midi.fakeDestinations = [FakeMIDIInterface.launchpadMIDI]
        midi.onSetupChanged?()

        XCTAssertEqual(midi.connectedInputs.count, 1, "same ref = no reconnect")
        XCTAssertFalse(transport.underpowerSuspected)
        XCTAssertEqual(
            transport.connectionState,
            .connected(deviceName: "Launchpad Pro MK3")
        )
    }

    /// Failed connects still count toward the threshold — a browning-
    /// out device that never comes up cleanly must raise the banner.
    func testRepeatedFailedConnectsRaiseUnderpower() {
        midi.connectSucceeds = false
        let transport = makeTransport()
        midi.plugInLaunchpad()                 // failed connect 1
        XCTAssertFalse(transport.underpowerSuspected)
        midi.onSetupChanged?()                 // failed connect 2
        XCTAssertFalse(transport.underpowerSuspected)
        midi.onSetupChanged?()                 // failed connect 3
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

    /// 30 s of trouble-free connection clears the banner (a one-time
    /// transient must not pin it for the whole session). Production
    /// drives reviewStability from a timer; tests drive it against
    /// the injected clock.
    func testUnderpowerClearsAfterSustainedStableConnection() {
        let transport = makeTransport()
        midi.plugInLaunchpad()
        for _ in 1...3 {
            now = now.addingTimeInterval(1)
            midi.unplugLaunchpad()
            now = now.addingTimeInterval(1)
            midi.plugInLaunchpad()
        }
        XCTAssertTrue(transport.underpowerSuspected)

        now = now.addingTimeInterval(29)       // 29 s quiet: not yet
        transport.reviewStability()
        XCTAssertTrue(transport.underpowerSuspected)

        now = now.addingTimeInterval(2)        // 31 s quiet: clears
        transport.reviewStability()
        XCTAssertFalse(transport.underpowerSuspected)
    }

    /// The stable clear only applies while CONNECTED — an unplugged
    /// transport keeps the banner (the user should still see it).
    func testStableClearRequiresLiveConnection() {
        let transport = makeTransport()
        midi.plugInLaunchpad()
        for _ in 1...3 {
            now = now.addingTimeInterval(1)
            midi.unplugLaunchpad()
            now = now.addingTimeInterval(1)
            midi.plugInLaunchpad()
        }
        midi.unplugLaunchpad()
        XCTAssertTrue(transport.underpowerSuspected)

        now = now.addingTimeInterval(120)
        transport.reviewStability()
        XCTAssertTrue(transport.underpowerSuspected)
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

    // MARK: - Control-button LEDs (function buttons, D-036)

    /// The control path must light CC addresses the grid gate rejects
    /// — 93 (Session, "row 9"), 8 (Stop Clip, "col 8 row 0") and 101
    /// (track select) are all PadIndex.isValid == false, which is
    /// exactly why the dedicated method exists.
    func testControlLightsBypassPadValidityGate() {
        let transport = makeTransport()
        midi.plugInLaunchpad()
        midi.sent.removeAll()

        XCTAssertFalse(PadIndex(93).isValid)
        XCTAssertFalse(PadIndex(8).isValid)
        XCTAssertFalse(PadIndex(101).isValid)

        transport.setControlLight(.solid(colorHint: 0xFF0000), cc: 93)
        XCTAssertEqual(
            midi.sent.map(\.sysex),
            [LaunchpadProMK3Protocol.sysExHeader
                + [0x03, 0x03, 93, 0x7F, 0x00, 0x00, 0xF7]]
        )

        // Batched frame: one message, both CCs addressed raw.
        midi.sent.removeAll()
        transport.setControlLights([
            8: .solid(colorHint: 0x00FF00),
            101: .solid(colorHint: 0x0000FF),
        ])
        XCTAssertEqual(midi.sent.count, 1)
        XCTAssertEqual(midi.sent[0].sysex.count, 18)   // 7 + 2×5 + 1
    }

    func testControlLedCacheDiffsRepeatSends() {
        let transport = makeTransport()
        midi.plugInLaunchpad()
        midi.sent.removeAll()

        transport.setControlLight(.solid(colorHint: 0xFFBF00), cc: 8)
        XCTAssertEqual(midi.sent.count, 1)
        // Unchanged → nothing on the wire.
        transport.setControlLight(.solid(colorHint: 0xFFBF00), cc: 8)
        XCTAssertEqual(midi.sent.count, 1)
        // Changed → one more message.
        transport.setControlLight(.off, cc: 8)
        XCTAssertEqual(midi.sent.count, 2)
    }

    /// Control pulses ride the palette path like grid pulses — red
    /// recording pulse lands on the PDF-cited red entry.
    func testControlPulseMapsToNearestPaletteEntry() {
        let transport = makeTransport()
        midi.plugInLaunchpad()
        midi.sent.removeAll()

        transport.setControlLight(.pulse(colorHint: 0xFF0000), cc: 1)
        XCTAssertEqual(
            midi.sent.map(\.sysex),
            [LaunchpadProMK3Protocol.ledPulse(
                pad: PadIndex(1),
                palette: LaunchpadProMK3Protocol.paletteRed
            )]
        )
    }

    /// Reconnect repaints EVERYTHING — grid and function buttons —
    /// from their caches, so a replug never leaves control LEDs dark.
    func testReconnectRedrawsControlLedsFromCache() {
        let transport = makeTransport()
        midi.plugInLaunchpad()
        transport.setControlLight(.solid(colorHint: 0xFF0000), cc: 93)
        transport.setControlLight(.solid(colorHint: 0xFFBF00), cc: 8)

        midi.unplugLaunchpad()
        midi.sent.removeAll()
        midi.plugInLaunchpad()

        // Programmer mode + one redraw: 64 grid + 2 control specs =
        // 7 + 66×5 + 1 bytes.
        XCTAssertEqual(midi.sent.count, 2)
        let redraw = midi.sent[1].sysex
        XCTAssertEqual(redraw.count, 338)
        for spec: [UInt8] in [
            [0x03, 8, 0x7F, 0x5F, 0x00],    // Stop Clip amber (>>1)
            [0x03, 93, 0x7F, 0x00, 0x00],   // Session red
        ] {
            XCTAssertTrue(redraw.indices.dropLast(4).contains { i in
                Array(redraw[i..<i + 5]) == spec
            }, "reconnect redraw missing control spec \(spec)")
        }
    }

    /// The gate-bypass must not swing the other way: a control write
    /// aimed at a VALID grid address (11..88) is rejected — nothing on
    /// the wire, nothing cached — so a mis-caller can't repaint pads
    /// behind ledCache's back and leave the grid diff blind to it.
    func testControlPathRejectsGridPadAddresses() {
        let transport = makeTransport()
        midi.plugInLaunchpad()

        // Grid paints top-left pad at PadIndex 81 via the grid path.
        let pad = LaunchpadPad(row: 0, col: 0)
        transport.setLight(.solid(colorHint: 0xFF8800), at: pad)
        midi.sent.removeAll()

        // Mis-aimed control writes at grid addresses: dropped whole,
        // even when batched with a legitimate function-button CC.
        transport.setControlLight(.solid(colorHint: 0x00FF00), cc: 81)
        XCTAssertTrue(midi.sent.isEmpty, "grid address dropped")
        transport.setControlLights([
            45: .solid(colorHint: 0x00FF00),   // valid grid pad
            93: .solid(colorHint: 0xFF0000),   // Session — legit
        ])
        XCTAssertEqual(
            midi.sent.map(\.sysex),
            [LaunchpadProMK3Protocol.sysExHeader
                + [0x03, 0x03, 93, 0x7F, 0x00, 0x00, 0xF7]],
            "only the function-button CC reaches the wire"
        )

        // ledCache stayed authoritative: the grid pad still diffs as
        // unchanged, and a reconnect redraw carries the GRID color for
        // 81 (the rejected write never entered controlLedCache, which
        // would have double-addressed the pad).
        midi.sent.removeAll()
        transport.setLight(.solid(colorHint: 0xFF8800), at: pad)
        XCTAssertTrue(midi.sent.isEmpty, "grid cache undisturbed")

        midi.unplugLaunchpad()
        midi.sent.removeAll()
        midi.plugInLaunchpad()
        let redraw = midi.sent[1].sysex
        // 64 grid + 1 control (93) specs = 7 + 65×5 + 1 bytes.
        XCTAssertEqual(redraw.count, 333)
        XCTAssertTrue(redraw.indices.dropLast(4).contains { i in
            Array(redraw[i..<i + 5]) == [0x03, 81, 0x7F, 0x44, 0x00]
        }, "pad 81 redraws with the grid color (0xFF8800 >> 1)")
    }

    /// Grid frames and control frames stay independent: a control
    /// write never perturbs the grid cache (a full-frame grid redraw
    /// still diffs to nothing) and vice versa.
    func testControlAndGridLedCachesAreIndependent() {
        let transport = makeTransport()
        midi.plugInLaunchpad()

        let pad = LaunchpadPad(row: 0, col: 0)
        transport.setLight(.solid(colorHint: 0xFF8800), at: pad)
        midi.sent.removeAll()

        transport.setControlLight(.solid(colorHint: 0xFF8800), cc: 91)
        XCTAssertEqual(midi.sent.count, 1)

        // The grid pad is still cached — resending it is a no-op.
        transport.setLight(.solid(colorHint: 0xFF8800), at: pad)
        XCTAssertEqual(midi.sent.count, 1)
    }
}
