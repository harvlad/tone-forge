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
        XCTAssertTrue(AppTab.perform.isPerformance)
        XCTAssertFalse(AppTab.mixer.isPerformance)
        XCTAssertFalse(AppTab.library.isPerformance)
    }

    func testPerformKeepsContributeRawValue() {
        // Persisted appTabRaw = "contribute" must still resolve after
        // Contribute became the lean Perform surface.
        XCTAssertEqual(AppTab.perform.rawValue, "contribute")
        XCTAssertEqual(AppTab(rawValue: "contribute"), .perform)
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

    func testPerformInheritsJamMode() {
        // Perform reuses the Jam instrument, so it pins .jamInKey
        // regardless of the persisted grid raw. The deeper .sample
        // construction grid is entered only via the Instrument Editor.
        XCTAssertEqual(
            TabModePolicy.mode(for: .perform, lastContributeModeRaw: "sample"),
            .jamInKey
        )
        XCTAssertEqual(
            TabModePolicy.mode(for: .perform, lastContributeModeRaw: "bogus"),
            .jamInKey
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
