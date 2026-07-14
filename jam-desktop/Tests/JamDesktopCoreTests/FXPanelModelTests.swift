// FXPanelModelTests.swift
//
// Headless coverage of the master FX intent model: autosave +
// onFXChanged on every commit, knob edits clearing the preset,
// preset application, corrupt-blob recovery.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

final class FXPanelModelTests: XCTestCase {

    private var defaults: UserDefaults!
    private let suite = "FXPanelModelTests"

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
    private func makeModel() -> FXPanelModel {
        FXPanelModel(store: FXSettingsStore(defaults: defaults))
    }

    @MainActor
    func testStartsNeutralWhenNothingPersisted() {
        let model = makeModel()
        XCTAssertEqual(model.settings, .neutral)
        // FXSettings.neutral IS the clean preset.
        XCTAssertEqual(model.presetId, "clean")
    }

    @MainActor
    func testKnobEditClearsPresetFiresCallbackAndPersists() {
        let model = makeModel()
        model.applyPreset(id: "shoegaze")
        XCTAssertNotNil(model.presetId)

        var fired: FXSettings?
        model.onFXChanged = { fired = $0 }

        var eq = model.eq
        eq.lowGainDb = 6
        model.eq = eq

        XCTAssertNil(model.presetId, "knob edit clears preset")
        XCTAssertEqual(fired?.eq.lowGainDb, 6)

        // A fresh model over the same defaults sees the saved state.
        let reloaded = makeModel()
        XCTAssertEqual(reloaded.eq.lowGainDb, 6)
        XCTAssertNil(reloaded.presetId)
    }

    @MainActor
    func testApplyPresetSetsAllParamsAndId() {
        let model = makeModel()
        guard let preset = FXPresetCatalog.preset(id: "shoegaze") else {
            return XCTFail("shoegaze preset missing from catalog")
        }
        var fired: FXSettings?
        model.onFXChanged = { fired = $0 }

        model.applyPreset(preset)

        XCTAssertEqual(model.settings, preset.settings)
        XCTAssertEqual(model.presetId, preset.id)
        XCTAssertEqual(fired, preset.settings)
    }

    @MainActor
    func testApplyUnknownPresetIdFallsBackToClean() {
        let model = makeModel()
        model.applyPreset(id: "no-such-preset")
        XCTAssertEqual(model.settings, FXPresetCatalog.clean.settings)
    }

    @MainActor
    func testResetReturnsToNeutral() {
        let model = makeModel()
        model.applyPreset(id: "shoegaze")
        model.reset()
        XCTAssertEqual(model.settings, .neutral)
        XCTAssertEqual(model.presetId, "clean")
    }

    @MainActor
    func testCorruptBlobFallsBackToNeutral() {
        defaults.set(Data("not json".utf8), forKey: "jamdesktop.fxSettings")
        let model = makeModel()
        XCTAssertEqual(model.settings, .neutral)
    }
}
