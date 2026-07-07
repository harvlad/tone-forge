// MIDIInterface.swift
//
// Thin seam between USBLaunchpadTransport and CoreMIDI. This is the
// ONLY file in the app allowed to import CoreMIDI — everything above
// it (transport, protocol layer, tests) works with [MIDIMessage] and
// plain byte arrays, so the whole Launchpad stack is testable with
// FakeMIDIInterface under plain `swift test`.
//
// Threading contract:
//   - All MIDIInterface methods are called on the main actor.
//   - `connectInput` handlers fire on CoreMIDI's high-priority
//     receive thread — NOT the main actor. The handler must stamp
//     its clocks there (pre-hop, per the ContributionEvent contract)
//     and hop to main itself.
//   - `onSetupChanged` is always delivered on the main queue.

import CoreMIDI
import Foundation
import ToneForgeEngine

/// A CoreMIDI endpoint reduced to what the transport needs. `ref` is
/// the raw MIDIEndpointRef (UInt32) — opaque outside this file.
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

// MARK: - CoreMIDI implementation

/// Box handed to the CoreMIDI receive thread. The parser is mutated
/// only on that thread (CoreMIDI serialises delivery per port), so
/// the unchecked-Sendable claim is sound.
private final class ReceiveContext: @unchecked Sendable {
    var parser = UMPParser()
    let handler: @Sendable ([MIDIMessage], UInt64) -> Void
    init(handler: @escaping @Sendable ([MIDIMessage], UInt64) -> Void) {
        self.handler = handler
    }
}

@MainActor
public final class CoreMIDIInterface: MIDIInterface {

    public var onSetupChanged: (() -> Void)?

    private var client = MIDIClientRef()
    private var outputPort = MIDIPortRef()
    /// One input port per connected source; disposed in bulk.
    private var inputPorts: [MIDIPortRef] = []

    public init() {
        MIDIClientCreateWithBlock(
            "ToneForge" as CFString, &client
        ) { [weak self] notification in
            guard notification.pointee.messageID == .msgSetupChanged else { return }
            DispatchQueue.main.async {
                guard let self else { return }
                MainActor.assumeIsolated { self.onSetupChanged?() }
            }
        }
        MIDIOutputPortCreate(client, "ToneForge Out" as CFString, &outputPort)
    }

    deinit {
        if client != 0 { MIDIClientDispose(client) }
    }

    // MARK: Endpoints

    public func sources() -> [MIDIEndpoint] {
        (0..<MIDIGetNumberOfSources()).map { endpoint(MIDIGetSource($0)) }
    }

    public func destinations() -> [MIDIEndpoint] {
        (0..<MIDIGetNumberOfDestinations()).map { endpoint(MIDIGetDestination($0)) }
    }

    private func endpoint(_ ref: MIDIEndpointRef) -> MIDIEndpoint {
        MIDIEndpoint(
            ref: ref,
            name: stringProperty(ref, kMIDIPropertyName),
            displayName: stringProperty(ref, kMIDIPropertyDisplayName)
        )
    }

    private func stringProperty(
        _ object: MIDIObjectRef, _ property: CFString
    ) -> String {
        var value: Unmanaged<CFString>?
        MIDIObjectGetStringProperty(object, property, &value)
        return value?.takeRetainedValue() as String? ?? ""
    }

    // MARK: Receive

    @discardableResult
    public func connectInput(
        _ endpoint: MIDIEndpoint,
        handler: @escaping @Sendable ([MIDIMessage], UInt64) -> Void
    ) -> Bool {
        let context = ReceiveContext(handler: handler)
        var port = MIDIPortRef()
        let status = MIDIInputPortCreateWithProtocol(
            client, "ToneForge In" as CFString, ._1_0, &port
        ) { eventListPointer, _ in
            // CoreMIDI receive thread. Decode UMP words and hand the
            // messages + the packet's host-time stamp straight to the
            // handler — no main-actor hop here.
            for packet in eventListPointer.unsafeSequence() {
                let wordCount = Int(packet.pointee.wordCount)
                guard wordCount > 0 else { continue }
                let words = withUnsafeBytes(of: packet.pointee.words) { raw in
                    Array(raw.bindMemory(to: UInt32.self).prefix(wordCount))
                }
                let messages = context.parser.consume(words)
                if !messages.isEmpty {
                    context.handler(messages, packet.pointee.timeStamp)
                }
            }
        }
        guard status == noErr else { return false }
        guard MIDIPortConnectSource(port, endpoint.ref, nil) == noErr else {
            MIDIPortDispose(port)
            return false
        }
        inputPorts.append(port)
        return true
    }

    public func disconnectAll() {
        for port in inputPorts {
            MIDIPortDispose(port)
        }
        inputPorts.removeAll()
    }

    // MARK: Send

    @discardableResult
    public func send(_ sysex: [UInt8], to endpoint: MIDIEndpoint) -> Bool {
        let words = UMPSysEx7.encode(sysex)
        guard !words.isEmpty else { return true }

        // A MIDIEventPacket holds ≤64 words; SysEx7 packets are word
        // PAIRS carrying their own start/continue/end framing, so
        // splitting a long message across event lists is legal. 62
        // keeps pairs intact with headroom.
        for start in stride(from: 0, to: words.count, by: 62) {
            let chunk = Array(words[start..<min(start + 62, words.count)])
            var eventList = MIDIEventList()
            let packet = MIDIEventListInit(&eventList, ._1_0)
            // ≤62 words always fits the fresh 64-word packet, so Add
            // cannot overflow here.
            chunk.withUnsafeBufferPointer { buffer in
                guard let base = buffer.baseAddress else { return }
                _ = MIDIEventListAdd(
                    &eventList, MemoryLayout<MIDIEventList>.size,
                    packet, 0, buffer.count, base
                )
            }
            guard MIDISendEventList(outputPort, endpoint.ref, &eventList) == noErr
            else { return false }
        }
        return true
    }
}
