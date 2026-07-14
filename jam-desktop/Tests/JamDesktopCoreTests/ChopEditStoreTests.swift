// ChopEditStoreTests.swift
//
// Persistence + notification coverage for ChopEditStore: boundary
// edits round-trip through UserDefaults, reverting to original
// boundaries drops the entry, empty collections are pruned, resets
// clear one preset, onEditsChanged fires per mutation, corrupt
// blobs degrade to empty.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

final class ChopEditStoreTests: XCTestCase {

    private var defaults: UserDefaults!
    private let suite = "ChopEditStoreTests"
    private let key = "jamdesktop.chopEdits"

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: suite)
        defaults.removePersistentDomain(forName: suite)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        super.tearDown()
    }

    @MainActor
    private func makeStore() -> ChopEditStore {
        ChopEditStore(defaults: defaults)
    }

    private func boundaryEdit(
        chopIndex: Int = 3,
        originalStart: Double = 10, originalEnd: Double = 12,
        editedStart: Double = 10.5, editedEnd: Double = 11.5
    ) -> ChopBoundaryEdit {
        ChopBoundaryEdit(
            chopIndex: chopIndex,
            originalStart: originalStart, originalEnd: originalEnd,
            editedStart: editedStart, editedEnd: editedEnd
        )
    }

    // MARK: - Round-trip

    @MainActor
    func testSetBoundaryRoundTripsThroughFreshStore() {
        let store = makeStore()
        store.setBoundary(boundaryEdit(), analysisId: "a1", presetKey: "harmonic")

        let reloaded = makeStore()
        let edits = reloaded.edits(analysisId: "a1", presetKey: "harmonic")
        XCTAssertEqual(edits.boundaryEdits[3]?.editedStart, 10.5)
        XCTAssertEqual(edits.boundaryEdits[3]?.editedEnd, 11.5)
        XCTAssertEqual(edits.boundaryEdits[3]?.originalStart, 10)
        XCTAssertTrue(edits.hasEdits)
    }

    @MainActor
    func testUnmodifiedBoundaryEditDropsEntry() {
        let store = makeStore()
        store.setBoundary(boundaryEdit(), analysisId: "a1", presetKey: "harmonic")

        // Revert to original boundaries — entry (and preset) pruned.
        store.setBoundary(
            boundaryEdit(editedStart: 10, editedEnd: 12),
            analysisId: "a1", presetKey: "harmonic"
        )

        XCTAssertFalse(
            store.edits(analysisId: "a1", presetKey: "harmonic").hasEdits
        )
        XCTAssertTrue(store.edits(analysisId: "a1").isEmpty, "empty song pruned")
    }

    @MainActor
    func testEditsAreScopedPerPresetAndSong() {
        let store = makeStore()
        store.setBoundary(boundaryEdit(chopIndex: 0), analysisId: "a1", presetKey: "harmonic")
        store.setBoundary(boundaryEdit(chopIndex: 1), analysisId: "a1", presetKey: "sections")
        store.setBoundary(boundaryEdit(chopIndex: 2), analysisId: "a2", presetKey: "harmonic")

        XCTAssertNotNil(
            store.edits(analysisId: "a1", presetKey: "harmonic").boundaryEdits[0])
        XCTAssertNil(
            store.edits(analysisId: "a1", presetKey: "harmonic").boundaryEdits[1])
        XCTAssertEqual(store.edits(analysisId: "a1").count, 2)
        XCTAssertEqual(store.edits(analysisId: "a2").count, 1)
    }

    @MainActor
    func testResetClearsOnePresetAndPersists() {
        let store = makeStore()
        store.setBoundary(boundaryEdit(), analysisId: "a1", presetKey: "harmonic")
        store.setBoundary(boundaryEdit(), analysisId: "a1", presetKey: "sections")

        store.reset(analysisId: "a1", presetKey: "harmonic")

        XCTAssertFalse(
            store.edits(analysisId: "a1", presetKey: "harmonic").hasEdits)
        XCTAssertTrue(
            store.edits(analysisId: "a1", presetKey: "sections").hasEdits)

        let reloaded = makeStore()
        XCTAssertFalse(
            reloaded.edits(analysisId: "a1", presetKey: "harmonic").hasEdits)
    }

    // MARK: - Notification

    @MainActor
    func testOnEditsChangedFiresWithAnalysisId() {
        let store = makeStore()
        var fired: [String] = []
        store.onEditsChanged = { fired.append($0) }

        store.setBoundary(boundaryEdit(), analysisId: "a1", presetKey: "harmonic")
        store.reset(analysisId: "a1", presetKey: "harmonic")

        XCTAssertEqual(fired, ["a1", "a1"])
    }

    // MARK: - Degenerate blobs

    @MainActor
    func testCorruptBlobFallsBackToEmpty() {
        defaults.set(Data("not json".utf8), forKey: key)
        XCTAssertTrue(makeStore().edits(analysisId: "a1").isEmpty)
    }

    @MainActor
    func testMissingBlobIsEmpty() {
        XCTAssertFalse(
            makeStore().edits(analysisId: "a1", presetKey: "harmonic").hasEdits)
    }
}
