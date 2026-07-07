// ChopCapTests.swift
//
// Compliance pin: no chop pad may play more than 8 seconds of source
// audio. The cap is enforced at the single production StemSlice
// construction site (`SampleBank.songDerived`), so these tests cover
// both the clamp primitive and the choke point.

import XCTest
@testable import ToneForgeEngine

final class ChopCapTests: XCTestCase {

    // MARK: - StemSlice.clamped

    func testCapConstantIsEightSeconds() {
        // The spec value. Changing this is a compliance decision, not
        // a refactor — the test exists to make that loud.
        XCTAssertEqual(StemSlice.maxChopDurationSec, 8.0)
    }

    func testLongSliceIsClampedToCapKeepingOnset() {
        let slice = StemSlice(stemRole: "vocals", startSec: 30.0, endSec: 42.0)

        let clamped = slice.clamped()

        XCTAssertEqual(clamped.startSec, 30.0)
        XCTAssertEqual(clamped.endSec, 38.0)
        XCTAssertEqual(clamped.durationSec, 8.0, accuracy: 1e-9)
        XCTAssertEqual(clamped.stemRole, "vocals")
    }

    func testShortSlicePassesThroughUnchanged() {
        let slice = StemSlice(stemRole: "drums", startSec: 1.5, endSec: 4.5)

        XCTAssertEqual(slice.clamped(), slice)
    }

    func testExactlyCapLengthIsUntouched() {
        let slice = StemSlice(stemRole: "other", startSec: 10.0, endSec: 18.0)

        XCTAssertEqual(slice.clamped(), slice)
    }

    func testDegenerateSliceStaysNonNegative() {
        // endSec < startSec should never produce a negative-length or
        // extended window.
        let slice = StemSlice(stemRole: "bass", startSec: 5.0, endSec: 3.0)

        let clamped = slice.clamped()

        XCTAssertEqual(clamped, slice)
        XCTAssertEqual(clamped.durationSec, 0.0)
    }

    func testCustomMaxDuration() {
        let slice = StemSlice(stemRole: "vocals", startSec: 0.0, endSec: 6.0)

        let clamped = slice.clamped(maxDuration: 2.0)

        XCTAssertEqual(clamped.endSec, 2.0)
    }

    // MARK: - Choke point: SampleBank.songDerived

    func testSongDerivedPadsAreCapped() {
        let preset = BundlePreset(
            stem: "vocals",
            sliceMode: "section",
            chops: [
                // 12 s section chop → must be clamped to 8 s.
                Chop(idx: 0, startSec: 20.0, endSec: 32.0, durationSec: 12.0),
                // 3 s chop → untouched.
                Chop(idx: 1, startSec: 40.0, endSec: 43.0, durationSec: 3.0),
            ]
        )

        let resolved = SampleBank.songDerived(
            preset: preset,
            packId: "song-derived:test:sections-vocals",
            name: "Test"
        )

        XCTAssertEqual(resolved.pack.pads.count, 2)

        let long = try? XCTUnwrap(resolved.pack.pads.first(where: { $0.padIdx == 0 })?.stemSlice)
        XCTAssertEqual(long?.startSec, 20.0)
        XCTAssertEqual(long?.endSec, 28.0)
        XCTAssertEqual(long?.durationSec ?? -1, 8.0, accuracy: 1e-9)

        let short = try? XCTUnwrap(resolved.pack.pads.first(where: { $0.padIdx == 1 })?.stemSlice)
        XCTAssertEqual(short?.startSec, 40.0)
        XCTAssertEqual(short?.endSec, 43.0)
    }

    func testEverySongDerivedPadWithinCap() {
        // Sweep a spread of durations; no pad may exceed the cap.
        let chops = (0..<16).map { i in
            Chop(
                idx: i,
                startSec: Double(i) * 10.0,
                endSec: Double(i) * 10.0 + Double(i),  // 0..15 s durations
                durationSec: Double(i)
            )
        }
        let preset = BundlePreset(stem: "drums", sliceMode: "beat", chops: chops)

        let resolved = SampleBank.songDerived(
            preset: preset, packId: "song-derived:test:beat-drums", name: "Sweep"
        )

        for pad in resolved.pack.pads {
            let slice = try? XCTUnwrap(pad.stemSlice)
            XCTAssertLessThanOrEqual(
                slice?.durationSec ?? .infinity,
                StemSlice.maxChopDurationSec,
                "pad \(pad.padIdx) exceeds the 8 s chop cap"
            )
        }
    }
}
