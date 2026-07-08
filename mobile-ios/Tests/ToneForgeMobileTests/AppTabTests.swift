// AppTabTests.swift
//
// Coverage for the D-022 five-tab shell types:
//   - AppTab raw values round-trip (they persist in appTabRaw);
//   - performance-tab classification (Library deep-link targets);
//   - TabModePolicy maps each tab onto the right engine AppMode and
//     leaves passive tabs (Mixer/Library) alone.
//
// The legacy playSurfaceRaw → appTabRaw migration is covered next to
// the store in ArtworkStoreTests.AppTabPersistenceTests.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

final class AppTabTests: XCTestCase {

    func testRawValuesRoundTrip() {
        for tab in AppTab.allCases {
            XCTAssertEqual(AppTab(rawValue: tab.rawValue), tab)
        }
        XCTAssertNil(AppTab(rawValue: "chordPads"))
    }

    func testPerformanceClassification() {
        XCTAssertTrue(AppTab.learn.isPerformance)
        XCTAssertTrue(AppTab.jam.isPerformance)
        XCTAssertTrue(AppTab.contribute.isPerformance)
        XCTAssertFalse(AppTab.mixer.isPerformance)
        XCTAssertFalse(AppTab.library.isPerformance)
    }

    // MARK: - TabModePolicy

    func testLearnAndJamPinTheirModes() {
        XCTAssertEqual(
            TabModePolicy.mode(for: .learn, lastContributeModeRaw: "sample"),
            .learnSong
        )
        XCTAssertEqual(
            TabModePolicy.mode(for: .jam, lastContributeModeRaw: "hybrid"),
            .jamInKey
        )
    }

    func testContributeRestoresLastGridMode() {
        XCTAssertEqual(
            TabModePolicy.mode(for: .contribute, lastContributeModeRaw: "sample"),
            .sample
        )
        XCTAssertEqual(
            TabModePolicy.mode(for: .contribute, lastContributeModeRaw: "hybrid"),
            .hybrid
        )
    }

    func testContributeFallsBackToSampleForNonGridModes() {
        // Not a contribute-family mode — jam must not leak in.
        XCTAssertEqual(
            TabModePolicy.mode(for: .contribute, lastContributeModeRaw: "jamInKey"),
            .sample
        )
        XCTAssertEqual(
            TabModePolicy.mode(for: .contribute, lastContributeModeRaw: "bogus"),
            .sample
        )
    }

    func testPassiveTabsLeaveModeUntouched() {
        XCTAssertNil(
            TabModePolicy.mode(for: .mixer, lastContributeModeRaw: "sample")
        )
        XCTAssertNil(
            TabModePolicy.mode(for: .library, lastContributeModeRaw: "sample")
        )
    }
}
