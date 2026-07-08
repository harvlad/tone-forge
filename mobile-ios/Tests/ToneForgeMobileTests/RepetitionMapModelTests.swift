// RepetitionMapModelTests.swift
//
// D-022 Learn redesign: section timeline → repetition rows for the
// Section Overview sheet.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

final class RepetitionMapModelTests: XCTestCase {

    func testGroupsByLabelInFirstAppearanceOrder() {
        let rows = RepetitionMapModel.rows(
            sections: [
                SectionEvent(start: 0, end: 10, label: "intro"),
                SectionEvent(start: 10, end: 30, label: "verse"),
                SectionEvent(start: 30, end: 50, label: "chorus"),
                SectionEvent(start: 50, end: 70, label: "verse"),
                SectionEvent(start: 70, end: 90, label: "chorus"),
            ],
            duration: 90
        )
        XCTAssertEqual(rows.map(\.label), ["Intro", "Verse", "Chorus"])
        XCTAssertEqual(rows[0].occurrences, [0...10])
        XCTAssertEqual(rows[1].occurrences, [10...30, 50...70])
        XCTAssertEqual(rows[2].occurrences, [30...50, 70...90])
    }

    func testLabelGroupingIsCaseInsensitive() {
        let rows = RepetitionMapModel.rows(
            sections: [
                SectionEvent(start: 0, end: 10, label: "Chorus"),
                SectionEvent(start: 20, end: 30, label: "chorus"),
            ],
            duration: 30
        )
        XCTAssertEqual(rows.count, 1)
        XCTAssertEqual(rows[0].label, "Chorus")
        XCTAssertEqual(rows[0].occurrences.count, 2)
    }

    func testUnlabelledSectionsGroupUnderSection() {
        let rows = RepetitionMapModel.rows(
            sections: [
                SectionEvent(start: 0, end: 10, label: nil),
                SectionEvent(start: 10, end: 20, label: nil),
            ],
            duration: 20
        )
        XCTAssertEqual(rows.map(\.label), ["Section"])
        XCTAssertEqual(rows[0].occurrences.count, 2)
    }

    func testClampsToDurationAndDropsDegenerates() {
        let rows = RepetitionMapModel.rows(
            sections: [
                SectionEvent(start: -5, end: 10, label: "intro"),
                SectionEvent(start: 80, end: 200, label: "outro"),
                SectionEvent(start: 30, end: 30, label: "zero"),
                SectionEvent(start: 150, end: 180, label: "ghost"),
            ],
            duration: 100
        )
        XCTAssertEqual(rows.map(\.label), ["Intro", "Outro"])
        XCTAssertEqual(rows[0].occurrences, [0...10])
        XCTAssertEqual(rows[1].occurrences, [80...100])
    }

    func testEmptyInputs() {
        XCTAssertTrue(
            RepetitionMapModel.rows(sections: [], duration: 100).isEmpty)
        XCTAssertTrue(
            RepetitionMapModel.rows(
                sections: [SectionEvent(start: 0, end: 10, label: "a")],
                duration: 0
            ).isEmpty)
    }
}
