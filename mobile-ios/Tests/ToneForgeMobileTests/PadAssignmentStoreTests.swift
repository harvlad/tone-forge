// PadAssignmentStoreTests.swift
//
// Blob round-trip, per-mode isolation, and dangling-reference cleanup
// for the pad-assignment store, against an isolated UserDefaults
// suite (SampleSettingsStore test pattern).

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class PadAssignmentStoreTests: XCTestCase {

    private var suiteName: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "PadAssignmentStoreTests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    private func makeStore() -> PadAssignmentStore {
        PadAssignmentStore(defaults: defaults)
    }

    func testEmptyByDefault() {
        let store = makeStore()
        XCTAssertNil(store.slot(mode: .sample, padIdx: 11))
        XCTAssertTrue(store.assignments(for: .sample).isEmpty)
    }

    func testAssignAndQuery() {
        let store = makeStore()
        let slot = PadSlot(ref: .localSample(id: UUID()))
        store.assign(slot, mode: .sample, padIdx: 42)

        XCTAssertEqual(store.slot(mode: .sample, padIdx: 42), slot)
        XCTAssertEqual(store.assignments(for: .sample), [42: slot])
        // Other modes unaffected.
        XCTAssertNil(store.slot(mode: .hybrid, padIdx: 42))
    }

    func testRestartPersistence() {
        let id = UUID()
        do {
            let store = makeStore()
            store.assign(
                PadSlot(ref: .localSample(id: id)), mode: .sample, padIdx: 11
            )
            store.assign(
                PadSlot(ref: .packPad(packId: "starter", padIdx: 23)),
                mode: .hybrid, padIdx: 88
            )
        }
        let reopened = makeStore()
        XCTAssertEqual(
            reopened.slot(mode: .sample, padIdx: 11),
            PadSlot(ref: .localSample(id: id))
        )
        XCTAssertEqual(
            reopened.slot(mode: .hybrid, padIdx: 88),
            PadSlot(ref: .packPad(packId: "starter", padIdx: 23))
        )
    }

    func testClearWithNil() {
        let store = makeStore()
        store.assign(
            PadSlot(ref: .localSample(id: UUID())), mode: .sample, padIdx: 11
        )
        store.assign(nil, mode: .sample, padIdx: 11)

        XCTAssertNil(store.slot(mode: .sample, padIdx: 11))
        let reopened = makeStore()
        XCTAssertNil(reopened.slot(mode: .sample, padIdx: 11))
    }

    func testRemoveAllReferencingDropsOnlyThatSample() {
        let store = makeStore()
        let doomed = UUID()
        let kept = UUID()
        store.assign(
            PadSlot(ref: .localSample(id: doomed)), mode: .sample, padIdx: 11
        )
        store.assign(
            PadSlot(ref: .localSample(id: doomed)), mode: .hybrid, padIdx: 55
        )
        store.assign(
            PadSlot(ref: .localSample(id: kept)), mode: .sample, padIdx: 12
        )
        store.assign(
            PadSlot(ref: .packPad(packId: "starter", padIdx: 1)),
            mode: .sample, padIdx: 13
        )

        store.removeAll(referencing: doomed)

        XCTAssertNil(store.slot(mode: .sample, padIdx: 11))
        XCTAssertNil(store.slot(mode: .hybrid, padIdx: 55))
        XCTAssertNotNil(store.slot(mode: .sample, padIdx: 12))
        XCTAssertNotNil(store.slot(mode: .sample, padIdx: 13))

        let reopened = makeStore()
        XCTAssertNil(reopened.slot(mode: .sample, padIdx: 11))
        XCTAssertNotNil(reopened.slot(mode: .sample, padIdx: 12))
    }

    func testCorruptBlobFallsBackToEmpty() {
        defaults.set(Data("garbage".utf8), forKey: "toneforge.padAssignments")
        let store = makeStore()
        XCTAssertTrue(store.assignmentsByMode.isEmpty)
    }

    func testPadSampleReferenceFrozenWireShape() throws {
        // The reference type rides inside session JSON (P6) — freeze it.
        let id = UUID(uuidString: "6F9B2C4D-1A2B-4C3D-8E5F-0123456789AB")!
        let json = """
        [{"type":"packPad","packId":"starter","padIdx":7},
         {"type":"localSample","id":"6F9B2C4D-1A2B-4C3D-8E5F-0123456789AB"}]
        """
        let decoded = try JSONDecoder().decode(
            [PadSampleReference].self, from: Data(json.utf8)
        )
        XCTAssertEqual(decoded, [
            .packPad(packId: "starter", padIdx: 7),
            .localSample(id: id),
        ])
        // Round-trip.
        let re = try JSONDecoder().decode(
            [PadSampleReference].self,
            from: try JSONEncoder().encode(decoded)
        )
        XCTAssertEqual(re, decoded)
    }
}
