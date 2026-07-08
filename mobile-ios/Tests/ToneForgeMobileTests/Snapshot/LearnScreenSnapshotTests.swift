// LearnScreenSnapshotTests.swift
//
// Golden-PNG snapshots of the redesigned Learn tab (D-022 Phase 4)
// at the two reference devices. Same deterministic fixture recipe as
// JamScreenSnapshotTests: bundle assigned directly, no audio boot,
// no network. With the transport parked at 0 the NOW card renders
// its empty placeholder and the NEXT card picks the first upcoming
// chord ("F" → fretboard), which exercises both card states plus the
// ring, controls and stat chips.

import XCTest
import SwiftUI
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class LearnScreenSnapshotTests: XCTestCase {

    func testLearnScreenIPadPro12_9() throws {
        #if canImport(UIKit)
        try assertSnapshot(
            of: makeLearnScreen(),
            size: CGSize(width: 1024, height: 1366),
            named: "learn-ipad-pro-12-9"
        )
        #else
        throw XCTSkip("Snapshot tests need UIKit — run via xcodebuild test on an iOS simulator.")
        #endif
    }

    func testLearnScreenIPhone15Pro() throws {
        #if canImport(UIKit)
        try assertSnapshot(
            of: makeLearnScreen(),
            size: CGSize(width: 393, height: 852),
            named: "learn-iphone-15-pro"
        )
        #else
        throw XCTSkip("Snapshot tests need UIKit — run via xcodebuild test on an iOS simulator.")
        #endif
    }

    // MARK: - Fixture

    private func makeLearnScreen() -> some View {
        let appState = AppState()
        appState.currentBundle = Self.fixtureBundle
        return LearnTabView().environmentObject(appState)
    }

    /// Same shape as the Jam fixture: A-minor progression, four
    /// labelled sections, 120 BPM 4/4 grids.
    private static let fixtureBundle = SongBundle(
        bundleVersion: 1,
        analysisId: "snapshot-fixture",
        meta: BundleMeta(
            title: "Snapshot Song",
            artist: "Fixture Artist",
            sourceUrl: "",
            durationSec: 180.0,
            tempoBpm: 120.0,
            detectedKey: "A minor"
        ),
        timeline: BundleTimeline(
            chords: [
                ChordEvent(start: 0, end: 4, symbol: "Am"),
                ChordEvent(start: 4, end: 8, symbol: "F"),
                ChordEvent(start: 8, end: 12, symbol: "C"),
                ChordEvent(start: 12, end: 16, symbol: "G"),
            ],
            sections: [
                SectionEvent(start: 0, end: 30, label: "intro"),
                SectionEvent(start: 30, end: 90, label: "verse"),
                SectionEvent(start: 90, end: 150, label: "chorus"),
                SectionEvent(start: 150, end: 180, label: "outro"),
            ],
            beats: stride(from: 0.0, to: 180.0, by: 0.5).map { $0 },
            downbeats: stride(from: 0.0, to: 180.0, by: 2.0).map { $0 }
        ),
        stems: [],
        presets: [:]
    )
}
