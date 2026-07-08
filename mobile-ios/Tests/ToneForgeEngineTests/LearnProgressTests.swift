// LearnProgressTests.swift
//
// SongLearnProgress / SectionProgress semantics + Codable round-trip
// (redesign Phase 8).

import XCTest
@testable import ToneForgeEngine

final class LearnProgressTests: XCTestCase {

    private func passing(accuracy: Double = 0.9) -> LearnPassResult {
        LearnPassResult(
            hits: 9, misses: 1, coverage: 1.0,
            accuracy: accuracy, isPassing: true
        )
    }

    private func failing(accuracy: Double = 0.5) -> LearnPassResult {
        LearnPassResult(
            hits: 1, misses: 1, coverage: 0.5,
            accuracy: accuracy, isPassing: false
        )
    }

    // MARK: - SectionProgress

    func testFoldTracksBestAccuracyAndPassCount() {
        var s = SectionProgress()
        s.fold(failing(accuracy: 0.5))
        XCTAssertEqual(s.passCount, 1)
        XCTAssertEqual(s.bestAccuracy, 0.5)
        XCTAssertFalse(s.learned)

        s.fold(passing(accuracy: 0.9))
        XCTAssertEqual(s.passCount, 2)
        XCTAssertEqual(s.bestAccuracy, 0.9)
        XCTAssertTrue(s.learned)
    }

    func testLearnedNeverReverts() {
        var s = SectionProgress()
        s.fold(passing())
        XCTAssertTrue(s.learned)
        s.fold(failing(accuracy: 0.1))
        XCTAssertTrue(s.learned, "a bad pass must not unlearn a section")
        XCTAssertEqual(s.bestAccuracy, 0.9)
    }

    // MARK: - SongLearnProgress

    func testSectionKeyLowercases() {
        XCTAssertEqual(SongLearnProgress.sectionKey("Chorus"), "chorus")
    }

    func testRecordPassAndDerivedStats() {
        var p = SongLearnProgress(analysisId: "song1")
        p.recordPass(passing(accuracy: 0.9), sectionLabel: "verse")
        p.recordPass(failing(accuracy: 0.5), sectionLabel: "chorus")

        XCTAssertEqual(p.learnedCount, 1)
        XCTAssertEqual(p.overallAccuracy, 0.7, accuracy: 1e-9)
        XCTAssertEqual(p.percentComplete(totalSections: 4), 0.25)
        XCTAssertEqual(p.percentComplete(totalSections: 0), 0)
    }

    func testPercentCompleteClamped() {
        var p = SongLearnProgress(analysisId: "song1")
        p.recordPass(passing(), sectionLabel: "a")
        p.recordPass(passing(), sectionLabel: "b")
        XCTAssertEqual(p.percentComplete(totalSections: 1), 1.0)
    }

    func testRecordStreakKeepsMax() {
        var p = SongLearnProgress(analysisId: "song1")
        p.recordStreak(5)
        p.recordStreak(3)
        XCTAssertEqual(p.longestStreak, 5)
        p.recordStreak(8)
        XCTAssertEqual(p.longestStreak, 8)
    }

    func testProgressForUnknownLabelIsDefaults() {
        let p = SongLearnProgress(analysisId: "song1")
        let s = p.progress(for: "bridge")
        XCTAssertFalse(s.learned)
        XCTAssertEqual(s.passCount, 0)
        XCTAssertEqual(s.bestAccuracy, 0)
        XCTAssertEqual(p.overallAccuracy, 0)
    }

    // MARK: - Codable

    func testCodableRoundTrip() throws {
        var p = SongLearnProgress(analysisId: "song1")
        p.recordPass(passing(), sectionLabel: "verse")
        p.recordStreak(12)

        let data = try JSONEncoder().encode(p)
        let decoded = try JSONDecoder().decode(
            SongLearnProgress.self, from: data)
        XCTAssertEqual(decoded, p)
        XCTAssertEqual(decoded.schemaVersion, 1)
    }
}
