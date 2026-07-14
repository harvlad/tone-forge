// SequencerPatternStoreTests.swift
//
// Blob round-trip + mutation coverage for the saved-sequencer-pattern
// store (D-023), against an isolated UserDefaults suite.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class SequencerPatternStoreTests: XCTestCase {

    private var suiteName: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "SequencerPatternStoreTests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    private func makeStore() -> SequencerPatternStore {
        SequencerPatternStore(defaults: defaults)
    }

    private func makePattern(name: String) -> SequencerPattern {
        SequencerPattern(
            name: name,
            stepCount: .sixteen,
            tracks: [
                SequencerTrack(
                    chopRef: .packPad(packId: "starter", padIdx: 51),
                    stepCount: 16,
                    name: "Kick"
                )
            ]
        )
    }

    func testEmptyByDefault() {
        let store = makeStore()
        XCTAssertTrue(store.all().isEmpty)
        XCTAssertNil(store.pattern(id: UUID()))
    }

    func testSaveAndQuery() {
        let store = makeStore()
        let pattern = makePattern(name: "Beat A")
        store.save(pattern)

        XCTAssertEqual(store.pattern(id: pattern.id), pattern)
        XCTAssertEqual(store.all().map(\.id), [pattern.id])
    }

    func testSaveUpdatesInPlace() {
        let store = makeStore()
        var pattern = makePattern(name: "Beat A")
        store.save(pattern)

        pattern.name = "Beat A (edited)"
        store.save(pattern)

        XCTAssertEqual(store.all().count, 1)
        XCTAssertEqual(store.pattern(id: pattern.id)?.name, "Beat A (edited)")
    }

    func testAllSortedByName() {
        let store = makeStore()
        store.save(makePattern(name: "Zebra"))
        store.save(makePattern(name: "apple"))
        store.save(makePattern(name: "Mango"))

        XCTAssertEqual(store.all().map(\.name), ["apple", "Mango", "Zebra"])
    }

    func testDelete() {
        let store = makeStore()
        let pattern = makePattern(name: "Beat A")
        store.save(pattern)
        store.delete(id: pattern.id)

        XCTAssertNil(store.pattern(id: pattern.id))
        XCTAssertTrue(store.all().isEmpty)
    }

    func testRestartPersistence() {
        let pattern = makePattern(name: "Persisted")
        do {
            let store = makeStore()
            store.save(pattern)
        }
        let reopened = makeStore()
        XCTAssertEqual(reopened.pattern(id: pattern.id), pattern)
    }

    func testCorruptBlobFallsBackToEmpty() {
        defaults.set(Data("garbage".utf8), forKey: "toneforge.sequencerPatterns")
        let store = makeStore()
        XCTAssertTrue(store.all().isEmpty)
    }
}
