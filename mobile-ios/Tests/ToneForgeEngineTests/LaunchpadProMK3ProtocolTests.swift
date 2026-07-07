// LaunchpadProMK3ProtocolTests.swift
//
// Byte-for-byte verification of the Launchpad Pro [MK3] protocol
// layer against the official Programmer's reference manual
// (LPP3_prog_ref_guide_200415). Citations use `// PDF p.<n>`; grid
// note numbers are additionally confirmed by the User Guide appendix
// A.1.9 (p.63). The SysEx example test reproduces the manual's own
// worked example verbatim.

import XCTest
@testable import ToneForgeEngine

final class LaunchpadProMK3ProtocolTests: XCTestCase {

    typealias LP = LaunchpadProMK3Protocol

    // MARK: - SysEx constants

    func testSysExHeader() {
        // "All SysEx messages begin with the following header":
        // F0h 00h 20h 29h 02h 0Eh. // PDF p.6
        XCTAssertEqual(LP.sysExHeader, [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0E])
    }

    func testProgrammerAndLiveModeSelect() {
        // Command 0Eh, <Mode> 1 = Programmer, 0 = Live. // PDF p.18
        XCTAssertEqual(
            LP.enterProgrammerMode,
            [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0E, 0x0E, 0x01, 0xF7]
        )
        XCTAssertEqual(
            LP.enterLiveMode,
            [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0E, 0x0E, 0x00, 0xF7]
        )
    }

    func testPortName() {
        // The Programmer-mode interface is "LPProMK3 MIDI". // PDF p.6
        // (Confirmed against the physical device via CoreMIDI.)
        XCTAssertEqual(LP.midiPortName, "LPProMK3 MIDI")
        XCTAssertEqual(LP.deviceNameFragment, "Launchpad Pro MK3")
    }

    // MARK: - Grid note mapping

    func testGridNoteToPadMapping() {
        // Programmer mode grid: notes 11–88, note = 10·row + col,
        // row 1 = bottom. // PDF p.19; User Guide p.63 (A.1.9)
        XCTAssertEqual(LP.padIndex(forNote: 11), PadIndex.at(row: 1, col: 1))
        XCTAssertEqual(LP.padIndex(forNote: 18), PadIndex.at(row: 1, col: 8))
        XCTAssertEqual(LP.padIndex(forNote: 81), PadIndex.at(row: 8, col: 1))
        XCTAssertEqual(LP.padIndex(forNote: 88), PadIndex.at(row: 8, col: 8))
        XCTAssertEqual(LP.padIndex(forNote: 45), PadIndex.at(row: 4, col: 5))
    }

    func testAllSixtyFourPadsRoundTrip() {
        for row in 1...8 {
            for col in 1...8 {
                let pad = PadIndex.at(row: row, col: col)
                let note = LP.note(for: pad)
                XCTAssertEqual(Int(note), row * 10 + col)
                XCTAssertEqual(LP.padIndex(forNote: note), pad)
            }
        }
    }

    func testInvalidNotesRejected() {
        // Values outside 11–88 or with col 0/9 are outer buttons or
        // nonexistent — never grid pads. // PDF p.19
        for note: UInt8 in [0, 1, 8, 9, 10, 19, 20, 89, 90, 99, 101, 108, 127] {
            XCTAssertNil(LP.padIndex(forNote: note), "note \(note)")
        }
    }

    // MARK: - Outer buttons

    func testControlButtonMapping() {
        // Programmer mode layout CCs. // PDF p.19
        XCTAssertEqual(LP.controlButton(forCC: 90), .shift)
        XCTAssertEqual(LP.controlButton(forCC: 99), .logo)
        XCTAssertEqual(LP.controlButton(forCC: 10), .left(row: 1))
        XCTAssertEqual(LP.controlButton(forCC: 80), .left(row: 8))
        XCTAssertEqual(LP.controlButton(forCC: 19), .right(row: 1))
        XCTAssertEqual(LP.controlButton(forCC: 89), .right(row: 8))
        XCTAssertEqual(LP.controlButton(forCC: 91), .top(col: 1))
        XCTAssertEqual(LP.controlButton(forCC: 98), .top(col: 8))
        XCTAssertEqual(LP.controlButton(forCC: 101), .trackSelect(col: 1))
        XCTAssertEqual(LP.controlButton(forCC: 108), .trackSelect(col: 8))
        XCTAssertEqual(LP.controlButton(forCC: 1), .trackControl(col: 1))
        XCTAssertEqual(LP.controlButton(forCC: 8), .trackControl(col: 8))
    }

    func testControlButtonRejectsGridAndUnknownCCs() {
        // Grid indices (11–88) and out-of-map values are not outer
        // buttons. // PDF p.19
        for cc: UInt8 in [0, 9, 11, 45, 88, 100, 109, 110, 127] {
            XCTAssertNil(LP.controlButton(forCC: cc), "cc \(cc)")
        }
    }

    // MARK: - LED lighting SysEx

    func testManualWorkedExample() {
        // The manual's own example: static yellow on pad 11, flashing
        // green (B=21, A=23) on pad 12, pulsing turquoise on pad 13:
        // F0h 00h 20h 29h 02h 0Eh 03h 00h 0Bh 0Dh 01h 0Ch 15h 17h
        // 02h 0Dh 25h F7h. // PDF p.13
        let messages = LP.ledMessages([
            .staticPalette(pad: PadIndex(11), palette: 13),
            .flash(pad: PadIndex(12), paletteB: 21, paletteA: 23),
            .pulse(pad: PadIndex(13), palette: 37),
        ])
        XCTAssertEqual(messages.count, 1)
        XCTAssertEqual(
            messages[0],
            [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0E, 0x03,
             0x00, 0x0B, 0x0D,
             0x01, 0x0C, 0x15, 0x17,
             0x02, 0x0D, 0x25,
             0xF7]
        )
    }

    func testFullGridRedrawIsSingleMessage() {
        // 64 RGB specs fit well inside the 106-spec limit, so a full
        // redraw is exactly one SysEx message. // PDF p.13
        var entries: [(pad: PadIndex, colorHint: UInt32)] = []
        for row in 1...8 {
            for col in 1...8 {
                entries.append((PadIndex.at(row: row, col: col), 0xFF8000))
            }
        }
        let messages = LP.ledRGBBatch(entries)
        XCTAssertEqual(messages.count, 1)
        // Header(6) + command(1) + 64 × 5 bytes + F7 = 328.
        XCTAssertEqual(messages[0].count, 328)
        XCTAssertEqual(Array(messages[0].prefix(7)),
                       LP.sysExHeader + [0x03])
        XCTAssertEqual(messages[0].last, 0xF7)
        // First spec: type 3, pad 11, RGB 0xFF/0x80/0x00 each >>1
        // (127-max wire range). // PDF p.13
        XCTAssertEqual(Array(messages[0][7..<12]),
                       [0x03, 0x0B, 0x7F, 0x40, 0x00])
    }

    func testBatchChunksAtLimit() {
        // 107 specs must split 106 + 1. // PDF p.13
        let specs = (0..<107).map { _ in
            LP.ColorSpec.rgb(pad: PadIndex(11), colorHint: 0xFFFFFF)
        }
        let messages = LP.ledMessages(specs)
        XCTAssertEqual(messages.count, 2)
        XCTAssertEqual(messages[0].count, 7 + 106 * 5 + 1)
        XCTAssertEqual(messages[1].count, 7 + 1 * 5 + 1)
        XCTAssertTrue(messages.allSatisfy { $0.last == 0xF7 })
    }

    func testFlashAndPulseSingleMessages() {
        // Flash: type 01h, 2 bytes, Colour B then Colour A. // PDF p.12
        XCTAssertEqual(
            LP.ledFlash(pad: PadIndex(11), paletteB: 21, paletteA: 23),
            LP.sysExHeader + [0x03, 0x01, 0x0B, 0x15, 0x17, 0xF7]
        )
        // Pulse: type 02h, 1 byte palette entry. // PDF p.12
        XCTAssertEqual(
            LP.ledPulse(pad: PadIndex(13), palette: 37),
            LP.sysExHeader + [0x03, 0x02, 0x0D, 0x25, 0xF7]
        )
    }

    func testNearestPaletteEntry() {
        // Anchors are the manual's prose-named entries. // PDF p.11–13
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0x000000), LP.paletteOff)
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0xFF0000), LP.paletteRed)
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0x00FF00), LP.paletteGreen)
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0x0000FF), LP.paletteBlue)
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0xFFFF00), LP.paletteYellow)
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0x00FFFF), LP.paletteTurquoise)
        // The mic-sample warm orange leans yellow.
        XCTAssertEqual(LP.nearestPaletteEntry(colorHint: 0xFF8C3A), LP.paletteYellow)
    }

    // MARK: - MIDI1Parser

    func testParsesNoteOnOffAndCC() {
        let messages = MIDI1Parser.parse([
            0x90, 0x0B, 0x64,       // noteOn ch1 note 11 vel 100
            0x80, 0x0B, 0x00,       // noteOff ch1 note 11
            0xB0, 0x5A, 0x7F,       // CC 90 (Shift) value 127
        ])
        XCTAssertEqual(messages, [
            .noteOn(channel: 0, note: 11, velocity: 100),
            .noteOff(channel: 0, note: 11, velocity: 0),
            .controlChange(channel: 0, controller: 90, value: 127),
        ])
    }

    func testRunningStatus() {
        // Second note reuses the 90h status byte. The device sends
        // Note On with velocity 0 for Note Off. // PDF p.6
        let messages = MIDI1Parser.parse([
            0x90, 0x0B, 0x64, 0x0C, 0x00,
        ])
        XCTAssertEqual(messages, [
            .noteOn(channel: 0, note: 11, velocity: 100),
            .noteOn(channel: 0, note: 12, velocity: 0),
        ])
    }

    func testSysExSplitAcrossBuffers() {
        var parser = MIDI1Parser()
        let full = LaunchpadProMK3Protocol.enterProgrammerMode
        XCTAssertEqual(parser.consume(Array(full.prefix(4))), [])
        XCTAssertEqual(parser.consume(Array(full.dropFirst(4))), [.sysex(full)])
    }

    func testRealTimeBytesSkippedInsideMessages() {
        // 0xF8 (clock) may interleave anywhere, including mid-SysEx.
        let messages = MIDI1Parser.parse([
            0xF0, 0x00, 0xF8, 0x20, 0x29, 0xF7,
            0xF8,
            0x90, 0xFA, 0x0B, 0x64,
        ])
        XCTAssertEqual(messages, [
            .sysex([0xF0, 0x00, 0x20, 0x29, 0xF7]),
            .noteOn(channel: 0, note: 11, velocity: 100),
        ])
    }

    func testUnconsumedMessageTypesDoNotDesync() {
        // Program change (1 data byte) followed by a note: the note
        // must still parse.
        let messages = MIDI1Parser.parse([
            0xC0, 0x05,
            0xD0, 0x40,
            0x90, 0x0B, 0x64,
        ])
        XCTAssertEqual(messages, [.noteOn(channel: 0, note: 11, velocity: 100)])
    }

    // MARK: - UMP framing

    func testUMPChannelVoiceDecode() {
        var parser = UMPParser()
        let messages = parser.consume([
            0x2090_0B64,    // MT2: noteOn ch1 note 11 vel 100
            0x2080_0B00,    // MT2: noteOff ch1 note 11
            0x20B0_5A7F,    // MT2: CC 90 value 127
        ])
        XCTAssertEqual(messages, [
            .noteOn(channel: 0, note: 11, velocity: 100),
            .noteOff(channel: 0, note: 11, velocity: 0),
            .controlChange(channel: 0, controller: 90, value: 127),
        ])
    }

    func testUMPSysEx7EncodeCompleteMessage() {
        // 2-byte payload fits one packet: status 0 (complete).
        let words = UMPSysEx7.encode([0xF0, 0x01, 0x02, 0xF7])
        XCTAssertEqual(words, [0x3002_0102, 0x0000_0000])
    }

    func testUMPSysEx7RoundTrip() {
        // Programmer-mode select (7-byte payload → start + end
        // packets) survives encode → decode intact.
        let original = LaunchpadProMK3Protocol.enterProgrammerMode
        let words = UMPSysEx7.encode(original)
        XCTAssertEqual(words.count, 4)   // 2 packets × 2 words
        var parser = UMPParser()
        XCTAssertEqual(parser.consume(words), [.sysex(original)])
    }

    func testUMPSysEx7RoundTripLargeMessage() {
        // A full 64-pad RGB redraw (328 bytes) split across many
        // SysEx7 packets — and across two consume() calls, as
        // CoreMIDI may deliver it in separate event packets.
        let entries = (1...8).flatMap { row in
            (1...8).map { col in
                (pad: PadIndex.at(row: row, col: col),
                 colorHint: UInt32(0x336699))
            }
        }
        let message = LaunchpadProMK3Protocol.ledRGBBatch(entries)[0]
        let words = UMPSysEx7.encode(message)
        var parser = UMPParser()
        let half = (words.count / 2) & ~1   // even split (whole packets)
        var out = parser.consume(Array(words.prefix(half)))
        out += parser.consume(Array(words.dropFirst(half)))
        XCTAssertEqual(out, [.sysex(message)])
    }

    func testUMPParserSkipsUnusedMessageTypes() {
        var parser = UMPParser()
        let messages = parser.consume([
            0x1000_0000,             // MT1 system: 1 word, skipped
            0x4090_0B64, 0x0000_0000, // MT4 (MIDI 2.0 voice): 2 words, skipped
            0x2090_0B64,             // real note
        ])
        XCTAssertEqual(messages, [.noteOn(channel: 0, note: 11, velocity: 100)])
    }
}
