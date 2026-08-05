// MIDIOut.swift
//
// MIDI OUTPUT primitives (PERFORM_PARITY spec 2B). MIDI IN already
// ships (USBLaunchpadTransport / MIDIKeyboardTransport); this adds the
// pure, testable half of OUTPUT — the byte encoders and the 24 PPQN
// clock-pulse math — so the CoreMIDI virtual source in the app target
// (MIDIOutTransport) stays a thin sender.
//
// Nothing here imports CoreMIDI: encoders return plain MIDI 1.0 byte
// arrays, matching the [UInt8] boundary the rest of the Launchpad stack
// already speaks. This mirrors LaunchpadProMK3Protocol (which decodes);
// this side encodes.

import Foundation

// MARK: - Byte encoders

/// MIDI 1.0 message encoders. Channels are 0-based (0 = MIDI channel 1).
/// System real-time messages are single status bytes with no channel.
public enum MIDIOutEncoder {

    // System real-time (clock/transport). No channel, no data bytes.
    public static let clock: [UInt8]    = [0xF8]  // timing clock (24/quarter)
    public static let start: [UInt8]    = [0xFA]  // start from song position 0
    public static let `continue`: [UInt8] = [0xFB] // resume from current position
    public static let stop: [UInt8]     = [0xFC]  // stop

    /// Note-on. Velocity 0 is a conventional note-off; callers wanting a
    /// true note-off should use `noteOff`.
    public static func noteOn(channel: UInt8, note: UInt8, velocity: UInt8) -> [UInt8] {
        [0x90 | (channel & 0x0F), note & 0x7F, velocity & 0x7F]
    }

    public static func noteOff(channel: UInt8, note: UInt8, velocity: UInt8 = 0) -> [UInt8] {
        [0x80 | (channel & 0x0F), note & 0x7F, velocity & 0x7F]
    }

    public static func controlChange(channel: UInt8, controller: UInt8, value: UInt8) -> [UInt8] {
        [0xB0 | (channel & 0x0F), controller & 0x7F, value & 0x7F]
    }
}

// MARK: - 24 PPQN clock generator

/// Emits MIDI timing-clock pulses at 24 per quarter note, phase-locked
/// to the song's beat grid. Pure: the caller (AudioEngine's driver)
/// samples song-time each tick and asks how many pulse boundaries were
/// crossed, then sends that many `MIDIOutEncoder.clock` messages.
///
/// Clock is free-running once Start is sent, but anchoring the pulse
/// grid on the first analysed beat means an external sequencer that
/// honours Song Position locks to the actual downbeat, not song-zero.
public struct MIDIClockGenerator: Sendable, Equatable {

    /// MIDI timing-clock resolution: 24 pulses per quarter note.
    public static let ppqn = 24

    public let beatClock: BeatClock

    public init(beatClock: BeatClock) {
        self.beatClock = beatClock
    }

    /// Seconds between clock pulses, or nil without a tempo.
    public var pulseDuration: Double? {
        guard let beat = beatClock.beatDuration, beat > 0 else { return nil }
        return beat / Double(Self.ppqn)
    }

    /// Grid anchor — the first analysed beat, else song-zero.
    private var anchor: Double { beatClock.beats.first ?? 0 }

    /// Zero-based pulse index at song-time `t`, or nil without tempo.
    /// Negative before the anchor (clamped by the caller).
    public func pulseIndex(atSongSec t: Double) -> Int? {
        guard let p = pulseDuration else { return nil }
        return Int(floor((t - anchor) / p))
    }

    /// Number of clock pulses to emit for the span (`from`, `to`].
    /// Zero when time didn't advance past a boundary, or without tempo.
    /// Never negative (a backward seek emits nothing; the caller should
    /// resync transport via Stop/Start instead).
    public func pulsesToEmit(fromSongSec from: Double, toSongSec to: Double) -> Int {
        guard let a = pulseIndex(atSongSec: from),
              let b = pulseIndex(atSongSec: to) else { return 0 }
        return max(0, b - a)
    }
}
