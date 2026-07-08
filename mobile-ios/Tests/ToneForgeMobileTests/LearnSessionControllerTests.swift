// LearnSessionControllerTests.swift
//
// Learn practice lifecycle on a headless AppState (redesign
// Phase 8): section keys + uniqueness, start/stop arming the A/B
// loop, press scoring with injected song times, pass completion
// folding into persisted progress, and the loop-wrap hook's mode
// guard. PadSynth triggers post to a lock-free queue, so voicing is
// safe without a booted engine.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class LearnSessionControllerTests: XCTestCase {

    private var app: AppState!
    private var tmpDir: URL!
    private var learnRoot: URL!

    /// verse (Dm Bb C F over 2 s bars), chorus, repeat verse, and an
    /// unlabelled tail section.
    private static let fixtureBundle = SongBundle(
        bundleVersion: 1,
        analysisId: "learn-fixture",
        meta: BundleMeta(
            title: "Learn Song",
            artist: "Fixture Artist",
            sourceUrl: "",
            durationSec: 32.0,
            tempoBpm: 120.0,
            detectedKey: "D minor"
        ),
        timeline: BundleTimeline(
            chords: [
                ChordEvent(start: 0, end: 2, symbol: "Dm"),
                ChordEvent(start: 2, end: 4, symbol: "Bb"),
                ChordEvent(start: 4, end: 6, symbol: "C"),
                ChordEvent(start: 6, end: 8, symbol: "F"),
                ChordEvent(start: 8, end: 12, symbol: "C"),
                ChordEvent(start: 12, end: 16, symbol: "F"),
            ],
            sections: [
                SectionEvent(start: 0, end: 8, label: "Verse"),
                SectionEvent(start: 8, end: 16, label: "chorus"),
                SectionEvent(start: 16, end: 24, label: "verse"),
                SectionEvent(start: 24, end: 32, label: nil),
            ],
            beats: stride(from: 0.0, to: 32.0, by: 0.5).map { $0 },
            downbeats: stride(from: 0.0, to: 32.0, by: 2.0).map { $0 }
        ),
        stems: [],
        presets: [:]
    )

    override func setUp() async throws {
        try await super.setUp()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("learn-tests-\(UUID().uuidString)")
        learnRoot = tmpDir.appendingPathComponent("learn")
        try FileManager.default.createDirectory(
            at: learnRoot, withIntermediateDirectories: true
        )
        app = AppState(
            sessionStoreRoot: tmpDir,
            learnProgressRoot: learnRoot
        )
        app.currentBundle = Self.fixtureBundle
    }

    override func tearDown() async throws {
        app.learnController.stopPractice()
        app.pause()
        if let dir = tmpDir { try? FileManager.default.removeItem(at: dir) }
        app = nil
        try await super.tearDown()
    }

    private var controller: LearnSessionController { app.learnController }
    private var verse: SectionEvent { Self.fixtureBundle.timeline.sections[0] }
    private var chorus: SectionEvent { Self.fixtureBundle.timeline.sections[1] }

    // MARK: - Sections

    func testSectionKeyLowercasesLabels() {
        XCTAssertEqual(
            LearnSessionController.sectionKey(for: verse), "verse")
    }

    func testSectionKeyForUnlabelledUsesStartTime() {
        let tail = Self.fixtureBundle.timeline.sections[3]
        XCTAssertEqual(LearnSessionController.sectionKey(for: tail), "t24")
    }

    func testUniqueSectionsDeduplicateByKey() {
        // Verse (0..8) and verse (16..24) collapse; chorus + t24 stay.
        let keys = controller.uniqueSections
            .map { LearnSessionController.sectionKey(for: $0) }
        XCTAssertEqual(keys, ["verse", "chorus", "t24"])
        XCTAssertEqual(controller.totalSections, 3)
    }

    func testProgressionChordsCollapseRepeats() {
        XCTAssertEqual(
            controller.progressionChords(for: verse),
            ["Dm", "Bb", "C", "F"]
        )
        // Chorus holds C then F for 2 bars each.
        XCTAssertEqual(
            controller.progressionChords(for: chorus),
            ["C", "F"]
        )
    }

    // MARK: - Lifecycle

    func testStartSectionArmsLoopAndPlays() {
        controller.startSection(verse)
        XCTAssertEqual(controller.phase, .practicing)
        XCTAssertEqual(controller.activeSection, verse)
        XCTAssertEqual(app.loopRegion, LoopRegion(startSec: 0, endSec: 8))
        XCTAssertTrue(app.isPlaying)
    }

    func testStopPracticeClearsLoopAndPhase() {
        controller.startSection(verse)
        controller.stopPractice()
        XCTAssertEqual(controller.phase, .idle)
        XCTAssertNil(controller.activeSection)
        XCTAssertNil(app.loopRegion)
    }

    // MARK: - Press scoring

    func testRecordPressScoresHitAndMiss() {
        controller.startSection(verse)

        controller.recordPress(symbol: "Dm", atTime: 1.0)
        XCTAssertEqual(controller.lastPressHit, true)
        XCTAssertEqual(controller.passHits, 1)
        XCTAssertEqual(controller.currentStreak, 1)

        controller.recordPress(symbol: "C", atTime: 1.0)
        XCTAssertEqual(controller.lastPressHit, false)
        XCTAssertEqual(controller.passMisses, 1)
        XCTAssertEqual(controller.currentStreak, 0)
        XCTAssertEqual(controller.sessionLongestStreak, 1)
    }

    func testRecordPressWhileIdleIsNotScored() {
        controller.recordPress(symbol: "Dm", atTime: 1.0)
        XCTAssertEqual(controller.passHits, 0)
        XCTAssertNil(controller.lastPressHit)
    }

    // MARK: - Pass completion

    private func playPerfectVersePass() {
        controller.recordPress(symbol: "Dm", atTime: 1.0)
        controller.recordPress(symbol: "Bb", atTime: 3.0)
        controller.recordPress(symbol: "C", atTime: 5.0)
        controller.recordPress(symbol: "F", atTime: 7.0)
    }

    func testPerfectPassMarksSectionLearnedAndPersists() {
        controller.startSection(verse)
        playPerfectVersePass()
        controller.passCompleted()

        XCTAssertEqual(controller.lastPassResult?.isPassing, true)
        XCTAssertTrue(controller.isLearned(verse))
        XCTAssertEqual(controller.learnedCount, 1)
        // Pass buffer resets for the next loop; streak carries.
        XCTAssertEqual(controller.passHits, 0)
        XCTAssertEqual(controller.currentStreak, 4)

        // Persisted to disk — a fresh store sees the record.
        let reloaded = LearnProgressStore(root: learnRoot)
            .load(analysisId: "learn-fixture")
        XCTAssertEqual(reloaded?.progress(for: "verse").learned, true)
        XCTAssertEqual(reloaded?.longestStreak, 4)
    }

    func testFailedPassDoesNotMarkLearned() {
        controller.startSection(verse)
        controller.recordPress(symbol: "Dm", atTime: 1.0)
        controller.recordPress(symbol: "C", atTime: 1.2)  // miss
        controller.passCompleted()

        XCTAssertEqual(controller.lastPassResult?.isPassing, false)
        XCTAssertFalse(controller.isLearned(verse))
        let record = LearnProgressStore(root: learnRoot)
            .load(analysisId: "learn-fixture")
        XCTAssertEqual(record?.progress(for: "verse").passCount, 1)
        XCTAssertEqual(record?.progress(for: "verse").bestAccuracy ?? 0,
                       0.5, accuracy: 1e-9)
    }

    func testNextUpAdvancesAsSectionsAreLearned() {
        XCTAssertEqual(
            controller.nextUpSection.map(
                LearnSessionController.sectionKey(for:)),
            "verse"
        )
        controller.startSection(verse)
        playPerfectVersePass()
        controller.passCompleted()
        XCTAssertEqual(
            controller.nextUpSection.map(
                LearnSessionController.sectionKey(for:)),
            "chorus"
        )
    }

    // MARK: - Loop-wrap hook

    func testLoopWrapHookScoresOnlyInLearnMode() {
        controller.startSection(verse)
        playPerfectVersePass()

        // Hook is inert outside .learnSong.
        app.modeCoordinator.setMode(.jamInKey)
        app.onLoopWrap?()
        XCTAssertNil(controller.lastPassResult)

        app.modeCoordinator.setMode(.learnSong)
        app.onLoopWrap?()
        XCTAssertEqual(controller.lastPassResult?.isPassing, true)
    }
}
