// MIDIOutTransport.swift
//
// CoreMIDI OUTPUT (PERFORM_PARITY spec 2B): publishes a virtual MIDI
// source named "Tone Forge Jam" that external synths, drum machines,
// DAWs, and other apps subscribe to. Emits 24 PPQN timing clock +
// start/stop/continue so hardware follows the Jam transport, plus
// note-on/off so pad triggers can play external gear.
//
// This is the OUTPUT counterpart to CoreMIDIInterface (input). It owns
// its own MIDIClientRef + virtual source and is created lazily (only
// when clock-out is enabled) so headless AppStates never open a
// CoreMIDI client — same rule as USBLaunchpadTransport.
//
// Raw MIDI 1.0 bytes go out via the legacy MIDIReceived + MIDIPacketList
// path: a virtual source "receives" the bytes and CoreMIDI fans them to
// every connected listener. This sidesteps hand-encoding UMP words for
// short channel/real-time messages (the encoders in
// ToneForgeEngine.MIDIOutEncoder already produce the byte streams).
//
// Threading: all methods are @MainActor. MIDIReceived itself is
// thread-safe, but the AudioEngine clock driver calls in on main.
//
// NOTE (v1): clock pulses are driven by a main-thread timer, so tempo
// carries the timer's jitter. A sample-accurate host-time schedule is
// the follow-up; documented in PERFORM_PARITY.

import Foundation
import ToneForgeEngine
#if canImport(CoreMIDI)
import CoreMIDI
#endif

@MainActor
public final class MIDIOutTransport {

    /// True once the virtual source is live.
    public private(set) var isAvailable = false

    #if canImport(CoreMIDI)
    private var client = MIDIClientRef()
    private var virtualSource = MIDIEndpointRef()

    public init(sourceName: String = "Tone Forge Jam") {
        let clientStatus = MIDIClientCreateWithBlock(
            "ToneForgeOut" as CFString, &client, nil
        )
        guard clientStatus == noErr else { return }

        // A virtual source appears in other apps' MIDI *input* lists.
        let srcStatus = MIDISourceCreateWithProtocol(
            client, sourceName as CFString, ._1_0, &virtualSource
        )
        guard srcStatus == noErr else {
            MIDIClientDispose(client)
            client = 0
            return
        }
        // Persist a stable unique ID so re-launches present the same
        // endpoint to peers that remember connections by ID.
        MIDIObjectSetIntegerProperty(virtualSource, kMIDIPropertyUniqueID, 0x546F_4A31) // "ToJ1"
        isAvailable = true
    }

    deinit {
        // deinit is nonisolated; CoreMIDI disposal is thread-safe.
        if virtualSource != 0 { MIDIEndpointDispose(virtualSource) }
        if client != 0 { MIDIClientDispose(client) }
    }

    // MARK: - Transport messages

    public func sendStart()    { send(MIDIOutEncoder.start) }
    public func sendStop()     { send(MIDIOutEncoder.stop) }
    public func sendContinue() { send(MIDIOutEncoder.continue) }

    /// Emit `count` timing-clock pulses (24 per quarter note). The
    /// AudioEngine driver computes `count` from MIDIClockGenerator.
    public func sendClockPulses(_ count: Int) {
        guard count > 0 else { return }
        for _ in 0..<count { send(MIDIOutEncoder.clock) }
    }

    // MARK: - Notes

    public func sendNoteOn(channel: UInt8 = 0, note: UInt8, velocity: UInt8 = 100) {
        send(MIDIOutEncoder.noteOn(channel: channel, note: note, velocity: velocity))
    }

    public func sendNoteOff(channel: UInt8 = 0, note: UInt8) {
        send(MIDIOutEncoder.noteOff(channel: channel, note: note))
    }

    // MARK: - Raw send

    /// Push raw MIDI 1.0 bytes out the virtual source (timestamp = now).
    private func send(_ bytes: [UInt8]) {
        guard isAvailable, !bytes.isEmpty else { return }
        var packetList = MIDIPacketList()
        var packet = MIDIPacketListInit(&packetList)
        bytes.withUnsafeBufferPointer { buf in
            guard let base = buf.baseAddress else { return }
            packet = MIDIPacketListAdd(
                &packetList,
                MemoryLayout<MIDIPacketList>.size,
                packet,
                0,            // timestamp 0 = deliver now
                buf.count,
                base
            )
        }
        MIDIReceived(virtualSource, &packetList)
    }
    #else
    public init(sourceName: String = "Tone Forge Jam") {}
    public func sendStart() {}
    public func sendStop() {}
    public func sendContinue() {}
    public func sendClockPulses(_ count: Int) {}
    public func sendNoteOn(channel: UInt8 = 0, note: UInt8, velocity: UInt8 = 100) {}
    public func sendNoteOff(channel: UInt8 = 0, note: UInt8) {}
    #endif
}
