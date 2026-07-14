// MIDIInterface.swift
//
// Thin seam between USBLaunchpadTransport and CoreMIDI, ported from
// the mobile app. This protocol layer deliberately does NOT import
// CoreMIDI — the CoreMIDI implementation lives in JamDesktopAudio
// (CoreMIDIInterface.swift), so everything above the seam (transport,
// protocol layer, controller, tests) works with [MIDIMessage] and
// plain byte arrays under headless `swift test`.
//
// Threading contract:
//   - All MIDIInterface methods are called on the main actor.
//   - `connectInput` handlers fire on CoreMIDI's high-priority
//     receive thread — NOT the main actor. The handler must stamp
//     its clocks there (pre-hop) and hop to main itself.
//   - `onSetupChanged` is always delivered on the main queue.

import Foundation
import ToneForgeEngine

/// A CoreMIDI endpoint reduced to what the transport needs. `ref` is
/// the raw MIDIEndpointRef (UInt32) — opaque outside the CoreMIDI
/// implementation.
public struct MIDIEndpoint: Hashable, Sendable {
    public let ref: UInt32
    /// Endpoint name, e.g. "LPProMK3 MIDI".
    public let name: String
    /// Display name, e.g. "Launchpad Pro MK3 LPProMK3 MIDI".
    public let displayName: String

    public init(ref: UInt32, name: String, displayName: String) {
        self.ref = ref
        self.name = name
        self.displayName = displayName
    }
}

/// Pads in, SysEx out — the transport's view of MIDI.
@MainActor
public protocol MIDIInterface: AnyObject {
    /// Fired (on the main queue) whenever the MIDI setup changes:
    /// device plugged, unplugged, renamed. The transport rescans.
    var onSetupChanged: (() -> Void)? { get set }

    func sources() -> [MIDIEndpoint]
    func destinations() -> [MIDIEndpoint]

    /// Start receiving from `endpoint`. The handler runs on the MIDI
    /// receive thread with the packet's host-time timestamp (0 when
    /// the driver didn't stamp one).
    @discardableResult
    func connectInput(
        _ endpoint: MIDIEndpoint,
        handler: @escaping @Sendable ([MIDIMessage], UInt64) -> Void
    ) -> Bool

    /// Send a complete F0…F7 SysEx message. Returns false on failure
    /// (the transport's underpower heuristic consumes this).
    @discardableResult
    func send(_ sysex: [UInt8], to endpoint: MIDIEndpoint) -> Bool

    /// Tear down every input connection (keeps the client alive so
    /// setup notifications continue).
    func disconnectAll()
}
