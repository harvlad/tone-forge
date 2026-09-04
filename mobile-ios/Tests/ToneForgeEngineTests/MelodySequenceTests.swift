// MelodySequenceTests.swift
//
// Cursor / slicing / pad-mapping parity tests for the melody
// sequence. The cursor and padForMidi cases mirror launchpad.js
// (onMelodyPosition / _padForMidi) — if you change expectations here,
// change the JS in the same commit.

import XCTest
@testable import ToneForgeEngine

final class MelodySequenceTests: XCTestCase {

    private func note(_ pitch: Int, _ start: Double, _ end: Double, vel: Int = 90) -> BundleMelodyNote {
        BundleMelodyNote(start: start, end: end, pitch: pitch, velocity: vel)
    }

    private func sequence(_ notes: [BundleMelodyNote],
                          phrases: [BundleMelodyPhrase] = []) -> MelodySequence {
        MelodySequence(bundle: BundleMelody(
            sourceStem: "vocals", confidence: 0.8, notes: notes, phrases: phrases
        ))!
    }

    // MARK: Decode

    func testBundleDecodesWithAndWithoutMelody() throws {
        let base = """
        {"bundleVersion":2,"analysisId":"a1",
         "meta":{"title":"t","artist":"a","sourceUrl":"","durationSec":10},
         "timeline":{"chords":[],"sections":[],"beats":[],"downbeats":[]},
         "stems":[],"presets":{}%@}
        """
        let without = base.replacingOccurrences(of: "%@", with: "")
        let with = base.replacingOccurrences(of: "%@", with: """
        ,"melody":{"sourceStem":"vocals","confidence":0.7,
          "notes":[{"start":0.0,"end":0.5,"pitch":60,"velocity":100}],
          "phrases":[{"start":0.0,"end":4.0,"sectionLabel":"verse","noteCount":1}]}
        """)
        let decoder = JSONDecoder()
        let b1 = try decoder.decode(SongBundle.self, from: Data(without.utf8))
        XCTAssertNil(b1.melody)
        let b2 = try decoder.decode(SongBundle.self, from: Data(with.utf8))
        XCTAssertEqual(b2.melody?.sourceStem, "vocals")
        XCTAssertEqual(b2.melody?.notes.count, 1)
        XCTAssertEqual(b2.melody?.phrases.first?.sectionLabel, "verse")
    }

    func testEmptyMelodyYieldsNilSequence() {
        XCTAssertNil(MelodySequence(bundle: nil))
        XCTAssertNil(MelodySequence(bundle: BundleMelody(
            sourceStem: "vocals", confidence: 0.5, notes: [], phrases: []
        )))
    }

    // MARK: Cursor (parity with launchpad.js onMelodyPosition)

    func testCursorInsideNoteGapAndEnd() {
        let seq = sequence([
            note(60, 0.0, 0.5),
            note(62, 1.0, 1.5),
            note(64, 2.0, 2.5),
        ])
        // Inside first note.
        XCTAssertEqual(seq.cursor(at: 0.25), MelodyCursor(nowIndex: 0, nextIndex: 1))
        // In the gap after it.
        XCTAssertEqual(seq.cursor(at: 0.75), MelodyCursor(nowIndex: nil, nextIndex: 1))
        // Before the first onset.
        XCTAssertEqual(seq.cursor(at: -1.0), MelodyCursor(nowIndex: nil, nextIndex: 0))
        // Inside the last note.
        XCTAssertEqual(seq.cursor(at: 2.25), MelodyCursor(nowIndex: 2, nextIndex: nil))
        // Past the end.
        XCTAssertEqual(seq.cursor(at: 9.0), MelodyCursor(nowIndex: nil, nextIndex: nil))
    }

    // MARK: Phrase slicing

    func testPhraseSlicingByOnset() {
        let phrase = BundleMelodyPhrase(start: 1.0, end: 2.0, sectionLabel: "chorus", noteCount: 1)
        let seq = sequence(
            [note(60, 0.0, 0.5), note(62, 1.0, 1.5), note(64, 2.0, 2.5)],
            phrases: [phrase]
        )
        let sliced = seq.notes(in: phrase)
        XCTAssertEqual(sliced.map(\.pitch), [62])
        // Onset-based: a note starting exactly at `end` belongs to the
        // NEXT phrase, matching the backend's [start, end) rule.
        XCTAssertEqual(seq.notes(from: 2.0, to: 3.0).map(\.pitch), [64])
    }

    // MARK: padForMidi (parity with launchpad.js _padForMidi)

    func testPadForMidiCornersAndMiddlePreference() {
        XCTAssertEqual(MelodySequence.padForMidi(40)?.row, 0)  // E2 bottom-left
        XCTAssertEqual(MelodySequence.padForMidi(40)?.col, 0)
        XCTAssertEqual(MelodySequence.padForMidi(82)?.row, 7)  // top-right
        XCTAssertEqual(MelodySequence.padForMidi(82)?.col, 7)
        // Middle C: duplicated across rows — nearest-middle row wins.
        let c4 = MelodySequence.padForMidi(60)
        XCTAssertEqual(c4?.row, 3)
        XCTAssertEqual(c4?.col, 5)
        // Above the grid (C6 = 84) octave-shifts down to 72.
        let c6 = MelodySequence.padForMidi(84)
        XCTAssertEqual(c6?.row, 5)
        XCTAssertEqual(c6?.col, 7)
        // Below the grid octave-shifts up.
        XCTAssertNotNil(MelodySequence.padForMidi(28))
    }

    // MARK: Player edges

    @MainActor
    private final class RecordingVoice: MelodyVoice {
        var events: [String] = []
        func noteOn(midi: Int, velocity: Float) { events.append("on:\(midi)") }
        func noteOff(midi: Int) { events.append("off:\(midi)") }
        func allNotesOff() { events.append("allOff") }
    }

    @MainActor
    func testPlayerEmitsEdgeEventsAndStopSilences() {
        let seq = sequence([note(60, 0.0, 0.5), note(62, 1.0, 1.5)])
        let voice = RecordingVoice()
        let player = MelodySequencePlayer(sequence: seq, voice: voice)
        player.advance(to: 0.1)          // enter note 0
        player.advance(to: 0.2)          // still note 0 — no new events
        player.advance(to: 0.7)          // gap — note 0 off
        player.advance(to: 1.1)          // enter note 1
        player.stop()                     // silence the sounding note
        player.stop()                     // idempotent — no extra events
        XCTAssertEqual(voice.events, ["on:60", "off:60", "on:62", "off:62"])
    }

    @MainActor
    func testPlayerSeekProducesSingleEdgePair() {
        let seq = sequence([note(60, 0.0, 4.0), note(62, 5.0, 6.0)])
        let voice = RecordingVoice()
        let player = MelodySequencePlayer(sequence: seq, voice: voice)
        player.advance(to: 1.0)
        player.advance(to: 5.5)          // seek across the gap
        XCTAssertEqual(voice.events, ["on:60", "off:60", "on:62"])
    }
}
