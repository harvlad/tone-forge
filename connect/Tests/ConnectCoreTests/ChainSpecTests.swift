//
// ChainSpecTests.swift
//
// Pins ChainSpec value semantics: defaults, clamping, and the
// permissive enum parsers. Pure value-type tests — no AVAudioEngine
// is spun up here (same policy as AudioEngineStateTests). The DSP
// graph side of P3b is exercised by integration tests once the WS
// apply_chain handler lands (P3d).
//

import XCTest
@testable import ConnectCore

final class ChainSpecTests: XCTestCase {

    // MARK: - Defaults

    func testBaselineSpecHasSafeFields() {
        // The baseline is what a fresh AudioEngine programs into the
        // graph before any chain has been applied. It must be coherent
        // (every section populated) and audible (no zero gain).
        let spec = ChainSpec.baseline
        XCTAssertEqual(spec.id, "tfc.baseline")
        XCTAssertEqual(spec.input.gainDb, 0)
        XCTAssertEqual(spec.input.highPassHz, 80)
        XCTAssertEqual(spec.gainStage.type, .tubeClean)
        XCTAssertFalse(spec.comp.enabled,
            "Baseline comp should default off so a fresh engine is dry.")
        XCTAssertEqual(spec.reverb.type, .room)
        XCTAssertEqual(spec.output.trimDb, 0)
    }

    func testBaselineSurvivesClamping() {
        // Baseline values are all in-bounds; clamping must be a no-op.
        XCTAssertEqual(ChainSpec.baseline.clamped(), ChainSpec.baseline)
    }

    // MARK: - Clamping per section

    func testInputGainAndHpfAreClampedIntoSafeBand() {
        let exotic = ChainSpec.Input(gainDb: 999, highPassHz: -50).clamped()
        XCTAssertEqual(exotic.gainDb, 12)
        XCTAssertEqual(exotic.highPassHz, 20)

        let other = ChainSpec.Input(gainDb: -999, highPassHz: 99_999).clamped()
        XCTAssertEqual(other.gainDb, -12)
        XCTAssertEqual(other.highPassHz, 500)
    }

    func testGainStageDriveAndBiasClampToUnitInterval() {
        let exotic = ChainSpec.GainStage(
            type: .tubeOverdrive,
            drive: 5.0,
            bias: -1.0
        ).clamped()
        XCTAssertEqual(exotic.drive, 1.0)
        XCTAssertEqual(exotic.bias, 0.0)
        // Type passes through unchanged — only numbers get clamped.
        XCTAssertEqual(exotic.type, .tubeOverdrive)
    }

    func testEQBandsClampToTwelveDbBoth() {
        let exotic = ChainSpec.EQ(
            bassDb: 50, midDb: -50, trebleDb: 13, presenceDb: -13
        ).clamped()
        XCTAssertEqual(exotic.bassDb, 12)
        XCTAssertEqual(exotic.midDb, -12)
        XCTAssertEqual(exotic.trebleDb, 12)
        XCTAssertEqual(exotic.presenceDb, -12)
    }

    func testCompClampsRatioThresholdAndTimes() {
        let exotic = ChainSpec.Comp(
            enabled: true,
            ratio: 0.1,             // below floor
            thresholdDb: 50,        // above ceiling
            attackMs: 9999,         // above ceiling
            releaseMs: 0.001        // below floor
        ).clamped()
        XCTAssertEqual(exotic.ratio, 1.0)
        XCTAssertEqual(exotic.thresholdDb, 0)
        XCTAssertEqual(exotic.attackMs, 200)
        XCTAssertEqual(exotic.releaseMs, 1)
        XCTAssertTrue(exotic.enabled,
            "Enabled flag is structural; clamping must not touch it.")
    }

    func testReverbSizeAndMixClampToUnitInterval() {
        let exotic = ChainSpec.Reverb(
            type: .hall, size: 5.0, mix: -2.0
        ).clamped()
        XCTAssertEqual(exotic.size, 1.0)
        XCTAssertEqual(exotic.mix, 0.0)
        XCTAssertEqual(exotic.type, .hall)
    }

    func testOutputTrimAsymmetricClamp() {
        // Output allows less boost (+6 dB) than cut (-12 dB) so a
        // misbehaving chain can never blow out headphones at unity in.
        let hot = ChainSpec.Output(trimDb: 50).clamped()
        XCTAssertEqual(hot.trimDb, 6)

        let cold = ChainSpec.Output(trimDb: -50).clamped()
        XCTAssertEqual(cold.trimDb, -12)
    }

    // MARK: - Permissive enum parsers

    func testGainStageParseAcceptsValidLabels() {
        XCTAssertEqual(ChainSpec.GainStage.StageType.parse("tube_clean"),
                       .tubeClean)
        XCTAssertEqual(ChainSpec.GainStage.StageType.parse("tube_break"),
                       .tubeBreak)
        XCTAssertEqual(ChainSpec.GainStage.StageType.parse("tube_overdrive"),
                       .tubeOverdrive)
        XCTAssertEqual(ChainSpec.GainStage.StageType.parse("tube_high_gain"),
                       .tubeHighGain)
    }

    func testGainStageParseFallsBackToCleanOnGarbage() {
        // A YAML typo (e.g. "tube_brake") must not produce a high-gain
        // chain by silent default. The safe fall-through is tubeClean.
        XCTAssertEqual(ChainSpec.GainStage.StageType.parse("tube_brake"),
                       .tubeClean)
        XCTAssertEqual(ChainSpec.GainStage.StageType.parse(""),
                       .tubeClean)
        XCTAssertEqual(ChainSpec.GainStage.StageType.parse("nonsense"),
                       .tubeClean)
    }

    func testReverbParseAcceptsValidLabels() {
        XCTAssertEqual(ChainSpec.Reverb.ReverbType.parse("room"), .room)
        XCTAssertEqual(ChainSpec.Reverb.ReverbType.parse("plate"), .plate)
        XCTAssertEqual(ChainSpec.Reverb.ReverbType.parse("spring"), .spring)
        XCTAssertEqual(ChainSpec.Reverb.ReverbType.parse("hall"), .hall)
        XCTAssertEqual(ChainSpec.Reverb.ReverbType.parse("small_hall"),
                       .smallHall)
    }

    func testReverbParseFallsBackToRoomOnGarbage() {
        XCTAssertEqual(ChainSpec.Reverb.ReverbType.parse("cathedral"), .room)
        XCTAssertEqual(ChainSpec.Reverb.ReverbType.parse(""), .room)
    }

    // MARK: - Full spec round-trip

    func testFullSpecClampingProducesAllSafeFields() {
        // Construct an out-of-band-on-every-field spec, clamp once,
        // verify every field landed inside its safe range. Guards the
        // composite clamped() against a future regression where one
        // section forgets to delegate.
        let exotic = ChainSpec(
            id: "tfc.test",
            displayName: "Test",
            input: ChainSpec.Input(gainDb: 100, highPassHz: 10_000),
            gainStage: ChainSpec.GainStage(
                type: .tubeHighGain, drive: 5.0, bias: -1.0
            ),
            eq: ChainSpec.EQ(
                bassDb: 30, midDb: -30, trebleDb: 30, presenceDb: -30
            ),
            comp: ChainSpec.Comp(
                enabled: true,
                ratio: 50,
                thresholdDb: -100,
                attackMs: 0,
                releaseMs: 99_999
            ),
            reverb: ChainSpec.Reverb(type: .plate, size: 9, mix: 9),
            output: ChainSpec.Output(trimDb: 99)
        )

        let safe = exotic.clamped()

        XCTAssertEqual(safe.id, "tfc.test")
        XCTAssertEqual(safe.input.gainDb, 12)
        XCTAssertEqual(safe.input.highPassHz, 500)
        XCTAssertEqual(safe.gainStage.drive, 1.0)
        XCTAssertEqual(safe.gainStage.bias, 0.0)
        XCTAssertEqual(safe.eq.bassDb, 12)
        XCTAssertEqual(safe.eq.midDb, -12)
        XCTAssertEqual(safe.eq.trebleDb, 12)
        XCTAssertEqual(safe.eq.presenceDb, -12)
        XCTAssertEqual(safe.comp.ratio, 20)
        XCTAssertEqual(safe.comp.thresholdDb, -40)
        XCTAssertEqual(safe.comp.attackMs, 0.1)
        XCTAssertEqual(safe.comp.releaseMs, 2000)
        XCTAssertEqual(safe.reverb.size, 1.0)
        XCTAssertEqual(safe.reverb.mix, 1.0)
        XCTAssertEqual(safe.output.trimDb, 6)
    }

    func testClampingIsIdempotent() {
        // Applying clamp twice must equal applying it once. Guards
        // against accidental drift if section bounds ever changed in
        // a way that depended on prior input.
        let exotic = ChainSpec(
            id: "tfc.test",
            displayName: "Test",
            input: ChainSpec.Input(gainDb: 100, highPassHz: -10),
            output: ChainSpec.Output(trimDb: 100)
        )
        let once = exotic.clamped()
        let twice = once.clamped()
        XCTAssertEqual(once, twice)
    }

    // MARK: - Wire decoding
    //
    // These mirror the backend's `_monitor_chain_to_wire` projection in
    // backend/tone_forge_api.py (see also the schema in
    // backend/tone_forge/monitor/README.md). When the wire shape ever
    // changes, both sides update together.

    /// A representative full payload as the backend ships over the WS.
    /// Mirrors the values from `tfc.clean_strat.yaml` so a sync drift
    /// against the bundled chain bank shows up as a test failure.
    private func cleanStratWirePayload() -> [String: Any] {
        return [
            "id": "tfc.clean_strat",
            "family": "clean",
            "display_name": "Clean Strat",
            "description": "Bright, low-noise clean.",
            "parameters": [
                "input": [
                    "gain_db": 0,
                    "high_pass_hz": 80,
                ] as [String: Any],
                "gain_stage": [
                    "type": "tube_clean",
                    "drive": 0.1,
                    "bias": 0.5,
                ] as [String: Any],
                "eq": [
                    "bass_db": 0,
                    "mid_db": -1,
                    "treble_db": 2,
                    "presence_db": 1,
                ] as [String: Any],
                "comp": [
                    "enabled": true,
                    "ratio": 2.0,
                    "threshold_db": -18,
                    "attack_ms": 5,
                    "release_ms": 80,
                ] as [String: Any],
                "reverb": [
                    "type": "room",
                    "size": 0.3,
                    "mix": 0.15,
                ] as [String: Any],
                "output": [
                    "trim_db": 0,
                ] as [String: Any],
            ] as [String: Any],
        ]
    }

    func testDecodeFromWireRoundTripsKnownChain() {
        guard let spec = ChainSpec.decode(fromWireDict: cleanStratWirePayload()) else {
            return XCTFail("Decoder rejected a structurally-valid frame")
        }

        XCTAssertEqual(spec.id, "tfc.clean_strat")
        XCTAssertEqual(spec.displayName, "Clean Strat")
        XCTAssertEqual(spec.input.gainDb, 0)
        XCTAssertEqual(spec.input.highPassHz, 80)
        XCTAssertEqual(spec.gainStage.type, .tubeClean)
        XCTAssertEqual(spec.gainStage.drive, 0.1, accuracy: 0.0001)
        XCTAssertEqual(spec.eq.bassDb, 0)
        XCTAssertEqual(spec.eq.midDb, -1)
        XCTAssertEqual(spec.eq.trebleDb, 2)
        XCTAssertEqual(spec.eq.presenceDb, 1)
        XCTAssertTrue(spec.comp.enabled)
        XCTAssertEqual(spec.comp.ratio, 2.0, accuracy: 0.0001)
        XCTAssertEqual(spec.comp.thresholdDb, -18)
        XCTAssertEqual(spec.comp.attackMs, 5)
        XCTAssertEqual(spec.comp.releaseMs, 80)
        XCTAssertEqual(spec.reverb.type, .room)
        XCTAssertEqual(spec.reverb.size, 0.3, accuracy: 0.0001)
        XCTAssertEqual(spec.reverb.mix, 0.15, accuracy: 0.0001)
        XCTAssertEqual(spec.output.trimDb, 0)
    }

    func testDecodeReturnsNilOnMissingId() {
        var payload = cleanStratWirePayload()
        payload.removeValue(forKey: "id")
        XCTAssertNil(ChainSpec.decode(fromWireDict: payload))
    }

    func testDecodeReturnsNilOnEmptyId() {
        var payload = cleanStratWirePayload()
        payload["id"] = ""
        XCTAssertNil(ChainSpec.decode(fromWireDict: payload))
    }

    func testDecodeReturnsNilOnMissingParameters() {
        var payload = cleanStratWirePayload()
        payload.removeValue(forKey: "parameters")
        XCTAssertNil(ChainSpec.decode(fromWireDict: payload))
    }

    func testDecodeFallsBackToDisplayNameFromIdWhenAbsent() {
        var payload = cleanStratWirePayload()
        payload.removeValue(forKey: "display_name")
        let spec = ChainSpec.decode(fromWireDict: payload)
        XCTAssertEqual(spec?.displayName, "tfc.clean_strat")
    }

    func testDecodeFallsBackToBaselineWhenSectionMissing() {
        // Pop the comp section. The decoder should preserve the rest
        // and fall back to Comp.baseline for the removed slice.
        var payload = cleanStratWirePayload()
        var params = payload["parameters"] as! [String: Any]
        params.removeValue(forKey: "comp")
        payload["parameters"] = params

        let spec = ChainSpec.decode(fromWireDict: payload)
        XCTAssertNotNil(spec)
        XCTAssertEqual(spec?.comp, ChainSpec.Comp.baseline)
        // Other sections still come through.
        XCTAssertEqual(spec?.gainStage.type, .tubeClean)
    }

    func testDecodeAcceptsBothIntAndDoubleForNumbers() {
        // YAML often serializes 0 as an Int and 0.5 as a Double; both
        // arrive through JSONSerialization as NSNumber. The decoder
        // must accept either.
        var payload = cleanStratWirePayload()
        var params = payload["parameters"] as! [String: Any]
        var eq = params["eq"] as! [String: Any]
        eq["bass_db"] = Int(3)            // integer arrival
        eq["mid_db"] = Double(-1.5)       // double arrival
        eq["treble_db"] = NSNumber(value: 2)  // explicit NSNumber
        params["eq"] = eq
        payload["parameters"] = params

        let spec = ChainSpec.decode(fromWireDict: payload)
        XCTAssertEqual(spec?.eq.bassDb, 3)
        XCTAssertEqual(spec?.eq.midDb ?? 0, Float(-1.5), accuracy: Float(0.0001))
        XCTAssertEqual(spec?.eq.trebleDb, 2)
    }

    func testDecodeFallsBackToCleanOnUnknownGainStageType() {
        var payload = cleanStratWirePayload()
        var params = payload["parameters"] as! [String: Any]
        var stage = params["gain_stage"] as! [String: Any]
        stage["type"] = "tube_brake"  // common YAML typo
        params["gain_stage"] = stage
        payload["parameters"] = params

        let spec = ChainSpec.decode(fromWireDict: payload)
        // Safe fall-through: unknown labels become tubeClean, never
        // a high-gain default. Preserves the rest of the stage.
        XCTAssertEqual(spec?.gainStage.type, .tubeClean)
    }

    func testDecodeFallsBackToRoomOnUnknownReverbType() {
        var payload = cleanStratWirePayload()
        var params = payload["parameters"] as! [String: Any]
        var verb = params["reverb"] as! [String: Any]
        verb["type"] = "cathedral"
        params["reverb"] = verb
        payload["parameters"] = params

        let spec = ChainSpec.decode(fromWireDict: payload)
        XCTAssertEqual(spec?.reverb.type, .room)
    }

    func testCoerceFloatHandlesEveryNumberOrigin() {
        XCTAssertEqual(ChainSpec.coerceFloat(Float(1.5)), 1.5)
        XCTAssertEqual(ChainSpec.coerceFloat(Double(2.5)), 2.5)
        XCTAssertEqual(ChainSpec.coerceFloat(Int(3)), 3.0)
        XCTAssertEqual(ChainSpec.coerceFloat(NSNumber(value: 4.5)), 4.5)
        XCTAssertNil(ChainSpec.coerceFloat("loud"))
        XCTAssertNil(ChainSpec.coerceFloat(nil))
    }
}
