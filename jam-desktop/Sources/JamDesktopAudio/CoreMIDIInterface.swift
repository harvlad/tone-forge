// CoreMIDIInterface.swift
//
// The ONLY file in jam-desktop allowed to import CoreMIDI, ported
// from the mobile app (MIDIInterface.swift). Implements the
// JamDesktopCore MIDIInterface seam; everything above it (transport,
// controller, tests) works with [MIDIMessage] and plain byte arrays.
//
// The mobile original also enabled CoreMIDI's network session — an
// iOS-only API with no macOS equivalent, dropped here.

import CoreMIDI
import Foundation
import JamDesktopCore
import ToneForgeEngine

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
            "Jamn" as CFString, &client
        ) { [weak self] notification in
            guard notification.pointee.messageID == .msgSetupChanged else { return }
            DispatchQueue.main.async {
                guard let self else { return }
                MainActor.assumeIsolated { self.onSetupChanged?() }
            }
        }
        MIDIOutputPortCreate(client, "Jamn Out" as CFString, &outputPort)
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
            client, "Jamn In" as CFString, ._1_0, &port
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
