// PadAssignmentStoreTests.swift

import XCTest
@testable import JamDesktopCore

@MainActor
final class PadAssignmentStoreTests: XCTestCase {

    private var defaults: UserDefaults!
    private var store: PadAssignmentStore!

    override func setUp() async throws {
        defaults = UserDefaults(suiteName: "PadAssignmentStoreTests")!
        defaults.removePersistentDomain(forName: "PadAssignmentStoreTests")
        store = PadAssignmentStore(defaults: defaults)
    }

    override func tearDown() async throws {
        defaults.removePersistentDomain(forName: "PadAssignmentStoreTests")
    }

    // MARK: - Basic Operations

    func testAssignSequence() {
        let patternId = UUID()
        store.assign(.sequence(patternId: patternId), padIdx: 12)

        XCTAssertEqual(store.slot(padIdx: 12), .sequence(patternId: patternId))
        XCTAssertNil(store.slot(padIdx: 0))
    }

    func testClearAssignment() {
        let patternId = UUID()
        store.assign(.sequence(patternId: patternId), padIdx: 5)
        XCTAssertNotNil(store.slot(padIdx: 5))

        store.assign(nil, padIdx: 5)
        XCTAssertNil(store.slot(padIdx: 5))
    }

    func testClearAll() {
        store.assign(.sequence(patternId: UUID()), padIdx: 1)
        store.assign(.sequence(patternId: UUID()), padIdx: 2)
        store.assign(.sequence(patternId: UUID()), padIdx: 3)

        store.clearAll()

        XCTAssertNil(store.slot(padIdx: 1))
        XCTAssertNil(store.slot(padIdx: 2))
        XCTAssertNil(store.slot(padIdx: 3))
    }

    // MARK: - Persistence

    func testPersistence() {
        let patternId = UUID()
        store.assign(.sequence(patternId: patternId), padIdx: 42)

        // Create new store from same defaults
        let store2 = PadAssignmentStore(defaults: defaults)
        XCTAssertEqual(store2.slot(padIdx: 42), .sequence(patternId: patternId))
    }

    func testPersistenceMultipleAssignments() {
        let id1 = UUID()
        let id2 = UUID()
        store.assign(.sequence(patternId: id1), padIdx: 0)
        store.assign(.sequence(patternId: id2), padIdx: 63)

        let store2 = PadAssignmentStore(defaults: defaults)
        XCTAssertEqual(store2.slot(padIdx: 0), .sequence(patternId: id1))
        XCTAssertEqual(store2.slot(padIdx: 63), .sequence(patternId: id2))
    }

    // MARK: - Query Methods

    func testSequencePads() {
        let id1 = UUID()
        let id2 = UUID()
        store.assign(.sequence(patternId: id1), padIdx: 10)
        store.assign(.sequence(patternId: id2), padIdx: 20)

        let pads = store.sequencePads()
        XCTAssertEqual(pads.count, 2)
        XCTAssertTrue(pads.contains { $0.padIdx == 10 && $0.patternId == id1 })
        XCTAssertTrue(pads.contains { $0.padIdx == 20 && $0.patternId == id2 })
    }

    // MARK: - Codable

    func testPadSlotReferenceCodable() throws {
        let patternId = UUID()
        let ref = PadSlotReference.sequence(patternId: patternId)

        let data = try JSONEncoder().encode(ref)
        let decoded = try JSONDecoder().decode(PadSlotReference.self, from: data)

        XCTAssertEqual(decoded, ref)
    }

    func testLocalSampleCodable() throws {
        let sampleId = UUID()
        let ref = PadSlotReference.localSample(id: sampleId)

        let data = try JSONEncoder().encode(ref)
        let decoded = try JSONDecoder().decode(PadSlotReference.self, from: data)

        XCTAssertEqual(decoded, ref)
    }

    func testPackPadCodable() throws {
        let ref = PadSlotReference.packPad(packId: "drums-808", padIdx: 5)

        let data = try JSONEncoder().encode(ref)
        let decoded = try JSONDecoder().decode(PadSlotReference.self, from: data)

        XCTAssertEqual(decoded, ref)
    }
}
