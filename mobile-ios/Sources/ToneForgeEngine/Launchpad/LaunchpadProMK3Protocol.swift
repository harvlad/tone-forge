// LaunchpadProMK3Protocol.swift
//
// Pure protocol layer for the Novation Launchpad Pro [MK3]. Every
// byte in this file is verified against the official "Launchpad Pro
// [MK3] Programmer's reference manual" (LPP3_prog_ref_guide_200415,
// 21 pages) — citations use `// PDF p.<n>`. Grid note numbers are
// additionally confirmed by the User Guide's appendix A.1.9 (p.63)
// and by the physical device.
//
// Nothing here touches CoreMIDI: this file converts between
// PadIndex/colour values and MIDI 1.0 byte streams (plus the UMP
// word framing CoreMIDI's `._1_0` protocol delivers), so the whole
// protocol is testable under plain `swift test` on macOS.
//
// Layering:
//   MIDIMessage / MIDI1Parser — generic MIDI 1.0 byte-stream parsing.
//   UMPParser / UMPSysEx7     — Universal MIDI Packet framing used by
//                               MIDIEventList (MIDI-1.0 protocol).
//   LaunchpadProMK3Protocol   — device-specific notes/CCs/SysEx.

import Foundation

// MARK: - MIDIMessage

/// The subset of MIDI the Launchpad transport consumes. Everything
/// else on the wire (aftertouch, clock, active sensing) parses but is
/// dropped before reaching this enum.
public enum MIDIMessage: Equatable, Sendable {
    /// `channel` is 0-based (0 = MIDI channel 1).
    case noteOn(channel: UInt8, note: UInt8, velocity: UInt8)
    case noteOff(channel: UInt8, note: UInt8, velocity: UInt8)
    case controlChange(channel: UInt8, controller: UInt8, value: UInt8)
    /// Complete SysEx including the F0/F7 framing bytes.
    case sysex([UInt8])
}

// MARK: - MIDI 1.0 byte-stream parser

/// Incremental MIDI 1.0 parser. Stateful so SysEx split across
/// several buffers reassembles, and running status resolves across
/// buffer boundaries. Real-time bytes (F8h–FFh) may interleave
/// anywhere per the MIDI spec and are skipped.
public struct MIDI1Parser: Sendable {

    private var runningStatus: UInt8 = 0
    private var pendingData: [UInt8] = []
    /// Non-nil while inside F0…F7 (accumulates including the F0).
    private var sysexBuffer: [UInt8]? = nil

    public init() {}

    /// One-shot convenience for byte streams known to be complete.
    public static func parse(_ bytes: [UInt8]) -> [MIDIMessage] {
        var parser = MIDI1Parser()
        return parser.consume(bytes)
    }

    public mutating func consume(_ bytes: [UInt8]) -> [MIDIMessage] {
        var out: [MIDIMessage] = []
        for byte in bytes {
            if byte >= 0xF8 { continue }            // real-time, skip
            if var sysex = sysexBuffer {
                if byte == 0xF7 {
                    sysex.append(0xF7)
                    out.append(.sysex(sysex))
                    sysexBuffer = nil
                } else if byte & 0x80 != 0 {
                    // Status byte interrupts an unterminated SysEx:
                    // drop the fragment, reprocess the byte below.
                    sysexBuffer = nil
                    consumeStatusOrData(byte, into: &out)
                } else {
                    sysex.append(byte)
                    sysexBuffer = sysex
                }
                continue
            }
            consumeStatusOrData(byte, into: &out)
        }
        return out
    }

    private mutating func consumeStatusOrData(
        _ byte: UInt8, into out: inout [MIDIMessage]
    ) {
        if byte == 0xF0 {
            sysexBuffer = [0xF0]
            runningStatus = 0
            pendingData.removeAll()
            return
        }
        if byte & 0x80 != 0 {
            if byte >= 0xF0 {
                // System common (F1–F7 outside SysEx): cancels
                // running status, carries nothing we consume.
                runningStatus = 0
            } else {
                runningStatus = byte
            }
            pendingData.removeAll()
            return
        }
        // Data byte under the current running status.
        guard runningStatus != 0 else { return }
        pendingData.append(byte)
        let needed = MIDI1Parser.dataLength(for: runningStatus)
        guard pendingData.count == needed else { return }
        emit(status: runningStatus, data: pendingData, into: &out)
        pendingData.removeAll()
    }

    private static func dataLength(for status: UInt8) -> Int {
        switch status & 0xF0 {
        case 0xC0, 0xD0: return 1   // program change / channel pressure
        default:          return 2
        }
    }

    private func emit(
        status: UInt8, data: [UInt8], into out: inout [MIDIMessage]
    ) {
        let channel = status & 0x0F
        switch status & 0xF0 {
        case 0x90:
            out.append(.noteOn(channel: channel, note: data[0], velocity: data[1]))
        case 0x80:
            out.append(.noteOff(channel: channel, note: data[0], velocity: data[1]))
        case 0xB0:
            out.append(.controlChange(channel: channel, controller: data[0], value: data[1]))
        default:
            break   // aftertouch / pitch bend etc: parsed, dropped
        }
    }
}

// MARK: - UMP (Universal MIDI Packet) framing

/// Decoder for the UMP words `MIDIEventList` delivers when the input
/// port is created with the `._1_0` protocol. Two message types
/// matter: MT 2 (32-bit MIDI 1.0 channel voice) and MT 3 (64-bit
/// SysEx7 data). Other types are skipped with the correct word count
/// so the stream never desynchronises.
public struct UMPParser: Sendable {

    /// SysEx7 payload accumulated across start/continue packets
    /// (payload excludes F0/F7 — UMP frames SysEx by status nibble).
    private var sysexPayload: [UInt8]? = nil

    public init() {}

    public mutating func consume(_ words: [UInt32]) -> [MIDIMessage] {
        var out: [MIDIMessage] = []
        var i = 0
        while i < words.count {
            let word = words[i]
            let messageType = word >> 28
            switch messageType {
            case 0x2:   // MIDI 1.0 channel voice, 1 word
                let status = UInt8((word >> 16) & 0xFF)
                let d1 = UInt8((word >> 8) & 0x7F)
                let d2 = UInt8(word & 0x7F)
                let channel = status & 0x0F
                switch status & 0xF0 {
                case 0x90:
                    out.append(.noteOn(channel: channel, note: d1, velocity: d2))
                case 0x80:
                    out.append(.noteOff(channel: channel, note: d1, velocity: d2))
                case 0xB0:
                    out.append(.controlChange(channel: channel, controller: d1, value: d2))
                default:
                    break
                }
                i += 1
            case 0x3:   // SysEx7, 2 words
                guard i + 1 < words.count else { return out }
                let word1 = words[i + 1]
                let status = (word >> 20) & 0xF
                let count = Int((word >> 16) & 0xF)
                var bytes: [UInt8] = [
                    UInt8((word >> 8) & 0x7F), UInt8(word & 0x7F),
                    UInt8((word1 >> 24) & 0x7F), UInt8((word1 >> 16) & 0x7F),
                    UInt8((word1 >> 8) & 0x7F), UInt8(word1 & 0x7F),
                ]
                bytes = Array(bytes.prefix(min(count, 6)))
                switch status {
                case 0x0:   // complete in one packet
                    out.append(.sysex([0xF0] + bytes + [0xF7]))
                    sysexPayload = nil
                case 0x1:   // start
                    sysexPayload = bytes
                case 0x2:   // continue
                    sysexPayload?.append(contentsOf: bytes)
                case 0x3:   // end
                    if var payload = sysexPayload {
                        payload.append(contentsOf: bytes)
                        out.append(.sysex([0xF0] + payload + [0xF7]))
                        sysexPayload = nil
                    }
                default:
                    break
                }
                i += 2
            case 0x4:   // MIDI 2.0 channel voice — not used, 2 words
                i += 2
            case 0x5:   // 128-bit data — not used, 4 words
                i += 4
            default:    // MT 0 (utility) / 1 (system): 1 word
                i += 1
            }
        }
        return out
    }
}

/// Encoder producing SysEx7 UMP words from a complete F0…F7 message.
/// Used by the send path (MIDISendEventList with `._1_0` protocol).
public enum UMPSysEx7 {

    /// `sysex` must include the F0/F7 framing; the framing is
    /// stripped because UMP carries it in the packet status nibble.
    /// Group 0. Returns pairs of words (each SysEx7 packet is 64-bit).
    public static func encode(_ sysex: [UInt8]) -> [UInt32] {
        var payload = sysex
        if payload.first == 0xF0 { payload.removeFirst() }
        if payload.last == 0xF7 { payload.removeLast() }

        var words: [UInt32] = []
        let chunks = stride(from: 0, to: max(payload.count, 1), by: 6).map {
            Array(payload[$0..<min($0 + 6, payload.count)])
        }
        for (index, chunk) in chunks.enumerated() {
            let status: UInt32
            if chunks.count == 1 {
                status = 0x0                     // complete
            } else if index == 0 {
                status = 0x1                     // start
            } else if index == chunks.count - 1 {
                status = 0x3                     // end
            } else {
                status = 0x2                     // continue
            }
            let b = chunk + Array(repeating: 0, count: 6 - chunk.count)
            words.append(
                (0x3 << 28) | (status << 20) | (UInt32(chunk.count) << 16)
                    | (UInt32(b[0]) << 8) | UInt32(b[1])
            )
            words.append(
                (UInt32(b[2]) << 24) | (UInt32(b[3]) << 16)
                    | (UInt32(b[4]) << 8) | UInt32(b[5])
            )
        }
        return words
    }
}

// MARK: - Launchpad Pro [MK3] protocol

public enum LaunchpadProMK3Protocol {

    // MARK: Device identity

    /// The three USB interfaces are named "LPProMK3 MIDI", "LPProMK3
    /// DIN" and "LPProMK3 DAW". Programmer Mode I/O (pad events in,
    /// LED lighting out) uses the MIDI interface. // PDF p.6
    /// (Names confirmed against the physical device via CoreMIDI.)
    public static let midiPortName = "LPProMK3 MIDI"

    /// Fragment matched against the CoreMIDI display name
    /// ("Launchpad Pro MK3 LPProMK3 MIDI" on macOS/iOS).
    public static let deviceNameFragment = "Launchpad Pro MK3"

    // MARK: SysEx

    /// All SysEx begins F0h 00h 20h 29h 02h 0Eh, then a command
    /// byte. // PDF p.6
    public static let sysExHeader: [UInt8] = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0E]

    /// Live/Programmer toggle: command 0Eh, mode 1 = Programmer,
    /// 0 = Live. // PDF p.18, command summary p.21
    public static let enterProgrammerMode: [UInt8] =
        sysExHeader + [0x0E, 0x01, 0xF7]
    public static let enterLiveMode: [UInt8] =
        sysExHeader + [0x0E, 0x00, 0xF7]

    /// LED lighting SysEx command byte. // PDF p.12
    public static let ledLightingCommand: UInt8 = 0x03

    /// A lighting message may carry at most 106 colour specs
    /// ("The message may contain up to 106 <Colour Spec> entries to
    /// light up the entire Launchpad Pro [MK3] surface"). // PDF p.13
    public static let maxColorSpecsPerMessage = 106

    // MARK: Grid note mapping

    /// Programmer-mode grid pads send/accept notes 11–88 where
    /// note = 10·row + col with row 1 at the BOTTOM — identical to
    /// the PadIndex convention. // PDF p.19; User Guide p.63 (A.1.9)
    public static func padIndex(forNote note: UInt8) -> PadIndex? {
        let pad = PadIndex(Int(note))
        return pad.isValid ? pad : nil
    }

    public static func note(for pad: PadIndex) -> UInt8 {
        UInt8(pad.rawValue)
    }

    // MARK: Outer buttons (CCs)

    /// The 32 buttons around the grid plus Shift and the logo LED all
    /// send Control Change in Programmer mode. // PDF p.19
    public enum ControlButton: Hashable, Sendable {
        /// CC 90, top-left. // PDF p.19
        case shift
        /// CC 99, the Novation logo (LED only — it is not a
        /// physical button, but it is addressable). // PDF p.19
        case logo
        /// Left column, CC = 10·row (10–80), row 1 = bottom. // PDF p.19
        case left(row: Int)
        /// Right column, CC = 10·row + 9 (19–89), row 1 = bottom. // PDF p.19
        case right(row: Int)
        /// Top row, CC = 90 + col (91–98). // PDF p.19
        case top(col: Int)
        /// Track select row (upper bottom row), CC = 100 + col
        /// (101–108). // PDF p.19
        case trackSelect(col: Int)
        /// Track control row (lower bottom row), CC = col (1–8).
        /// // PDF p.19
        case trackControl(col: Int)
    }

    public static func controlButton(forCC cc: UInt8) -> ControlButton? {
        switch Int(cc) {
        case 90:          return .shift
        case 99:          return .logo
        case 1...8:       return .trackControl(col: Int(cc))
        case 101...108:   return .trackSelect(col: Int(cc) - 100)
        case 91...98:     return .top(col: Int(cc) - 90)
        case 10, 20, 30, 40, 50, 60, 70, 80:
            return .left(row: Int(cc) / 10)
        case 19, 29, 39, 49, 59, 69, 79, 89:
            return .right(row: Int(cc) / 10)
        default:
            return nil
        }
    }

    // MARK: LED lighting

    /// One <Colour Spec> in the LED lighting SysEx. // PDF p.12–13
    public enum ColorSpec: Equatable, Sendable {
        /// Type 00h: static palette colour, 1 data byte. // PDF p.12
        case staticPalette(pad: PadIndex, palette: UInt8)
        /// Type 01h: flashing between Colour B and Colour A, 2 data
        /// bytes (B first). // PDF p.12
        case flash(pad: PadIndex, paletteB: UInt8, paletteA: UInt8)
        /// Type 02h: pulsing palette colour, 1 data byte. // PDF p.12
        case pulse(pad: PadIndex, palette: UInt8)
        /// Type 03h: RGB, 3 data bytes 0–127. `colorHint` is 0xRRGGBB
        /// with 8-bit channels; each channel is shifted >>1 onto the
        /// wire. // PDF p.13
        case rgb(pad: PadIndex, colorHint: UInt32)

        var bytes: [UInt8] {
            switch self {
            case .staticPalette(let pad, let palette):
                return [0x00, UInt8(pad.rawValue), palette & 0x7F]
            case .flash(let pad, let b, let a):
                return [0x01, UInt8(pad.rawValue), b & 0x7F, a & 0x7F]
            case .pulse(let pad, let palette):
                return [0x02, UInt8(pad.rawValue), palette & 0x7F]
            case .rgb(let pad, let hint):
                return [
                    0x03, UInt8(pad.rawValue),
                    UInt8((hint >> 16) & 0xFF) >> 1,
                    UInt8((hint >> 8) & 0xFF) >> 1,
                    UInt8(hint & 0xFF) >> 1,
                ]
            }
        }
    }

    /// Builds LED lighting SysEx messages from colour specs, chunking
    /// so no message exceeds 106 specs. A full 64-pad redraw is a
    /// single message. // PDF p.12–13
    public static func ledMessages(_ specs: [ColorSpec]) -> [[UInt8]] {
        guard !specs.isEmpty else { return [] }
        return stride(from: 0, to: specs.count, by: maxColorSpecsPerMessage)
            .map { start in
                let chunk = specs[start..<min(start + maxColorSpecsPerMessage, specs.count)]
                return sysExHeader + [ledLightingCommand]
                    + chunk.flatMap(\.bytes) + [0xF7]
            }
    }

    /// RGB batch convenience: the transport's whole-frame redraw path.
    public static func ledRGBBatch(
        _ entries: [(pad: PadIndex, colorHint: UInt32)]
    ) -> [[UInt8]] {
        ledMessages(entries.map { .rgb(pad: $0.pad, colorHint: $0.colorHint) })
    }

    /// Single-pad flash message (palette colours). // PDF p.12
    public static func ledFlash(
        pad: PadIndex, paletteB: UInt8, paletteA: UInt8
    ) -> [UInt8] {
        ledMessages([.flash(pad: pad, paletteB: paletteB, paletteA: paletteA)])[0]
    }

    /// Single-pad pulse message (palette colour). // PDF p.12
    public static func ledPulse(pad: PadIndex, palette: UInt8) -> [UInt8] {
        ledMessages([.pulse(pad: pad, palette: palette)])[0]
    }

    // MARK: Palette (PDF-cited entries only)

    /// The reference manual's palette table (PDF p.9–10) is printed
    /// as colour swatches without RGB values, so only entries the
    /// manual names in prose are used here:
    ///   0  = off        // PDF p.12 (example 4)
    ///   5  = red        // PDF p.11 (example 1)
    ///   13 = yellow     // PDF p.13 (SysEx example)
    ///   21 = green      // PDF p.13 (SysEx example)
    ///   37 = turquoise  // PDF p.13 (SysEx example)
    ///   45 = blue       // PDF p.12 (example 3)
    /// Static/dim colours go over the RGB spec (type 03h) instead, so
    /// this coarse table only ever feeds flash/pulse effects.
    public static let paletteOff: UInt8 = 0
    public static let paletteRed: UInt8 = 5
    public static let paletteYellow: UInt8 = 13
    public static let paletteGreen: UInt8 = 21
    public static let paletteTurquoise: UInt8 = 37
    public static let paletteBlue: UInt8 = 45

    private static let paletteAnchors: [(entry: UInt8, hint: UInt32)] = [
        (paletteOff, 0x000000),
        (paletteRed, 0xFF0000),
        (paletteYellow, 0xFFFF00),
        (paletteGreen, 0x00FF00),
        (paletteTurquoise, 0x00FFFF),
        (paletteBlue, 0x0000FF),
    ]

    /// Nearest PDF-cited palette entry to an 0xRRGGBB hint (squared
    /// Euclidean distance in RGB). Used for pulse effects, where the
    /// hardware requires a palette entry rather than RGB.
    public static func nearestPaletteEntry(colorHint: UInt32) -> UInt8 {
        let r = Double((colorHint >> 16) & 0xFF)
        let g = Double((colorHint >> 8) & 0xFF)
        let b = Double(colorHint & 0xFF)
        var best: (entry: UInt8, distance: Double) = (paletteOff, .infinity)
        for anchor in paletteAnchors {
            let ar = Double((anchor.hint >> 16) & 0xFF)
            let ag = Double((anchor.hint >> 8) & 0xFF)
            let ab = Double(anchor.hint & 0xFF)
            let d = (r - ar) * (r - ar) + (g - ag) * (g - ag) + (b - ab) * (b - ab)
            if d < best.distance {
                best = (anchor.entry, d)
            }
        }
        return best.entry
    }
}
