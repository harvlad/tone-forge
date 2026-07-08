// SynthPresetsTests.swift
//
// Redesign Phase 6: the new sawMix/detuneCents knobs must default to
// the historic sound, "Dreamy Lead" must be exactly those defaults,
// and the preset catalog must have stable unique ids (settings stores
// persist the id).

import XCTest
@testable import ToneForgeMobile

final class PadSynthParamsDefaultsTests: XCTestCase {

    func testDefaultsReproduceHistoricSound() {
        let p = PadSynthParams()
        XCTAssertEqual(p.masterGain, 0.311)
        XCTAssertEqual(p.brightness, 1.0)
        XCTAssertEqual(p.strumMs, 15)
        XCTAssertEqual(p.attackMs, 6)
        XCTAssertEqual(p.releaseSec, 2.5)
        // New knobs at their neutral values: 0.5 saw/tri balance is
        // byte-identical to the old (saw + tri) * 0.55 mix, ±6 cents
        // matches the old hard-coded detune.
        XCTAssertEqual(p.sawMix, 0.5)
        XCTAssertEqual(p.detuneCents, 6.0)
    }

    func testMemberwiseInitDefaultsMatchEmptyInit() {
        XCTAssertEqual(PadSynthParams(), PadSynthParams(masterGain: 0.311))
    }
}

final class SynthPresetsTests: XCTestCase {

    func testDreamyLeadIsTheDefaultSound() {
        XCTAssertEqual(SynthPresetCatalog.dreamyLead.params, PadSynthParams())
        XCTAssertEqual(SynthPresetCatalog.defaultPreset.id, "dreamyLead")
        XCTAssertEqual(SynthPresetCatalog.all.first?.id, "dreamyLead")
    }

    func testPresetIdsAreUnique() {
        let ids = SynthPresetCatalog.all.map(\.id)
        XCTAssertEqual(Set(ids).count, ids.count)
        XCTAssertEqual(SynthPresetCatalog.all.count, 5)
    }

    func testLookupById() {
        XCTAssertEqual(SynthPresetCatalog.preset(id: "warmPad")?.name, "Warm Pad")
        XCTAssertEqual(SynthPresetCatalog.preset(id: "deepBass")?.params.strumMs, 0)
        XCTAssertNil(SynthPresetCatalog.preset(id: "nope"))
    }

    func testEveryPresetKeepsTheBusTrim() {
        // masterGain is the fixed 0.311 voice-bus trim (D-010/D-013),
        // not a per-preset loudness knob.
        for preset in SynthPresetCatalog.all {
            XCTAssertEqual(preset.params.masterGain, 0.311, "\(preset.id)")
        }
    }

    func testPresetsAudiblyDiffer() {
        // Each preset must differ from the default in at least one
        // voice knob (otherwise the picker is lying).
        for preset in SynthPresetCatalog.all where preset.id != "dreamyLead" {
            XCTAssertNotEqual(preset.params, PadSynthParams(), "\(preset.id)")
        }
    }
}
