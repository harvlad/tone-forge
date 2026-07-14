// FakeMIDIInterface.swift
//
// In-memory MIDIInterface for Launchpad tests, ported from the mobile
// suite: scripted endpoints, captured sends, and synchronous injection
// of received messages. The Launchpad endpoints mirror the names the
// physical device exposes over CoreMIDI (hardware-verified).

import Foundation
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class FakeMIDIInterface: MIDIInterface {

    // The physical device's three interfaces, as CoreMIDI reports them.
    nonisolated static let launchpadMIDI = MIDIEndpoint(
        ref: 101, name: "LPProMK3 MIDI",
        displayName: "Launchpad Pro MK3 LPProMK3 MIDI"
    )
    nonisolated static let launchpadDIN = MIDIEndpoint(
        ref: 102, name: "LPProMK3 DIN",
        displayName: "Launchpad Pro MK3 LPProMK3 DIN"
    )
    nonisolated static let launchpadDAW = MIDIEndpoint(
        ref: 103, name: "LPProMK3 DAW",
        displayName: "Launchpad Pro MK3 LPProMK3 DAW"
    )

    var onSetupChanged: (() -> Void)?

    var fakeSources: [MIDIEndpoint] = []
    var fakeDestinations: [MIDIEndpoint] = []

    private(set) var connectedInputs: [MIDIEndpoint] = []
    /// Tests reset this between phases, hence not private(set).
    var sent: [(sysex: [UInt8], endpoint: MIDIEndpoint)] = []

    var connectSucceeds = true
    var sendSucceeds = true

    private var handlers: [MIDIEndpoint: @Sendable ([MIDIMessage], UInt64) -> Void] = [:]

    func sources() -> [MIDIEndpoint] { fakeSources }
    func destinations() -> [MIDIEndpoint] { fakeDestinations }

    @discardableResult
    func connectInput(
        _ endpoint: MIDIEndpoint,
        handler: @escaping @Sendable ([MIDIMessage], UInt64) -> Void
    ) -> Bool {
        guard connectSucceeds else { return false }
        connectedInputs.append(endpoint)
        handlers[endpoint] = handler
        return true
    }

    @discardableResult
    func send(_ sysex: [UInt8], to endpoint: MIDIEndpoint) -> Bool {
        guard sendSucceeds else { return false }
        sent.append((sysex, endpoint))
        return true
    }

    func disconnectAll() {
        connectedInputs.removeAll()
        handlers.removeAll()
    }

    // MARK: - Test drivers

    /// Attach all three Launchpad interfaces and fire setup-changed.
    func plugInLaunchpad() {
        fakeSources = [Self.launchpadDAW, Self.launchpadDIN, Self.launchpadMIDI]
        fakeDestinations = [Self.launchpadDAW, Self.launchpadDIN, Self.launchpadMIDI]
        onSetupChanged?()
    }

    func unplugLaunchpad() {
        fakeSources = []
        fakeDestinations = []
        onSetupChanged?()
    }

    /// Inject received messages as if the driver delivered a packet.
    func receive(
        _ messages: [MIDIMessage], hostTime: UInt64 = 0,
        from endpoint: MIDIEndpoint = FakeMIDIInterface.launchpadMIDI
    ) {
        handlers[endpoint]?(messages, hostTime)
    }
}
