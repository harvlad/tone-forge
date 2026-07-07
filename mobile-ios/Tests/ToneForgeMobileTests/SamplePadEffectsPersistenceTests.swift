// SamplePadEffectsPersistenceTests.swift
//
// Coverage for the pad-effect override surface on
// `SampleSettingsStore`:
//   - Setting an override survives a store re-init from the same
//     UserDefaults (persistence round trip).
//   - Setting nil clears the override.
//   - `effectivePadEffects` implements the three-tier fallback
//     (user override > manifest baseline > `.neutral`).
//   - Values are clamped on write so a bad slider drag can't
//     persist an out-of-range delay time.

import XCTest
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class SamplePadEffectsPersistenceTests: XCTestCase {

    private var defaults: UserDefaults!
    private let suiteName = "toneforge.tests.padeffects"

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: suiteName)
        defaults.removePersistentDomain(forName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        super.tearDown()
    }

    func testOverridePersistsAcrossStoreInit() {
        let store1 = SampleSettingsStore(defaults: defaults)
        let fx = SamplePadEffects(
            delayTimeSec: 0.4, delayFeedback: 25, delayMix: 33,
            filterCutoffHz: 1_200, filterResonanceDb: 6
        )
        store1.setPadEffectsOverride(fx, packId: "starter", padIdx: 4)

        // New store from same UserDefaults: override should decode
        // exactly (clamped values are identical since all fields sit
        // inside their allowed range).
        let store2 = SampleSettingsStore(defaults: defaults)
        let seen = store2.padEffectsOverride(packId: "starter", padIdx: 4)
        XCTAssertEqual(seen, fx)
    }

    func testSettingNilClearsOverride() {
        let store = SampleSettingsStore(defaults: defaults)
        let fx = SamplePadEffects.neutral
        store.setPadEffectsOverride(fx, packId: "starter", padIdx: 0)
        XCTAssertNotNil(store.padEffectsOverride(packId: "starter", padIdx: 0))

        store.setPadEffectsOverride(nil, packId: "starter", padIdx: 0)
        XCTAssertNil(store.padEffectsOverride(packId: "starter", padIdx: 0))
    }

    func testEffectivePadEffectsFallbackOrder() {
        let store = SampleSettingsStore(defaults: defaults)
        let manifest = SamplePadEffects(
            delayTimeSec: 0.1, delayFeedback: 10, delayMix: 20,
            filterCutoffHz: 8_000, filterResonanceDb: 2
        )

        // 1) No override, no manifest → neutral.
        XCTAssertEqual(
            store.effectivePadEffects(packId: "p", padIdx: 0, manifestBaseline: nil),
            .neutral
        )

        // 2) No override, manifest present → manifest.
        XCTAssertEqual(
            store.effectivePadEffects(packId: "p", padIdx: 0, manifestBaseline: manifest),
            manifest
        )

        // 3) Override present → override wins.
        let override = SamplePadEffects(
            delayTimeSec: 0.9, delayFeedback: 40, delayMix: 55,
            filterCutoffHz: 500, filterResonanceDb: 4
        )
        store.setPadEffectsOverride(override, packId: "p", padIdx: 0)
        XCTAssertEqual(
            store.effectivePadEffects(packId: "p", padIdx: 0, manifestBaseline: manifest),
            override
        )
    }

    func testOverrideValuesAreClampedOnWrite() {
        let store = SampleSettingsStore(defaults: defaults)
        // Feed absurd values; expect them clamped in the persisted
        // override (a stale UI drag can never crash the audio unit).
        let raw = SamplePadEffects(
            delayTimeSec: 10, delayFeedback: 200, delayMix: -50,
            filterCutoffHz: 999_999, filterResonanceDb: 500
        )
        store.setPadEffectsOverride(raw, packId: "starter", padIdx: 2)
        let seen = store.padEffectsOverride(packId: "starter", padIdx: 2)
        XCTAssertEqual(seen?.delayTimeSec, 2)
        XCTAssertEqual(seen?.delayFeedback, 95)
        XCTAssertEqual(seen?.delayMix, 0)
        XCTAssertEqual(seen?.filterCutoffHz, 20_000)
        XCTAssertEqual(seen?.filterResonanceDb, 24)
    }

    func testKeyFormatIsPackIdHashPadIdx() {
        XCTAssertEqual(
            SampleSettingsStore.padEffectsKey(packId: "starter", padIdx: 7),
            "starter#7"
        )
    }
}
