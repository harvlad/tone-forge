// MIDIPadMapStoreTests.swift
//
// Persistence round-trip for the MIDI-Learn pad map (sidecar
// UserDefaults key, mobile wire format: [[note, pad], ...] JSON
// pairs) and the map -> noteRouting derivation rule.

import XCTest
@testable import JamDesktopCore

@MainActor
final class MIDIPadMapStoreTests: XCTestCase {

    private var defaults: UserDefaults!
    private let suite = "MIDIPadMapStoreTests"

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: suite)
        defaults.removePersistentDomain(forName: suite)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        super.tearDown()
    }

    func testEmptyByDefault() {
        XCTAssertTrue(MIDIPadMapStore(defaults: defaults).map.isEmpty)
    }

    func testRoundTrip() {
        let store = MIDIPadMapStore(defaults: defaults)
        store.map = [70: 5, 40: 0, 51: 15]

        let reloaded = MIDIPadMapStore(defaults: defaults)
        XCTAssertEqual(reloaded.map, [70: 5, 40: 0, 51: 15])
    }

    func testClearPersists() {
        let store = MIDIPadMapStore(defaults: defaults)
        store.map = [70: 5]
        store.map = [:]

        XCTAssertTrue(MIDIPadMapStore(defaults: defaults).map.isEmpty)
    }

    func testOnMapChangedFires() {
        let store = MIDIPadMapStore(defaults: defaults)
        var seen: [[Int: Int]] = []
        store.onMapChanged = { seen.append($0) }
        store.map = [40: 0]
        store.map = [:]

        XCTAssertEqual(seen, [[40: 0], [:]])
    }

    func testGarbageDataLoadsAsEmpty() {
        defaults.set(Data("not json".utf8), forKey: MIDIPadMapStore.key)
        XCTAssertTrue(MIDIPadMapStore(defaults: defaults).map.isEmpty)
    }

    func testNoteRoutingDerivation() {
        // Empty map keeps the synth default; a learned map wins and
        // routes (only) its notes to the sample grid.
        XCTAssertEqual(MIDIPadMapStore.noteRouting(map: [:]), .synth)
        XCTAssertEqual(MIDIPadMapStore.noteRouting(map: [70: 5]),
                       .mappedPads(map: [70: 5]))
    }
}
