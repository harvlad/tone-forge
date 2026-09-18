// PadFXStoreTests.swift
//
// Per-pad FX store: cross-surface key form, clamped writes,
// whole-map swap (Project restore), and disk round-trip.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class PadFXStoreTests: XCTestCase {

    private var tempDir: URL!
    private var store: PadFXStore!

    override func setUp() async throws {
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        store = PadFXStore(root: tempDir)
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: tempDir)
    }

    func testKeyFormMatchesIOS() {
        // iOS SampleSettingsStore.padEffectsKey: "\(packId)#\(padIdx)".
        XCTAssertEqual(PadFXStore.key(packId: "kit:abc", padIdx: 7), "kit:abc#7")
    }

    func testSetGetClear() {
        let fx = SamplePadEffects(
            delayTimeSec: 0.4, delayFeedback: 30, delayMix: 25,
            filterCutoffHz: 4_000, filterResonanceDb: 3)
        store.setEffects(fx, packId: "p", padIdx: 2)
        XCTAssertEqual(store.effects(packId: "p", padIdx: 2), fx)
        XCTAssertNil(store.effects(packId: "p", padIdx: 3))
        store.setEffects(nil, packId: "p", padIdx: 2)
        XCTAssertNil(store.effects(packId: "p", padIdx: 2))
    }

    func testWritesClampOutOfRangeValues() {
        let wild = SamplePadEffects(
            delayTimeSec: 99, delayFeedback: 500, delayMix: -10,
            filterCutoffHz: 5, filterResonanceDb: 100)
        store.setEffects(wild, packId: "p", padIdx: 0)
        XCTAssertEqual(store.effects(packId: "p", padIdx: 0), wild.clamped())
    }

    func testReplaceAllSwapsWholeMap() {
        store.setEffects(.neutral, packId: "old", padIdx: 0)
        let fx = SamplePadEffects(
            delayTimeSec: 0.2, delayFeedback: 10, delayMix: 15,
            filterCutoffHz: 8_000, filterResonanceDb: 1)
        store.replaceAll(["new#4": fx])
        XCTAssertNil(store.effects(packId: "old", padIdx: 0))
        XCTAssertEqual(store.effectsByKey, ["new#4": fx])
        store.replaceAll([:])
        XCTAssertTrue(store.effectsByKey.isEmpty)
    }

    func testDiskRoundTrip() {
        let fx = SamplePadEffects(
            delayTimeSec: 0.6, delayFeedback: 20, delayMix: 40,
            filterCutoffHz: 1_500, filterResonanceDb: 9)
        store.setEffects(fx, packId: "kit:x", padIdx: 11)
        let reloaded = PadFXStore(root: tempDir)
        XCTAssertEqual(reloaded.effects(packId: "kit:x", padIdx: 11), fx)
    }

    func testOnChangedFires() {
        var fired = 0
        store.onChanged = { fired += 1 }
        store.setEffects(.neutral, packId: "p", padIdx: 0)
        store.replaceAll([:])
        XCTAssertEqual(fired, 2)
    }
}
