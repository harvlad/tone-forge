// SectionResolverTests.swift
//
// Pure-logic tests for `SectionResolver.current`, `.currentLabel`,
// `.isAllowed`, and `.uniqueLabels`. Covers before-first / exact
// boundary / after-last / gap-between-sections cases and all three
// `isAllowed` semantic branches (nil = allow, empty = deny, non-empty
// = whitelist).

import XCTest
@testable import ToneForgeEngine

final class SectionResolverTests: XCTestCase {

    // MARK: - Fixtures

    private let sections: [SectionEvent] = [
        SectionEvent(start: 0, end: 8, label: "Intro"),
        SectionEvent(start: 8, end: 24, label: "Verse"),
        SectionEvent(start: 24, end: 40, label: "Chorus"),
        // Gap from 40 to 48 (no section)
        SectionEvent(start: 48, end: 64, label: "Verse"),
    ]

    // MARK: - current(t:in:)

    func testCurrentBeforeFirstReturnsNil() {
        XCTAssertNil(SectionResolver.current(t: -1.0, in: sections))
    }

    func testCurrentAtExactStartReturnsThatSection() {
        // Half-open window: t=8.0 belongs to Verse, not Intro.
        let s = SectionResolver.current(t: 8.0, in: sections)
        XCTAssertEqual(s?.label, "Verse")
    }

    func testCurrentJustBeforeEndReturnsThatSection() {
        let s = SectionResolver.current(t: 23.999, in: sections)
        XCTAssertEqual(s?.label, "Verse")
    }

    func testCurrentInGapReturnsNil() {
        // 40..48 is a gap.
        XCTAssertNil(SectionResolver.current(t: 44.0, in: sections))
    }

    func testCurrentAfterLastReturnsNil() {
        XCTAssertNil(SectionResolver.current(t: 100.0, in: sections))
    }

    func testCurrentEmptySectionsReturnsNil() {
        XCTAssertNil(SectionResolver.current(t: 5.0, in: []))
    }

    // MARK: - currentLabel

    func testCurrentLabelReturnsLabel() {
        XCTAssertEqual(SectionResolver.currentLabel(t: 30.0, in: sections), "Chorus")
    }

    func testCurrentLabelInGapIsNil() {
        XCTAssertNil(SectionResolver.currentLabel(t: 44.0, in: sections))
    }

    // MARK: - isAllowed (nil = allow all)

    func testIsAllowedNilAllowsEverything() {
        XCTAssertTrue(SectionResolver.isAllowed(t: 5.0, in: sections, allowed: nil))
        XCTAssertTrue(SectionResolver.isAllowed(t: 44.0, in: sections, allowed: nil))
    }

    // MARK: - isAllowed (empty = deny all except gaps)

    func testIsAllowedEmptyDeniesWhenInsideSection() {
        XCTAssertFalse(SectionResolver.isAllowed(t: 5.0, in: sections, allowed: []))
        XCTAssertFalse(SectionResolver.isAllowed(t: 30.0, in: sections, allowed: []))
    }

    func testIsAllowedEmptyAllowsInGap() {
        // Gap between sections is "no section to gate on" → allow.
        XCTAssertTrue(SectionResolver.isAllowed(t: 44.0, in: sections, allowed: []))
    }

    // MARK: - isAllowed (whitelist)

    func testIsAllowedWhitelistAllowsListedLabel() {
        let allow: Set<String> = ["Chorus"]
        XCTAssertTrue(SectionResolver.isAllowed(t: 30.0, in: sections, allowed: allow))
    }

    func testIsAllowedWhitelistDeniesUnlistedLabel() {
        let allow: Set<String> = ["Chorus"]
        XCTAssertFalse(SectionResolver.isAllowed(t: 5.0, in: sections, allowed: allow))
    }

    func testIsAllowedWhitelistCaseInsensitive() {
        let allow: Set<String> = ["chorus"]
        XCTAssertTrue(SectionResolver.isAllowed(t: 30.0, in: sections, allowed: allow))
    }

    func testIsAllowedWhitelistAllowsInGap() {
        let allow: Set<String> = ["Chorus"]
        XCTAssertTrue(SectionResolver.isAllowed(t: 44.0, in: sections, allowed: allow))
    }

    func testIsAllowedAllowsPastLastSection() {
        // No current section = musical "no section" = allow.
        let allow: Set<String> = ["Chorus"]
        XCTAssertTrue(SectionResolver.isAllowed(t: 100.0, in: sections, allowed: allow))
    }

    // MARK: - uniqueLabels

    func testUniqueLabelsPreservesFirstEncounterOrder() {
        // "Verse" appears twice; second occurrence dropped.
        let labels = SectionResolver.uniqueLabels(in: sections)
        XCTAssertEqual(labels, ["Intro", "Verse", "Chorus"])
    }

    func testUniqueLabelsDropsNilAndEmpty() {
        let withNoLabels = [
            SectionEvent(start: 0, end: 4, label: nil),
            SectionEvent(start: 4, end: 8, label: ""),
            SectionEvent(start: 8, end: 12, label: "Verse"),
        ]
        let labels = SectionResolver.uniqueLabels(in: withNoLabels)
        XCTAssertEqual(labels, ["Verse"])
    }

    func testUniqueLabelsIsCaseInsensitiveForDedup() {
        let mixed = [
            SectionEvent(start: 0, end: 4, label: "Verse"),
            SectionEvent(start: 4, end: 8, label: "verse"),
            SectionEvent(start: 8, end: 12, label: "VERSE"),
        ]
        let labels = SectionResolver.uniqueLabels(in: mixed)
        XCTAssertEqual(labels, ["Verse"])
    }
}
