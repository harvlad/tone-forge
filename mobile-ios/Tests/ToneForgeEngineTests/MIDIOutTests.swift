// MIDIOutTests.swift
//
// MIDI OUTPUT byte encoders + 24 PPQN clock-pulse math.

import XCTest
@testable import ToneForgeEngine

final class MIDIOutTests: XCTestCase {

    // MARK: - Encoders

    func testRealtimeBytes() {
        XCTAssertEqual(MIDIOutEncoder.clock, [0xF8])
        XCTAssertEqual(MIDIOutEncoder.start, [0xFA])
        XCTAssertEqual(MIDIOutEncoder.continue, [0xFB])
        XCTAssertEqual(MIDIOutEncoder.stop, [0xFC])
    }

    func testNoteEncoders() {
        XCTAssertEqual(MIDIOutEncoder.noteOn(channel: 0, note: 60, velocity: 100),
                       [0x90, 60, 100])
        XCTAssertEqual(MIDIOutEncoder.noteOff(channel: 1, note: 64),
                       [0x81, 64, 0])
        // Channel + data masking (channel 16 wraps to 0x0F; note > 127 masks).
        XCTAssertEqual(MIDIOutEncoder.noteOn(channel: 0xFF, note: 200, velocity: 200),
                       [0x9F, 200 & 0x7F, 200 & 0x7F])
        XCTAssertEqual(MIDIOutEncoder.controlChange(channel: 0, controller: 7, value: 127),
                       [0xB0, 7, 127])
    }

    // MARK: - Clock

    func testPulseDuration() {
        let g = MIDIClockGenerator(beatClock: BeatClock(tempoBpm: 120))  // beat 0.5 s
        XCTAssertEqual(g.pulseDuration!, 0.5 / 24, accuracy: 1e-12)
    }

    func testTwentyFourPulsesPerBeat() {
        let g = MIDIClockGenerator(beatClock: BeatClock(tempoBpm: 120))
        // One beat (0.5 s) → exactly 24 clock pulses.
        XCTAssertEqual(g.pulsesToEmit(fromSongSec: 0, toSongSec: 0.5), 24)
        // One bar (4 beats) → 96.
        XCTAssertEqual(g.pulsesToEmit(fromSongSec: 0, toSongSec: 2.0), 96)
    }

    func testPartialSpanCrossesBoundaries() {
        let g = MIDIClockGenerator(beatClock: BeatClock(tempoBpm: 120))
        let pulse = 0.5 / 24
        // Span covering 3 pulse boundaries.
        XCTAssertEqual(g.pulsesToEmit(fromSongSec: 0, toSongSec: pulse * 3 + 1e-6), 3)
        // Sub-pulse span → no boundary crossed.
        XCTAssertEqual(g.pulsesToEmit(fromSongSec: 0, toSongSec: pulse * 0.4), 0)
    }

    func testBackwardSeekEmitsNothing() {
        let g = MIDIClockGenerator(beatClock: BeatClock(tempoBpm: 120))
        XCTAssertEqual(g.pulsesToEmit(fromSongSec: 1.0, toSongSec: 0.5), 0)
    }

    func testAnchoredOnFirstBeat() {
        // First beat at 0.3 → the pulse grid starts there.
        let g = MIDIClockGenerator(beatClock: BeatClock(tempoBpm: 120, beats: [0.3, 0.8]))
        // 0.3 → 0.8 is one beat → 24 pulses.
        XCTAssertEqual(g.pulsesToEmit(fromSongSec: 0.3, toSongSec: 0.8), 24)
    }

    func testNoTempoNoPulses() {
        let g = MIDIClockGenerator(beatClock: BeatClock())
        XCTAssertNil(g.pulseDuration)
        XCTAssertNil(g.pulseIndex(atSongSec: 1.0))
        XCTAssertEqual(g.pulsesToEmit(fromSongSec: 0, toSongSec: 5), 0)
    }
}
