// LearnSettingsStoreTests.swift
//
// D-022: the Learn tab's persisted practice rate. Round-trip through
// an isolated UserDefaults suite, clamping on save, and garbage-blob
// tolerance (falls back to defaults).

import XCTest
@testable import ToneForgeMobile

@MainActor
final class LearnSettingsStoreTests: XCTestCase {

    private var defaults: UserDefaults!
    private let suiteName = "LearnSettingsStoreTests"

    override func setUp() async throws {
        defaults = UserDefaults(suiteName: suiteName)
        defaults.removePersistentDomain(forName: suiteName)
    }

    override func tearDown() async throws {
        defaults.removePersistentDomain(forName: suiteName)
    }

    func testDefaultsToFullSpeed() {
        let store = LearnSettingsStore(defaults: defaults)
        XCTAssertEqual(store.practiceRateX, 1.0)
    }

    func testRoundTrip() {
        let store = LearnSettingsStore(defaults: defaults)
        store.practiceRateX = 0.75
        let reloaded = LearnSettingsStore(defaults: defaults)
        XCTAssertEqual(reloaded.practiceRateX, 0.75)
    }

    func testClampsOnSave() {
        let store = LearnSettingsStore(defaults: defaults)
        store.practiceRateX = 0.1
        var reloaded = LearnSettingsStore(defaults: defaults)
        XCTAssertEqual(reloaded.practiceRateX, 0.5)
        store.practiceRateX = 3.0
        reloaded = LearnSettingsStore(defaults: defaults)
        XCTAssertEqual(reloaded.practiceRateX, 1.0)
    }

    func testGarbageBlobFallsBackToDefaults() {
        defaults.set(Data("not json".utf8), forKey: "toneforge.learnSettings")
        let store = LearnSettingsStore(defaults: defaults)
        XCTAssertEqual(store.practiceRateX, 1.0)
    }

    func testMissingFieldDecodesToDefault() throws {
        // A v1 blob from a build that only knew storeVersion — the
        // decodeIfPresent path must fill practiceRateX.
        defaults.set(
            Data(#"{"storeVersion":1}"#.utf8),
            forKey: "toneforge.learnSettings")
        let store = LearnSettingsStore(defaults: defaults)
        XCTAssertEqual(store.practiceRateX, 1.0)
    }
}
