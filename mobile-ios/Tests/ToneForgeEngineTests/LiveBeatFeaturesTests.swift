// LiveBeatFeaturesTests.swift
//
// Guards the Live Beat classification fix: a low body-thump must resolve
// as low-frequency energy and land nearer the kick template than the
// snare. The bug was a 128-sample window (375 Hz FFT bins) that folded
// the whole sub-500 Hz band into one bin, so low taps read as broadband
// and misfired as snare. The window is now 1024 samples (47 Hz bins).

import XCTest
@testable import ToneForgeEngine

final class LiveBeatFeaturesTests: XCTestCase {

    private let sampleRate = 48_000.0

    /// A pure sine of `freq` Hz over one feature window.
    private func sine(_ freq: Double) -> [Float] {
        let n = LiveBeatFeatures.windowSize
        return (0..<n).map { i in
            Float(sin(2 * Double.pi * freq * Double(i) / sampleRate))
        }
    }

    /// Nearest default template to a feature vector, by the same weighted
    /// distance the matcher uses. Independent of maxDistance/confidence.
    private func nearestRole(_ f: LiveBeatFeatures) -> DrumRole {
        LiveBeatProfile.heuristicDefault.templates
            .min { a, b in
                f.distance(to: a.features, variance: a.variance)
                    < f.distance(to: b.features, variance: b.variance)
            }!
            .role
    }

    func testWindowIsLargeEnoughToResolveLowBand() {
        // 1024 @ 48kHz = 47 Hz bins, ~10 bins under 500 Hz.
        XCTAssertEqual(LiveBeatFeatures.windowSize, 1024)
    }

    func testLowThumpResolvesAsLowEnergy() throws {
        let f = try XCTUnwrap(
            LiveBeatFeatures.extract(from: sine(110), sampleRate: sampleRate)
        )
        // The whole point of the bigger window: a 110 Hz tone is now
        // clearly low, not smeared broadband.
        XCTAssertLessThan(f.centroidNorm, 0.25, "110 Hz should read dark")
        XCTAssertGreaterThan(f.lowRatio, 0.4, "110 Hz should read low-band heavy")
        XCTAssertTrue(f.centroidNorm.isFinite && f.lowRatio.isFinite
                      && f.zcr.isFinite && f.crestFactor.isFinite)
    }

    func testLowThumpMatchesKickNotSnare() throws {
        let f = try XCTUnwrap(
            LiveBeatFeatures.extract(from: sine(110), sampleRate: sampleRate)
        )
        // Regression: this used to land on snare.
        XCTAssertEqual(nearestRole(f), .kick)
    }

    func testBrightTapMatchesHatNotKick() throws {
        let f = try XCTUnwrap(
            LiveBeatFeatures.extract(from: sine(8_000), sampleRate: sampleRate)
        )
        let role = nearestRole(f)
        XCTAssertEqual(role, .closedHat)
        XCTAssertNotEqual(role, .kick)
    }
}
