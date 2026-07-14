// RehearsalModelTests.swift
//
// Section grid construction (dedupe + recurrence), chord-progression
// collapsing, section→loop mapping, selection cycling, and the goal
// timer lifecycle hooks.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class RehearsalModelTests: XCTestCase {

    private func makeBundle(
        sections: [SectionEvent],
        chords: [ChordEvent] = []
    ) -> SongBundle {
        SongBundle(
            bundleVersion: 1,
            analysisId: "test",
            meta: BundleMeta(
                title: "T", artist: "A", sourceUrl: "", durationSec: 120),
            timeline: BundleTimeline(chords: chords, sections: sections),
            stems: [],
            presets: [:]
        )
    }

    func testDedupesRepeatedLabelsAndCountsRecurrence() {
        let model = RehearsalModel()
        model.load(bundle: makeBundle(sections: [
            SectionEvent(start: 0, end: 10, label: "Verse"),
            SectionEvent(start: 10, end: 20, label: "Chorus"),
            SectionEvent(start: 20, end: 30, label: "verse"),
            SectionEvent(start: 30, end: 40, label: "Chorus"),
            SectionEvent(start: 40, end: 50, label: "Bridge"),
        ]))
        XCTAssertEqual(model.items.map(\.label), ["Verse", "Chorus", "Bridge"])
        XCTAssertEqual(model.items.map(\.recurrenceCount), [2, 2, 1])
        // First occurrence's timing wins.
        XCTAssertEqual(model.items[0].section.start, 0)
    }

    func testUnlabeledSectionsAllKept() {
        let model = RehearsalModel()
        model.load(bundle: makeBundle(sections: [
            SectionEvent(start: 0, end: 10, label: nil),
            SectionEvent(start: 10, end: 20, label: ""),
            SectionEvent(start: 20, end: 30, label: nil),
        ]))
        XCTAssertEqual(model.items.count, 3)
        XCTAssertEqual(model.items.map(\.label), ["Section 1", "Section 2", "Section 3"])
        XCTAssertEqual(model.items.map(\.recurrenceCount), [1, 1, 1])
    }

    func testProgressionCollapsesConsecutiveRepeats() {
        let section = SectionEvent(start: 0, end: 8, label: "Verse")
        let chords = [
            ChordEvent(start: 0, end: 2, symbol: "Am"),
            ChordEvent(start: 2, end: 4, symbol: "Am"),
            ChordEvent(start: 4, end: 6, symbol: "F"),
            ChordEvent(start: 6, end: 8, symbol: "Am"),
            ChordEvent(start: 8, end: 10, symbol: "G"), // outside
        ]
        XCTAssertEqual(
            RehearsalModel.progression(in: section, chords: chords),
            ["Am", "F", "Am"]
        )
    }

    func testProgressionIncludesBoundaryOverlaps() {
        let section = SectionEvent(start: 4, end: 8, label: "Chorus")
        let chords = [
            ChordEvent(start: 0, end: 4, symbol: "C"),   // ends at start: excluded
            ChordEvent(start: 3, end: 5, symbol: "G"),   // straddles in
            ChordEvent(start: 7, end: 9, symbol: "D"),   // straddles out
            ChordEvent(start: 8, end: 10, symbol: "E"),  // starts at end: excluded
        ]
        XCTAssertEqual(
            RehearsalModel.progression(in: section, chords: chords),
            ["G", "D"]
        )
    }

    func testActiveLoopMapsSelectedSection() {
        let model = RehearsalModel()
        model.load(bundle: makeBundle(sections: [
            SectionEvent(start: 0, end: 10, label: "Verse"),
            SectionEvent(start: 10, end: 25, label: "Chorus"),
        ]))
        model.select(sectionIndex: 1)
        XCTAssertEqual(
            model.activeLoop,
            LoopRegion(inSeconds: 10, outSeconds: 25)
        )

        model.loopEnabled = false
        XCTAssertNil(model.activeLoop)
    }

    func testSelectIgnoresUnknownIndex() {
        let model = RehearsalModel()
        model.load(bundle: makeBundle(sections: [
            SectionEvent(start: 0, end: 10, label: "Verse"),
        ]))
        model.select(sectionIndex: 99)
        XCTAssertEqual(model.selectedIndex, 0)
    }

    func testSelectNextWraps() {
        let model = RehearsalModel()
        model.load(bundle: makeBundle(sections: [
            SectionEvent(start: 0, end: 10, label: "Verse"),
            SectionEvent(start: 10, end: 20, label: "Chorus"),
        ]))
        XCTAssertEqual(model.selectedIndex, 0)
        model.selectNext()
        XCTAssertEqual(model.selectedIndex, 1)
        model.selectNext()
        XCTAssertEqual(model.selectedIndex, 0)
    }

    func testDefaultsMatchWebParity() {
        let model = RehearsalModel()
        XCTAssertTrue(model.loopEnabled)          // web loop defaults ON
        XCTAssertEqual(model.speed, 1.0)
        XCTAssertEqual(RehearsalModel.speeds, [0.5, 0.75, 1.0])
        XCTAssertEqual(model.goalTimer.goalMinutes, 15)
    }

    func testViewLifecycleDrivesGoalTimer() {
        let model = RehearsalModel()
        let t0 = Date(timeIntervalSince1970: 1_700_000_000)
        model.enterView(now: t0)
        XCTAssertTrue(model.goalTimer.isRunning)
        XCTAssertEqual(
            model.goalTimer.elapsedMinutes(now: t0.addingTimeInterval(120)), 2,
            accuracy: 0.001)
        model.leaveView()
        XCTAssertFalse(model.goalTimer.isRunning)
    }
}
