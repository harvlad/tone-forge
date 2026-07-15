// LiveBeatGuidedCaptureTests.swift
//
// Guards deterministic segmentation of a guided ("tap-along") calibration
// take: given a continuous buffer with taps placed near known beat times,
// the extractor must return one hit per beat, ignore silent beats (missed
// taps), and pull each hit's features from the tap — not the surrounding
// silence.

import XCTest
@testable import ToneForgeEngine

final class LiveBeatGuidedCaptureTests: XCTestCase {

    private let sampleRate = 48_000.0

    /// A short decaying burst of `freq` Hz written into `buffer` at
    /// `offset`, modelling a percussive tap.
    private func writeTap(
        into buffer: inout [Float], at offset: Int, freq: Double, amp: Float
    ) {
        let len = 4096
        for i in 0..<len where offset + i < buffer.count {
            let env = Float(exp(-Double(i) / 1500))
            let s = Float(sin(2 * Double.pi * freq * Double(i) / sampleRate))
            buffer[offset + i] += amp * env * s
        }
    }

    func testOneHitPerBeat() {
        // 6 beats, 0.5 s apart. Tap lands ~80 ms after each beat (reaction).
        let beatTimes = (0..<6).map { Double($0) * 0.5 + 0.5 }
        var buffer = [Float](repeating: 0, count: Int(4.0 * sampleRate))
        let reaction = Int(0.08 * sampleRate)
        for t in beatTimes {
            writeTap(into: &buffer, at: Int(t * sampleRate) + reaction, freq: 120, amp: 0.8)
        }

        let hits = LiveBeatGuidedCapture.extractHits(
            from: buffer, sampleRate: sampleRate, expectedTimes: beatTimes
        )
        XCTAssertEqual(hits.count, beatTimes.count)
    }

    func testSilentBeatDropped() {
        // 6 beats but the user misses beat 3 (no tap written there).
        let beatTimes = (0..<6).map { Double($0) * 0.5 + 0.5 }
        var buffer = [Float](repeating: 0, count: Int(4.0 * sampleRate))
        for (i, t) in beatTimes.enumerated() where i != 2 {
            writeTap(into: &buffer, at: Int(t * sampleRate), freq: 120, amp: 0.8)
        }

        let hits = LiveBeatGuidedCapture.extractHits(
            from: buffer, sampleRate: sampleRate, expectedTimes: beatTimes
        )
        XCTAssertEqual(hits.count, beatTimes.count - 1)
    }

    func testHitFeaturesTrackTapTimbre() throws {
        // A low tap and a bright tap on two beats should segment into
        // features whose low-band ratio separates them.
        let beatTimes = [0.5, 1.0]
        var buffer = [Float](repeating: 0, count: Int(2.0 * sampleRate))
        writeTap(into: &buffer, at: Int(0.5 * sampleRate), freq: 100, amp: 0.8)
        writeTap(into: &buffer, at: Int(1.0 * sampleRate), freq: 5000, amp: 0.8)

        let hits = LiveBeatGuidedCapture.extractHits(
            from: buffer, sampleRate: sampleRate, expectedTimes: beatTimes
        )
        XCTAssertEqual(hits.count, 2)
        XCTAssertGreaterThan(hits[0].features.lowRatio, hits[1].features.lowRatio)
    }

    func testEmptyInputs() {
        XCTAssertTrue(
            LiveBeatGuidedCapture.extractHits(
                from: [], sampleRate: sampleRate, expectedTimes: [0.5]
            ).isEmpty
        )
        XCTAssertTrue(
            LiveBeatGuidedCapture.extractHits(
                from: [Float](repeating: 0, count: 1000),
                sampleRate: sampleRate, expectedTimes: []
            ).isEmpty
        )
    }
}
