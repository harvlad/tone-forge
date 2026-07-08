// FXSettingsTests.swift
//
// Unit tests for FXSettings (D-022 Phase 6): codable round-trip,
// clamping, neutral detection, and preset catalog uniqueness.

import XCTest
@testable import ToneForgeEngine

final class FXSettingsTests: XCTestCase {

    // MARK: - Neutral detection

    func testNeutralEQ() {
        XCTAssertTrue(FXEQParams.neutral.isNeutral)
        var eq = FXEQParams.neutral
        eq.lowGainDb = 0.1
        XCTAssertFalse(eq.isNeutral)
    }

    func testNeutralComp() {
        XCTAssertTrue(FXCompParams.neutral.isNeutral)
        var comp = FXCompParams.neutral
        comp.amountDb = 5
        XCTAssertFalse(comp.isNeutral)
    }

    func testNeutralReverb() {
        XCTAssertTrue(FXReverbParams.neutral.isNeutral)
        var rev = FXReverbParams.neutral
        rev.mix = 10
        XCTAssertFalse(rev.isNeutral)
    }

    func testNeutralDelay() {
        XCTAssertTrue(FXDelayParams.neutral.isNeutral)
        var delay = FXDelayParams.neutral
        delay.mix = 15
        XCTAssertFalse(delay.isNeutral)
    }

    func testNeutralSettings() {
        XCTAssertTrue(FXSettings.neutral.isNeutral)
        var s = FXSettings.neutral
        s.eq.lowGainDb = 2
        XCTAssertFalse(s.isNeutral)
    }

    // MARK: - Clamping

    func testEQClamping() {
        let eq = FXEQParams(
            lowFreq: -100,
            lowGainDb: 50,
            midFreq: 50000,
            midGainDb: -50,
            highFreq: 0,
            highGainDb: 30
        ).clamped()
        XCTAssertEqual(eq.lowFreq, 20)
        XCTAssertEqual(eq.lowGainDb, 24)
        XCTAssertEqual(eq.midFreq, 8000)
        XCTAssertEqual(eq.midGainDb, -24)
        XCTAssertEqual(eq.highFreq, 1000)
        XCTAssertEqual(eq.highGainDb, 24)
    }

    func testCompClamping() {
        let comp = FXCompParams(
            thresholdDb: -100,
            amountDb: 100,
            attackMs: 0,
            releaseMs: 5000,
            makeupDb: -10
        ).clamped()
        XCTAssertEqual(comp.thresholdDb, -60)
        XCTAssertEqual(comp.amountDb, 40)
        XCTAssertEqual(comp.attackMs, 0.1)
        XCTAssertEqual(comp.releaseMs, 3000)
        XCTAssertEqual(comp.makeupDb, 0)
    }

    func testReverbClamping() {
        let rev = FXReverbParams(
            mix: 150,
            sizeSeconds: 10,
            dampPercent: -10
        ).clamped()
        XCTAssertEqual(rev.mix, 100)
        XCTAssertEqual(rev.sizeSeconds, 6)
        XCTAssertEqual(rev.dampPercent, 0)
    }

    func testDelayClamping() {
        let delay = FXDelayParams(
            timeSec: 5,
            feedback: 100,
            mix: -20
        ).clamped()
        XCTAssertEqual(delay.timeSec, 2)
        XCTAssertEqual(delay.feedback, 95)
        XCTAssertEqual(delay.mix, 0)
    }

    func testSettingsClamping() {
        var s = FXSettings.neutral
        s.fxReturnDb = 20
        let clamped = s.clamped()
        XCTAssertEqual(clamped.fxReturnDb, 6)
    }

    // MARK: - Codable round-trip

    func testEQCodableRoundTrip() throws {
        let eq = FXEQParams(lowFreq: 300, lowGainDb: 3, midFreq: 2000, midGainDb: -2, highFreq: 8000, highGainDb: 4)
        let data = try JSONEncoder().encode(eq)
        let decoded = try JSONDecoder().decode(FXEQParams.self, from: data)
        XCTAssertEqual(eq, decoded)
    }

    func testSettingsCodableRoundTrip() throws {
        var s = FXSettings.neutral
        s.eq.lowGainDb = 2
        s.comp.amountDb = 10
        s.reverb.mix = 30
        s.delay.mix = 20
        s.fxReturnDb = -6
        s.presetId = nil

        let data = try JSONEncoder().encode(s)
        let decoded = try JSONDecoder().decode(FXSettings.self, from: data)
        XCTAssertEqual(s, decoded)
    }

    func testSettingsDecodesWithMissingKeys() throws {
        // Minimal JSON — all optional fields should fall back to neutral
        let json = Data(#"{"schemaVersion":1}"#.utf8)
        let s = try JSONDecoder().decode(FXSettings.self, from: json)
        XCTAssertTrue(s.eq.isNeutral)
        XCTAssertTrue(s.comp.isNeutral)
        XCTAssertTrue(s.reverb.isNeutral)
        XCTAssertTrue(s.delay.isNeutral)
    }

    // MARK: - Preset catalog

    func testPresetCatalogHasUniqueIds() {
        let ids = FXPresetCatalog.all.map(\.id)
        XCTAssertEqual(ids.count, Set(ids).count, "preset IDs must be unique")
    }

    func testPresetCatalogHasClean() {
        let clean = FXPresetCatalog.preset(id: "clean")
        XCTAssertNotNil(clean)
        XCTAssertTrue(clean!.settings.isNeutral)
    }

    func testAllPresetsHavePresetIdSet() {
        for preset in FXPresetCatalog.all {
            XCTAssertEqual(preset.settings.presetId, preset.id, "\(preset.name) should have presetId matching its id")
        }
    }
}
