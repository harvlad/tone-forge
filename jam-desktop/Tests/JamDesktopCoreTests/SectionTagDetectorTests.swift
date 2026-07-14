// SectionTagDetectorTests.swift
//
// Taxonomy lock-step with jam.js/debug.js detectSectionTags. If any of
// these fail after a rule change, the web regexes changed — mirror the
// change, don't loosen the test.

import XCTest
@testable import JamDesktopCore

final class SectionTagDetectorTests: XCTestCase {

    private func section(
        start: Double = 0, end: Double = 10,
        landmarks: [LandmarkNote]? = nil
    ) -> DebugSection {
        DebugSection(startS: start, endS: end, landmarkNotes: landmarks)
    }

    private func chord(_ symbol: String, mid: Double = 5) -> DebugChord {
        DebugChord(startS: mid - 0.5, endS: mid + 0.5, symbol: symbol)
    }

    private func tags(
        _ symbols: [String], start: Double = 0, end: Double = 10
    ) -> [String] {
        SectionTagDetector.detectSectionTags(
            section(start: start, end: end),
            chords: symbols.map { chord($0) }
        ).map { $0.id }
    }

    // MARK: - barre regex ^(F#?|B|Bb)(m?)(?![a-z0-9])

    func testBarreFiresOnRoots() {
        XCTAssertTrue(tags(["F"]).contains("barre"))
        XCTAssertTrue(tags(["F#"]).contains("barre"))
        XCTAssertTrue(tags(["B"]).contains("barre"))
        XCTAssertTrue(tags(["Bb"]).contains("barre"))
        XCTAssertTrue(tags(["Fm"]).contains("barre"))
        XCTAssertTrue(tags(["Bm"]).contains("barre"))
    }

    func testFmaj7FiresColourNotBarre() {
        // "m" of "maj" is eaten by m?, then "a" trips the lookahead.
        let t = tags(["Fmaj7"])
        XCTAssertFalse(t.contains("barre"))
        XCTAssertTrue(t.contains("colour"))
    }

    func testF7BlockedByDigitLookahead() {
        let t = tags(["F7"])
        XCTAssertFalse(t.contains("barre"))
        XCTAssertTrue(t.contains("colour"))
    }

    func testNonBarreRootsDoNotFire() {
        XCTAssertFalse(tags(["C"]).contains("barre"))
        XCTAssertFalse(tags(["G"]).contains("barre"))
        XCTAssertFalse(tags(["Am"]).contains("barre"))
    }

    // MARK: - colour regex (7|sus2|sus4|add9|maj7|m7) case-insensitive

    func testColourExtensions() {
        XCTAssertTrue(tags(["Csus4"]).contains("colour"))
        XCTAssertTrue(tags(["Gadd9"]).contains("colour"))
        XCTAssertTrue(tags(["Am7"]).contains("colour"))
        XCTAssertTrue(tags(["CMAJ7"]).contains("colour"))
        XCTAssertFalse(tags(["C", "G", "Am"]).contains("colour"))
    }

    // MARK: - chord membership by midpoint [start, end)

    func testChordOutsideSectionIgnored() {
        let sec = section(start: 0, end: 4)
        let outside = DebugChord(startS: 4.5, endS: 6.5, symbol: "F")  // mid 5.5
        let boundary = DebugChord(startS: 3.5, endS: 4.5, symbol: "B")  // mid 4.0 — excluded
        let tags = SectionTagDetector.detectSectionTags(sec, chords: [outside, boundary])
        XCTAssertFalse(tags.contains { $0.id == "barre" })
    }

    // MARK: - jumps: landmark span > 10 semitones, needs >= 2 notes

    func testJumpsSpanBoundary() {
        func span(_ lo: Double, _ hi: Double) -> Bool {
            SectionTagDetector.detectSectionTags(
                section(landmarks: [
                    LandmarkNote(pitch: lo), LandmarkNote(pitch: hi),
                ]), chords: []
            ).contains { $0.id == "jumps" }
        }
        XCTAssertFalse(span(60, 70))   // span 10 — no
        XCTAssertTrue(span(60, 71))    // span 11 — yes
    }

    func testJumpsNeedsTwoLandmarks() {
        let tags = SectionTagDetector.detectSectionTags(
            section(landmarks: [LandmarkNote(pitch: 60)]), chords: [])
        XCTAssertFalse(tags.contains { $0.id == "jumps" })
    }

    // MARK: - quick: 0 < duration < 4

    func testQuickDurationBoundary() {
        XCTAssertTrue(tags([], start: 0, end: 3.9).contains("quick"))
        XCTAssertFalse(tags([], start: 0, end: 4.0).contains("quick"))
        XCTAssertFalse(tags([], start: 0, end: 0).contains("quick"))
    }

    // MARK: - tagSummary

    func testTagSummaryIndexesAllSections() throws {
        let bundle = DebugBundle(understanding: DebugUnderstanding(
            sections: [
                DebugSection(startS: 0, endS: 2),     // quick
                DebugSection(startS: 2, endS: 10),    // none
            ],
            chords: []))
        let rows = SectionTagDetector.tagSummary(bundle)
        XCTAssertEqual(rows.count, 2)
        XCTAssertEqual(rows[0].tags.map { $0.id }, ["quick"])
        XCTAssertTrue(rows[1].tags.isEmpty)
    }
}
