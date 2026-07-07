// SamplePadEffectsTests.swift
//
// Coverage for `SamplePadEffects`:
//   - `.neutral` renders as an audibly-transparent config.
//   - `clamped()` pins every field into its documented range.
//   - Codable round-trips symmetrically.
//   - Missing JSON keys degrade to `.neutral` field-by-field.
//   - `SamplePad` decodes cleanly with + without the effects field.

import XCTest
@testable import ToneForgeEngine

final class SamplePadEffectsTests: XCTestCase {

    func testNeutralIsAudiblyOff() {
        let n = SamplePadEffects.neutral
        XCTAssertEqual(n.delayMix, 0)
        XCTAssertEqual(n.delayFeedback, 0)
        XCTAssertEqual(n.filterCutoffHz, 20_000)
        XCTAssertEqual(n.filterResonanceDb, 0)
        XCTAssertTrue(n.isNeutral)
    }

    func testClampedPinsEveryFieldIntoRange() {
        let raw = SamplePadEffects(
            delayTimeSec: 5.0,      // > 2
            delayFeedback: 150,     // > 95
            delayMix: -10,          // < 0
            filterCutoffHz: 50,     // < 100
            filterResonanceDb: 40   // > 24
        )
        let c = raw.clamped()
        XCTAssertEqual(c.delayTimeSec, 2)
        XCTAssertEqual(c.delayFeedback, 95)
        XCTAssertEqual(c.delayMix, 0)
        XCTAssertEqual(c.filterCutoffHz, 100)
        XCTAssertEqual(c.filterResonanceDb, 24)
    }

    func testCodableRoundTrip() throws {
        let orig = SamplePadEffects(
            delayTimeSec: 0.375,
            delayFeedback: 42,
            delayMix: 33,
            filterCutoffHz: 1_800,
            filterResonanceDb: 6.5
        )
        let data = try JSONEncoder().encode(orig)
        let decoded = try JSONDecoder().decode(SamplePadEffects.self, from: data)
        XCTAssertEqual(decoded, orig)
    }

    func testMissingKeysDegradeToNeutralFieldByField() throws {
        // Provide only delayMix; expect the rest to fall back to
        // `.neutral` values instead of failing decode.
        let json = #"{ "delayMix": 60 }"#.data(using: .utf8)!
        let decoded = try JSONDecoder().decode(SamplePadEffects.self, from: json)
        XCTAssertEqual(decoded.delayMix, 60)
        XCTAssertEqual(decoded.delayTimeSec, SamplePadEffects.neutral.delayTimeSec)
        XCTAssertEqual(decoded.delayFeedback, SamplePadEffects.neutral.delayFeedback)
        XCTAssertEqual(decoded.filterCutoffHz, SamplePadEffects.neutral.filterCutoffHz)
        XCTAssertEqual(decoded.filterResonanceDb, SamplePadEffects.neutral.filterResonanceDb)
    }

    func testSamplePadDecodesWithoutEffectsField() throws {
        // A pack manifest written before Phase 6d — no effects key.
        let json = #"""
        {
            "padIdx": 0,
            "name": "Kick",
            "family": "percussion",
            "filename": "00_kick.m4a"
        }
        """#.data(using: .utf8)!
        let pad = try JSONDecoder().decode(SamplePad.self, from: json)
        XCTAssertNil(pad.effects)
    }

    func testSamplePadDecodesWithEffectsField() throws {
        let json = #"""
        {
            "padIdx": 3,
            "name": "Verb Stab",
            "family": "stabs",
            "filename": "03_stab.m4a",
            "effects": {
                "delayTimeSec": 0.5,
                "delayFeedback": 30,
                "delayMix": 25,
                "filterCutoffHz": 4000,
                "filterResonanceDb": 3
            }
        }
        """#.data(using: .utf8)!
        let pad = try JSONDecoder().decode(SamplePad.self, from: json)
        XCTAssertEqual(pad.effects?.delayTimeSec, 0.5)
        XCTAssertEqual(pad.effects?.filterCutoffHz, 4000)
    }

    func testIsNeutralTruePastEpsilon() {
        var e = SamplePadEffects.neutral
        e.delayMix = 1e-12
        XCTAssertTrue(e.isNeutral)
        e.delayMix = 1
        XCTAssertFalse(e.isNeutral)
    }
}
