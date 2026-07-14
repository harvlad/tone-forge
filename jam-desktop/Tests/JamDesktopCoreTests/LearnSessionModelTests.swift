// LearnSessionModelTests.swift
//
// Practice session state: press recording, hit/miss scoring, streak
// folding, pass completion + persistence.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class LearnSessionModelTests: XCTestCase {

    private var tempDir: URL!
    private var store: LearnProgressStore!
    private var model: LearnSessionModel!

    override func setUp() async throws {
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("learn-tests-\(UUID())", isDirectory: true)
        try FileManager.default.createDirectory(
            at: tempDir, withIntermediateDirectories: true)
        store = LearnProgressStore(root: tempDir)
        model = LearnSessionModel(progressStore: store)
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: tempDir)
    }

    // MARK: - Section configuration

    func testUniqueSectionsDedupesByLabel() {
        let sections = [
            SectionEvent(start: 0, end: 10, label: "Verse"),
            SectionEvent(start: 10, end: 20, label: "Chorus"),
            SectionEvent(start: 20, end: 30, label: "verse"),  // same key
            SectionEvent(start: 30, end: 40, label: "Chorus"), // dupe
        ]
        model.configure(sections: sections, chords: [], analysisId: "a1")
        XCTAssertEqual(model.uniqueSections.count, 2)
        XCTAssertEqual(model.uniqueSections[0].label, "Verse")
        XCTAssertEqual(model.uniqueSections[1].label, "Chorus")
    }

    func testSongChordsFirstAppearanceOrder() {
        let chords = [
            ChordEvent(start: 0, end: 2, symbol: "C"),
            ChordEvent(start: 2, end: 4, symbol: "Am"),
            ChordEvent(start: 4, end: 6, symbol: "C"),  // repeat
            ChordEvent(start: 6, end: 8, symbol: "G"),
        ]
        model.configure(sections: [], chords: chords, analysisId: "a1")
        XCTAssertEqual(model.songChords, ["C", "Am", "G"])
    }

    // MARK: - Practice session

    func testStartSectionSetsPracticingPhase() {
        let section = SectionEvent(start: 10, end: 20, label: "Intro")
        model.configure(sections: [section], chords: [], analysisId: "a1")
        model.startSection(section)
        XCTAssertEqual(model.phase, .practicing)
        XCTAssertEqual(model.activeSection?.label, "Intro")
        XCTAssertNotNil(model.loopRegion)
        XCTAssertEqual(model.loopRegion?.inSeconds, 10)
    }

    func testRecordPressHitIncrementsCounter() {
        let section = SectionEvent(start: 0, end: 10, label: "A")
        let chords = [ChordEvent(start: 0, end: 10, symbol: "C")]
        model.configure(sections: [section], chords: chords, analysisId: "a1")
        model.startSection(section)

        model.recordPress(symbol: "C", atTime: 5.0)
        XCTAssertEqual(model.passHits, 1)
        XCTAssertEqual(model.passMisses, 0)
        XCTAssertEqual(model.lastPressHit, true)
        XCTAssertEqual(model.currentStreak, 1)
    }

    func testRecordPressMissResetsStreak() {
        let section = SectionEvent(start: 0, end: 10, label: "A")
        let chords = [ChordEvent(start: 0, end: 10, symbol: "C")]
        model.configure(sections: [section], chords: chords, analysisId: "a1")
        model.startSection(section)

        model.recordPress(symbol: "C", atTime: 2.0)
        model.recordPress(symbol: "C", atTime: 3.0)
        XCTAssertEqual(model.currentStreak, 2)
        XCTAssertEqual(model.sessionLongestStreak, 2)

        model.recordPress(symbol: "Dm", atTime: 4.0)  // miss
        XCTAssertEqual(model.currentStreak, 0)
        XCTAssertEqual(model.sessionLongestStreak, 2)  // preserved
        XCTAssertEqual(model.passMisses, 1)
    }

    func testPassCompletedScoresAndPersists() {
        let section = SectionEvent(start: 0, end: 10, label: "A")
        let chords = [ChordEvent(start: 0, end: 10, symbol: "C")]
        model.configure(sections: [section], chords: chords, analysisId: "a1")
        model.startSection(section)

        model.recordPress(symbol: "C", atTime: 5.0)
        model.passCompleted()

        XCTAssertNotNil(model.lastPassResult)
        XCTAssertEqual(model.lastPassResult?.hits, 1)
        XCTAssertEqual(model.lastPassResult?.coverage, 1.0)
        // Pass counters reset for next pass
        XCTAssertEqual(model.passHits, 0)
        XCTAssertEqual(model.passMisses, 0)

        // Persisted progress reflects the pass
        let loaded = store.load(analysisId: "a1")
        XCTAssertNotNil(loaded)
        XCTAssertEqual(loaded?.sections["a"]?.passCount, 1)
    }

    func testStopPracticePersistsStreak() {
        let section = SectionEvent(start: 0, end: 10, label: "A")
        let chords = [ChordEvent(start: 0, end: 10, symbol: "C")]
        model.configure(sections: [section], chords: chords, analysisId: "a1")
        model.startSection(section)

        model.recordPress(symbol: "C", atTime: 2.0)
        model.recordPress(symbol: "C", atTime: 3.0)
        model.recordPress(symbol: "C", atTime: 4.0)
        model.stopPractice()

        XCTAssertEqual(model.phase, .idle)
        XCTAssertNil(model.activeSection)

        let loaded = store.load(analysisId: "a1")
        XCTAssertEqual(loaded?.longestStreak, 3)
    }

    func testOnPlayChordCallback() {
        let section = SectionEvent(start: 0, end: 10, label: "A")
        model.configure(sections: [section], chords: [], analysisId: "a1")
        model.startSection(section)

        var played: [String] = []
        model.onPlayChord = { played.append($0) }
        model.recordPress(symbol: "G", atTime: 5.0)
        XCTAssertEqual(played, ["G"])
    }

    // MARK: - Progress tracking

    func testIsLearnedAfterPassingPass() {
        let section = SectionEvent(start: 0, end: 10, label: "Chorus")
        // 80% accuracy threshold: 4 hits = 80%
        let chords = [ChordEvent(start: 0, end: 10, symbol: "C")]
        model.configure(sections: [section], chords: chords, analysisId: "a1")
        model.startSection(section)

        // One hit on the only chord window = 100% coverage, 100% accuracy
        model.recordPress(symbol: "C", atTime: 5.0)
        model.passCompleted()

        XCTAssertTrue(model.isLearned(section))
        XCTAssertEqual(model.learnedCount, 1)
    }

    func testNextUpSectionSkipsLearned() {
        let sec1 = SectionEvent(start: 0, end: 10, label: "Verse")
        let sec2 = SectionEvent(start: 10, end: 20, label: "Chorus")
        let chords = [
            ChordEvent(start: 0, end: 10, symbol: "C"),
            ChordEvent(start: 10, end: 20, symbol: "Am"),
        ]
        model.configure(sections: [sec1, sec2], chords: chords, analysisId: "a1")

        // Learn first section
        model.startSection(sec1)
        model.recordPress(symbol: "C", atTime: 5.0)
        model.passCompleted()
        model.stopPractice()

        XCTAssertEqual(model.nextUpSection?.label, "Chorus")
    }

    // MARK: - Chord prediction

    func testPredictionProgress() {
        let chords = [
            ChordEvent(start: 0, end: 4, symbol: "C"),
            ChordEvent(start: 4, end: 8, symbol: "G"),
        ]
        model.configure(sections: [], chords: chords, analysisId: "a1")

        let current = chords[0]
        // At time 2.0, halfway through a 4-second chord
        let pred = model.prediction(atTime: 2.0, currentChord: current)
        XCTAssertNotNil(pred)
        XCTAssertEqual(pred?.currentSymbol, "C")
        XCTAssertEqual(pred?.nextSymbol, "G")
        XCTAssertEqual(pred?.remainingSec ?? -1, 2.0, accuracy: 0.01)
        XCTAssertEqual(pred?.progress ?? -1, 0.5, accuracy: 0.01)
        XCTAssertFalse(pred?.imminent ?? true)
    }

    func testPredictionImminentLast600ms() {
        let chords = [
            ChordEvent(start: 0, end: 4, symbol: "C"),
            ChordEvent(start: 4, end: 8, symbol: "G"),
        ]
        model.configure(sections: [], chords: chords, analysisId: "a1")

        let pred = model.prediction(atTime: 3.5, currentChord: chords[0])
        XCTAssertEqual(pred?.remainingSec ?? -1, 0.5, accuracy: 0.01)
        XCTAssertTrue(pred?.imminent ?? false)
    }
}
