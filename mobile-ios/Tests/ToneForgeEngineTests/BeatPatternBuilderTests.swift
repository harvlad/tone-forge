// BeatPatternBuilderTests.swift
//
// Beat Capture (D-024): hits → editable SequencerPattern.

import XCTest
@testable import ToneForgeEngine

final class BeatPatternBuilderTests: XCTestCase {

    private func hit(_ role: DrumRole, _ time: Double, vel: Float = 0.8)
        -> DetectedHit {
        DetectedHit(
            timeSec: time, role: role, confidence: 0.9, velocity: vel,
            features: OnsetFeatures(
                centroidHz: 0, zcr: 0, attackSec: 0, durationSec: 0,
                pitchedness: 0, lowBandRatio: 0, peakRMS: vel
            )
        )
    }

    func testOneTrackPerRole() {
        let hits = [
            hit(.kick, 0.0), hit(.kick, 1.0),
            hit(.snare, 0.5), hit(.snare, 1.5),
            hit(.closedHat, 0.25),
        ]
        let pattern = BeatPatternBuilder.build(
            hits: hits, bpm: 120, quantize: .sixteenth, songSynced: false
        )
        XCTAssertEqual(pattern.tracks.count, 3)
        let names = Set(pattern.tracks.compactMap(\.name))
        XCTAssertEqual(names, ["Kick", "Snare", "Closed Hat"])
    }

    func testRoleOrderKickFirst() {
        let hits = [hit(.perc, 0.0), hit(.kick, 0.0), hit(.snare, 0.0)]
        let pattern = BeatPatternBuilder.build(
            hits: hits, bpm: 120, quantize: .sixteenth, songSynced: false
        )
        XCTAssertEqual(pattern.tracks.first?.name, "Kick")
    }

    func testStepPlacementAt120BPM() {
        // 120 BPM → 16th = 0.125 s. Kick at 0 and 0.5 s → steps 0 and 4.
        let hits = [hit(.kick, 0.0), hit(.kick, 0.5)]
        let pattern = BeatPatternBuilder.build(
            hits: hits, bpm: 120, quantize: .sixteenth, songSynced: false
        )
        let kick = try? XCTUnwrap(pattern.tracks.first)
        XCTAssertTrue(kick?.steps[0].isActive ?? false)
        XCTAssertTrue(kick?.steps[4].isActive ?? false)
        XCTAssertFalse(kick?.steps[1].isActive ?? true)
    }

    func testVelocityCarried() {
        let hits = [hit(.kick, 0.0, vel: 0.42)]
        let pattern = BeatPatternBuilder.build(
            hits: hits, bpm: 120, quantize: .sixteenth, songSynced: false
        )
        XCTAssertEqual(pattern.tracks.first?.steps[0].velocity ?? 0,
                       0.42, accuracy: 0.001)
    }

    func testBpmOverrideStandaloneOnly() {
        let hits = [hit(.kick, 0.0)]
        let standalone = BeatPatternBuilder.build(
            hits: hits, bpm: 128, quantize: .sixteenth, songSynced: false
        )
        XCTAssertEqual(standalone.bpmOverride ?? 0, 128, accuracy: 0.001)

        let synced = BeatPatternBuilder.build(
            hits: hits, bpm: 128, quantize: .sixteenth, songSynced: true
        )
        XCTAssertNil(synced.bpmOverride)
    }

    func testQuarterQuantizeSnapsToBeat() {
        // A hit near step 5 (0.625 s) snaps to step 4 under 1/4.
        let hits = [hit(.snare, 0.625)]
        let pattern = BeatPatternBuilder.build(
            hits: hits, bpm: 120, quantize: .quarter, songSynced: false
        )
        let snare = pattern.tracks.first
        XCTAssertTrue(snare?.steps[4].isActive ?? false)
        XCTAssertFalse(snare?.steps[5].isActive ?? true)
    }

    func testStepCountGrowsForLongPerformance() {
        // Hit past 2 s at 120 BPM (16th = 0.125) → step 16 → needs 32.
        let hits = [hit(.kick, 0.0), hit(.kick, 2.1)]
        let pattern = BeatPatternBuilder.build(
            hits: hits, bpm: 120, quantize: .sixteenth, songSynced: false
        )
        XCTAssertEqual(pattern.stepCount, .thirtyTwo)
    }

    func testEmptyHitsProducesEmptyPattern() {
        let pattern = BeatPatternBuilder.build(
            hits: [], bpm: 120, quantize: .keep, songSynced: false
        )
        XCTAssertTrue(pattern.tracks.isEmpty)
    }
}
