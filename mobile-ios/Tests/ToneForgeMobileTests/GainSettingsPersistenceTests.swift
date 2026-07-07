// GainSettingsPersistenceTests.swift
//
// Coverage for the voice/chop/vocoder level settings + appModeRaw on
// `SampleSettingsStore`:
//   - spec defaults (voice 0.9, chops 0.55, vocoder 0.4, mode
//     "sample") on a fresh store;
//   - values persist across a store re-init (round trip);
//   - blobs written before the newer keys existed still decode,
//     falling back to the defaults without disturbing older fields;
//   - out-of-range gain values are clamped to [0, 1] on save.

import XCTest
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class GainSettingsPersistenceTests: XCTestCase {

    private var defaults: UserDefaults!
    private let suiteName = "toneforge.tests.gains"
    private let blobKey = "toneforge.sampleSettings"

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

    func testFreshStoreUsesSpecDefaults() {
        let store = SampleSettingsStore(defaults: defaults)

        XCTAssertEqual(store.voiceGainLinear, 0.9)
        XCTAssertEqual(store.chopGainLinear, 0.55)
        XCTAssertEqual(store.vocoderGainLinear, 0.4)
        XCTAssertEqual(store.appModeRaw, "sample")
        XCTAssertEqual(SampleSettingsStore.defaultVoiceGain, 0.9)
        XCTAssertEqual(SampleSettingsStore.defaultChopGain, 0.55)
        XCTAssertEqual(SampleSettingsStore.defaultVocoderGain, 0.4)
        XCTAssertEqual(SampleSettingsStore.defaultAppModeRaw, "sample")
    }

    func testGainsPersistAcrossStoreInit() {
        let store1 = SampleSettingsStore(defaults: defaults)
        store1.voiceGainLinear = 0.42
        store1.chopGainLinear = 0.77
        store1.vocoderGainLinear = 0.61
        store1.appModeRaw = "hybrid"

        let store2 = SampleSettingsStore(defaults: defaults)

        XCTAssertEqual(store2.voiceGainLinear, 0.42, accuracy: 1e-9)
        XCTAssertEqual(store2.chopGainLinear, 0.77, accuracy: 1e-9)
        XCTAssertEqual(store2.vocoderGainLinear, 0.61, accuracy: 1e-9)
        XCTAssertEqual(store2.appModeRaw, "hybrid")
    }

    func testOldBlobWithoutGainKeysDecodesWithDefaults() throws {
        // Simulate a blob written before the gain sliders landed:
        // write current-shape settings, then strip the new keys.
        let seed = SampleSettingsStore(defaults: defaults)
        seed.currentPackId = "legacy-pack"
        seed.layerFaderDb = -6

        let data = try XCTUnwrap(defaults.data(forKey: blobKey))
        var json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        json.removeValue(forKey: "voiceGainLinear")
        json.removeValue(forKey: "chopGainLinear")
        json.removeValue(forKey: "vocoderGainLinear")
        json.removeValue(forKey: "appModeRaw")
        defaults.set(try JSONSerialization.data(withJSONObject: json), forKey: blobKey)

        let store = SampleSettingsStore(defaults: defaults)

        // New fields fall back to defaults…
        XCTAssertEqual(store.voiceGainLinear, 0.9)
        XCTAssertEqual(store.chopGainLinear, 0.55)
        XCTAssertEqual(store.vocoderGainLinear, 0.4)
        XCTAssertEqual(store.appModeRaw, "sample")
        // …without disturbing the old blob's fields.
        XCTAssertEqual(store.currentPackId, "legacy-pack")
        XCTAssertEqual(store.layerFaderDb, -6)
    }

    func testGainsClampedToUnitRangeOnSave() {
        let store1 = SampleSettingsStore(defaults: defaults)
        store1.voiceGainLinear = 3.0
        store1.chopGainLinear = -1.0
        store1.vocoderGainLinear = 2.5

        let store2 = SampleSettingsStore(defaults: defaults)

        XCTAssertEqual(store2.voiceGainLinear, 1.0)
        XCTAssertEqual(store2.chopGainLinear, 0.0)
        XCTAssertEqual(store2.vocoderGainLinear, 1.0)
    }
}
