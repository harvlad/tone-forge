// SequenceRecorderTests.swift
//
// Unit tests for the 4x4 Launchpad SequenceRecorder. Pins the tap→step
// quantization (Snap floors via the clock; Free rounds to the nearest
// 16th), source-qualified multi-track building, silent-track pruning on
// finalize, loop-length→stepCount resolution, the three sources
// (pads / songChords / keyChords), and the song-clock vs internal-clock
// bpmOverride behaviour on finalize.
//
// At 120 BPM a 16th step is 0.125 s (SequencerClock: 1 / (bpm/60 * 4)).

import XCTest
@testable import ToneForgeMobile
@testable import ToneForgeEngine

@MainActor
final class SequenceRecorderTests: XCTestCase {

    private static let keySymbols = ["C", "Dm", "Em", "F", "G", "Am", "Bdim"]

    // MARK: - Helpers

    /// Active step indices across all tracks (recorder builds one track
    /// per tapped source-cell, so this reflects the tapped cells' hits).
    private func activeSteps(_ recorder: SequenceRecorder) -> [Int] {
        recorder.pattern.tracks.flatMap { track in
            track.steps.enumerated().compactMap { $0.element.isActive ? $0.offset : nil }
        }
    }

    /// Recorder configured on the songChords source (16 chops).
    private func makeSongChordsRecorder(
        loop: SequenceRecorder.LoopLength = .oneBar
    ) -> SequenceRecorder {
        let r = SequenceRecorder()
        r.configure(
            loopLength: loop,
            songBPM: 120,
            sectionSteps: 16,
            packId: "starter",
            padCount: 16,
            songChordCount: 16,
            keyChordSymbols: Self.keySymbols,
            initialSource: .songChords
        )
        return r
    }

    // MARK: - Quantized capture

    func testQuantizedCaptureFloorsToStep() {
        let r = makeSongChordsRecorder()
        r.captureMode = .quantized
        r.startRecording(atSongSeconds: 0)
        // 0.35 s / 0.125 = 2.8 → floor → step 2.
        r.capture(cell: 5, atSongSeconds: 0.35)

        XCTAssertEqual(activeSteps(r), [2])
        XCTAssertTrue(r.hasSteps(cell: 5))
    }

    // MARK: - Free capture

    func testFreeCaptureRoundsToNearestStep() {
        let r = makeSongChordsRecorder()
        r.captureMode = .free
        r.startRecording(atSongSeconds: 0)
        // 0.35 s / 0.125 = 2.8 → round → step 3 (vs quantized's 2).
        r.capture(cell: 5, atSongSeconds: 0.35)

        XCTAssertEqual(activeSteps(r), [3])
    }

    func testFreeRoundsDownJustAfterBoundary() {
        let r = makeSongChordsRecorder()
        r.captureMode = .free
        r.startRecording(atSongSeconds: 0)
        // Half-step boundary is 0.0625 s. Just before → rounds to 0.
        r.capture(cell: 0, atSongSeconds: 0.05)

        XCTAssertEqual(activeSteps(r), [0])
    }

    func testFreeRoundsUpJustBeforeNextBoundary() {
        let r = makeSongChordsRecorder()
        r.captureMode = .free
        r.startRecording(atSongSeconds: 0)
        // 0.08 s / 0.125 = 0.64 → rounds to 1.
        r.capture(cell: 0, atSongSeconds: 0.08)

        XCTAssertEqual(activeSteps(r), [1])
    }

    // MARK: - Multi-cell → multi-track

    func testDistinctCellsBecomeSeparateTracks() {
        let r = makeSongChordsRecorder()
        r.captureMode = .quantized
        r.startRecording(atSongSeconds: 0)
        r.capture(cell: 0, atSongSeconds: 0.0)     // step 0
        r.capture(cell: 5, atSongSeconds: 0.125)   // step 1
        r.capture(cell: 5, atSongSeconds: 0.25)    // step 2, same cell

        XCTAssertEqual(r.pattern.tracks.count, 2)
        XCTAssertTrue(r.hasSteps(cell: 0))
        XCTAssertTrue(r.hasSteps(cell: 5))
    }

    /// The same cell tapped under two different sources builds two
    /// distinct tracks (source-qualified keying).
    func testSameCellDifferentSourcesBecomeSeparateTracks() {
        let r = makeSongChordsRecorder()
        r.captureMode = .quantized
        r.startRecording(atSongSeconds: 0)
        r.capture(cell: 0, atSongSeconds: 0.0)     // songChords cell 0

        r.setSource(.keyChords)
        r.capture(cell: 0, atSongSeconds: 0.125)   // keyChords cell 0

        XCTAssertEqual(r.pattern.tracks.count, 2)
        // hasSteps is source-qualified: only the active source's cell 0.
        XCTAssertTrue(r.hasSteps(cell: 0))         // keyChords active
        r.setSource(.songChords)
        XCTAssertTrue(r.hasSteps(cell: 0))         // songChords also present
    }

    func testOutOfRangeCellDoesNotRecord() {
        let r = SequenceRecorder()
        r.configure(
            loopLength: .oneBar,
            songBPM: 120,
            sectionSteps: 16,
            packId: nil,
            padCount: 0,
            songChordCount: 4,      // only cells 0..3 valid
            keyChordSymbols: [],
            initialSource: .songChords
        )
        r.startRecording(atSongSeconds: 0)
        r.capture(cell: 9, atSongSeconds: 0.0)     // beyond songChordCount

        XCTAssertTrue(r.pattern.tracks.isEmpty)
        XCTAssertTrue(r.isEmpty)
    }

    func testCaptureIgnoredWhenNotRecording() {
        let r = makeSongChordsRecorder()
        r.capture(cell: 0, atSongSeconds: 0.0)     // no startRecording

        XCTAssertTrue(r.isEmpty)
    }

    // MARK: - Finalize

    func testFinalizePrunesSilentTracksAndSetsLooping() {
        let r = makeSongChordsRecorder()
        r.startRecording(atSongSeconds: 0)
        r.capture(cell: 0, atSongSeconds: 0.0)
        let final = r.finalize(name: "Arp")

        XCTAssertEqual(final.name, "Arp")
        XCTAssertTrue(final.isLooping)
        XCTAssertEqual(final.tracks.count, 1)
        XCTAssertTrue(final.tracks.allSatisfy { t in t.steps.contains { $0.isActive } })
    }

    func testFinalizeEmptyNameFallsBack() {
        let r = makeSongChordsRecorder()
        r.startRecording(atSongSeconds: 0)
        r.capture(cell: 0, atSongSeconds: 0.0)

        XCTAssertEqual(r.finalize(name: "").name, "Sequence")
    }

    /// Song-clock sessions leave bpmOverride nil (follow song); internal
    /// clock sessions bake the tempo so playback needs no song.
    func testFinalizeBpmOverrideBySession() {
        let songSynced = makeSongChordsRecorder()
        songSynced.startRecording(useSongClock: true, atSongSeconds: 0, bpm: 120)
        songSynced.capture(cell: 0, atSongSeconds: 0.0)
        XCTAssertNil(songSynced.finalize(name: "S").bpmOverride)

        let standalone = makeSongChordsRecorder()
        standalone.setSource(.keyChords)
        standalone.startRecording(useSongClock: false, atSongSeconds: 0, bpm: 100)
        standalone.capture(cell: 0, atSongSeconds: 0.0)
        XCTAssertEqual(standalone.finalize(name: "K").bpmOverride, 100)
    }

    func testClearRemovesAllHits() {
        let r = makeSongChordsRecorder()
        r.startRecording(atSongSeconds: 0)
        r.capture(cell: 0, atSongSeconds: 0.0)
        XCTAssertFalse(r.isEmpty)

        r.clear()
        XCTAssertTrue(r.isEmpty)
        XCTAssertFalse(r.hasSteps(cell: 0))
    }

    // MARK: - chopRef

    func testChopRefSongChords() {
        let r = makeSongChordsRecorder()
        guard case .bundleChop(let preset, let idx, _)? = r.chopRef(forCell: 3) else {
            return XCTFail("Expected bundleChop for songChords source")
        }
        XCTAssertEqual(preset, "harmonic")
        XCTAssertEqual(idx, 3)
    }

    func testChopRefPads() {
        let r = makeSongChordsRecorder()
        r.setSource(.pads)
        guard case .packPad(let packId, let padIdx)? = r.chopRef(forCell: 7) else {
            return XCTFail("Expected packPad for pads source")
        }
        XCTAssertEqual(packId, "starter")
        XCTAssertEqual(padIdx, 7)
    }

    func testChopRefKeyChords() {
        let r = makeSongChordsRecorder()
        r.setSource(.keyChords)
        guard case .synthChord(let symbol, _)? = r.chopRef(forCell: 1) else {
            return XCTFail("Expected synthChord for keyChords source")
        }
        XCTAssertEqual(symbol, Self.keySymbols[1])   // "Dm"
    }

    func testChopRefNilBeyondCellCount() {
        let r = SequenceRecorder()
        r.configure(
            loopLength: .oneBar,
            songBPM: 120,
            sectionSteps: 16,
            packId: nil,
            padCount: 0,
            songChordCount: 4,
            keyChordSymbols: [],
            initialSource: .songChords
        )
        XCTAssertNil(r.chopRef(forCell: 4))
        XCTAssertNil(r.chopRef(forCell: -1))
    }

    // MARK: - Loop length → stepCount

    func testLoopLengthStepCounts() {
        XCTAssertEqual(
            SequenceRecorder.stepCount(for: .oneBar, sectionSteps: 16), .sixteen)
        XCTAssertEqual(
            SequenceRecorder.stepCount(for: .twoBar, sectionSteps: 16), .thirtyTwo)
        // Section clamps into {16, 32}.
        XCTAssertEqual(
            SequenceRecorder.stepCount(for: .section, sectionSteps: 16), .sixteen)
        XCTAssertEqual(
            SequenceRecorder.stepCount(for: .section, sectionSteps: 17), .thirtyTwo)
        XCTAssertEqual(
            SequenceRecorder.stepCount(for: .section, sectionSteps: 32), .thirtyTwo)
    }

    func testConfigureAppliesStepCount() {
        let one = makeSongChordsRecorder(loop: .oneBar)
        XCTAssertEqual(one.pattern.stepCount, .sixteen)

        let two = makeSongChordsRecorder(loop: .twoBar)
        XCTAssertEqual(two.pattern.stepCount, .thirtyTwo)
    }

    // MARK: - Load existing (in-place editing)

    /// Build a saved pattern, then load it into a fresh recorder and
    /// confirm the id, tracks, and loop length survive.
    func testLoadPreservesIdAndTracks() {
        let author = makeSongChordsRecorder()
        author.startRecording(atSongSeconds: 0)
        author.capture(cell: 0, atSongSeconds: 0.0)
        author.capture(cell: 5, atSongSeconds: 0.125)
        let saved = author.finalize(name: "Loop")

        let editor = makeSongChordsRecorder()
        editor.load(saved)

        XCTAssertEqual(editor.pattern.id, saved.id)       // updates in place
        XCTAssertEqual(editor.pattern.tracks.count, 2)
        XCTAssertEqual(editor.loopLength, .oneBar)
        // Reverse-mapped: songChords cells 0 and 5 show as recorded.
        XCTAssertTrue(editor.hasSteps(cell: 0))
        XCTAssertTrue(editor.hasSteps(cell: 5))
    }

    /// Loading sets activeSource from the first track's ChopReference.
    func testLoadPicksSourceFromFirstTrack() {
        let author = makeSongChordsRecorder()
        author.setSource(.keyChords)
        author.startRecording(atSongSeconds: 0)
        author.capture(cell: 2, atSongSeconds: 0.0)       // synthChord "Em"
        let saved = author.finalize(name: "Chords")

        let editor = makeSongChordsRecorder()             // starts on songChords
        editor.load(saved)

        XCTAssertEqual(editor.activeSource, .keyChords)
        XCTAssertTrue(editor.hasSteps(cell: 2))
    }

    /// After loading, an existing cell overdubs into the SAME track (no
    /// duplicate) while a new cell adds a track.
    func testCaptureAtStepReusesLoadedTrack() {
        let author = makeSongChordsRecorder()
        author.startRecording(atSongSeconds: 0)
        author.capture(cell: 0, atSongSeconds: 0.0)       // step 0
        let saved = author.finalize(name: "L")

        let editor = makeSongChordsRecorder()
        editor.load(saved)
        editor.captureAtStep(cell: 0, step: 4)            // same cell, new step
        editor.captureAtStep(cell: 6, step: 8)            // new cell

        XCTAssertEqual(editor.pattern.tracks.count, 2)    // 1 reused + 1 new
        XCTAssertEqual(Set(activeSteps(editor)), [0, 4, 8])
    }

    /// captureAtStep wraps out-of-range step indices into the loop.
    func testCaptureAtStepWrapsIndex() {
        let r = makeSongChordsRecorder()                  // 16 steps
        r.captureAtStep(cell: 0, step: 17)                // 17 % 16 = 1

        XCTAssertEqual(activeSteps(r), [1])
    }

    /// A loaded pattern with a baked bpmOverride re-finalizes with the
    /// same override (standalone playback preserved through an edit).
    func testLoadPreservesStandaloneBpmOverride() {
        let author = makeSongChordsRecorder()
        author.setSource(.keyChords)
        author.startRecording(useSongClock: false, atSongSeconds: 0, bpm: 96)
        author.capture(cell: 0, atSongSeconds: 0.0)
        let saved = author.finalize(name: "K")
        XCTAssertEqual(saved.bpmOverride, 96)

        let editor = makeSongChordsRecorder()
        editor.load(saved)

        XCTAssertEqual(editor.finalize(name: "K").bpmOverride, 96)
    }
}
