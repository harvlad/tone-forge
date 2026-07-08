// LayerSlotsTests.swift
//
// Unit tests for LayerSlots (D-022 Phase 7): pure logic for slot
// management, toggle behavior, assignment, and Codable round-trip.

import XCTest
@testable import ToneForgeEngine

final class LayerSlotsTests: XCTestCase {

    // MARK: - RecordingSlot

    func testRecordingSlotToggled() {
        XCTAssertEqual(RecordingSlot.a.toggled, .b)
        XCTAssertEqual(RecordingSlot.b.toggled, .a)
    }

    func testRecordingSlotRawValues() {
        XCTAssertEqual(RecordingSlot.a.rawValue, "A")
        XCTAssertEqual(RecordingSlot.b.rawValue, "B")
    }

    // MARK: - LayerSlots init

    func testDefaultInit() {
        let slots = LayerSlots()
        XCTAssertEqual(slots.active, .a)
        XCTAssertTrue(slots.takes.isEmpty)
        XCTAssertFalse(slots.hasAnyTake)
    }

    func testCustomInit() {
        let id = UUID()
        let slots = LayerSlots(active: .b, takes: [.a: id])
        XCTAssertEqual(slots.active, .b)
        XCTAssertEqual(slots.takes[.a], id)
        XCTAssertNil(slots.takes[.b])
    }

    // MARK: - Toggle

    func testToggleActiveFromA() {
        var slots = LayerSlots()
        XCTAssertEqual(slots.active, .a)
        let result = slots.toggleActive()
        XCTAssertEqual(result, .b)
        XCTAssertEqual(slots.active, .b)
    }

    func testToggleActiveFromB() {
        var slots = LayerSlots(active: .b)
        let result = slots.toggleActive()
        XCTAssertEqual(result, .a)
        XCTAssertEqual(slots.active, .a)
    }

    // MARK: - Assign / Clear

    func testAssignSessionToSlot() {
        var slots = LayerSlots()
        let id = UUID()
        slots.assign(sessionId: id, to: .a)
        XCTAssertEqual(slots.take(for: .a), id)
        XCTAssertTrue(slots.hasTake(.a))
        XCTAssertTrue(slots.hasAnyTake)
    }

    func testReassignReplacesOldPointer() {
        var slots = LayerSlots()
        let id1 = UUID()
        let id2 = UUID()
        slots.assign(sessionId: id1, to: .a)
        slots.assign(sessionId: id2, to: .a)
        XCTAssertEqual(slots.take(for: .a), id2)
    }

    func testClearSlot() {
        var slots = LayerSlots()
        let id = UUID()
        slots.assign(sessionId: id, to: .b)
        XCTAssertTrue(slots.hasTake(.b))
        slots.clear(slot: .b)
        XCTAssertFalse(slots.hasTake(.b))
        XCTAssertNil(slots.take(for: .b))
    }

    func testHasTakeForBothSlots() {
        var slots = LayerSlots()
        let idA = UUID()
        let idB = UUID()
        slots.assign(sessionId: idA, to: .a)
        slots.assign(sessionId: idB, to: .b)
        XCTAssertTrue(slots.hasTake(.a))
        XCTAssertTrue(slots.hasTake(.b))
        XCTAssertTrue(slots.hasAnyTake)
    }

    // MARK: - Codable

    func testCodableRoundTrip() throws {
        var slots = LayerSlots(active: .b)
        slots.assign(sessionId: UUID(), to: .a)
        slots.assign(sessionId: UUID(), to: .b)

        let data = try JSONEncoder().encode(slots)
        let decoded = try JSONDecoder().decode(LayerSlots.self, from: data)

        XCTAssertEqual(decoded.active, slots.active)
        XCTAssertEqual(decoded.takes, slots.takes)
    }

    func testCodableEmptyTakes() throws {
        let slots = LayerSlots()
        let data = try JSONEncoder().encode(slots)
        let decoded = try JSONDecoder().decode(LayerSlots.self, from: data)
        XCTAssertTrue(decoded.takes.isEmpty)
    }
}
