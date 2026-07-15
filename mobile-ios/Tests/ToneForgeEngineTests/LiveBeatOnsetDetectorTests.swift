// LiveBeatOnsetDetectorTests.swift
//
// Guards the onset detector's re-arm behaviour: it must fire once per tap
// even when input gain lifts the noise floor above `offThreshold` (the
// "only one hit ever registered" bug), yet fire exactly once for a
// sustained tone (no machine-gun retrigger / speaker feedback cascade).

import XCTest
@testable import ToneForgeEngine

final class LiveBeatOnsetDetectorTests: XCTestCase {

    private let frame = 512

    /// Drive the detector with a sequence of per-buffer RMS levels and
    /// count the onsets it reports.
    private func countOnsets(_ rmsSequence: [Float], config: LiveBeatOnsetConfig) -> Int {
        var detector = LiveBeatOnsetDetector(config: config)
        var onsets = 0
        for rms in rmsSequence where detector.process(rms: rms, sampleCount: frame) {
            onsets += 1
        }
        return onsets
    }

    /// Four decaying taps sitting on a noise floor ABOVE `offThreshold`.
    /// Pre-fix (absolute-floor re-arm only) the envelope never dipped below
    /// `offThreshold`, so the detector latched after the first hit and only
    /// one onset registered. The peak-relative re-arm must recover all four.
    func testMultipleTapsOverElevatedNoiseFloor() {
        let config = LiveBeatOnsetConfig.desktop
        // Floor 0.008 > offThreshold 0.006. Each tap: an attack buffer then
        // decay back to the floor, spaced well past the refractory.
        let floor: Float = 0.008
        let taps = 4
        var seq: [Float] = []
        for _ in 0..<taps {
            seq.append(0.18)               // attack
            seq.append(0.12)               // early decay
            seq.append(contentsOf: Array(repeating: floor, count: 24))  // rest
        }

        XCTAssertEqual(countOnsets(seq, config: config), taps)
    }

    /// A tone that stays loud (a ringing sample bleeding from the speakers,
    /// held music) must fire exactly once — the envelope never decays to a
    /// fraction of its own peak, so the detector stays latched.
    func testSustainedToneFiresOnce() {
        let config = LiveBeatOnsetConfig.desktop
        let seq = Array(repeating: Float(0.2), count: 200)  // never releases
        XCTAssertEqual(countOnsets(seq, config: config), 1)
    }
}
