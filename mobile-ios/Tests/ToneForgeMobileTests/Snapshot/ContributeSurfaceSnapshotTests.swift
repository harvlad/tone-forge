// ContributeSurfaceSnapshotTests.swift
//
// Golden-PNG snapshots of the Play (Contribute) screen after the
// Phase 9 restyle — the named 4×4 sample grid, the
// [Instrument | Samples] switch, pack picker row, quantize chips,
// and layer fader. Contribute is the persisted default surface, so
// a plain PlayView over a fresh AppState renders it.
//
// Same harness as JamScreenSnapshotTests: deterministic fixture
// bundle, no audio boot, no network; goldens recorded via the
// TEST_RUNNER_TONEFORGE_SNAPSHOT_* env vars, skipped on macOS
// `swift test`.

import XCTest
import SwiftUI
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class ContributeSurfaceSnapshotTests: XCTestCase {

    func testContributeScreenIPadPro12_9() throws {
        #if canImport(UIKit)
        try assertSnapshot(
            of: makeContributeScreen(),
            size: CGSize(width: 1024, height: 1366),
            named: "contribute-ipad-pro-12-9"
        )
        #else
        throw XCTSkip("Snapshot tests need UIKit — run via xcodebuild test on an iOS simulator.")
        #endif
    }

    func testContributeScreenIPhone15Pro() throws {
        #if canImport(UIKit)
        try assertSnapshot(
            of: makeContributeScreen(),
            size: CGSize(width: 393, height: 852),
            named: "contribute-iphone-15-pro"
        )
        #else
        throw XCTSkip("Snapshot tests need UIKit — run via xcodebuild test on an iOS simulator.")
        #endif
    }

    // MARK: - Fixture

    /// PlayView over an AppState with a song loaded but no audio /
    /// network activity. Contribute is the default surface; sample
    /// mode is the default contribute mode, so the named 4×4 grid
    /// renders (empty "+" tiles — no pack activated).
    private func makeContributeScreen() -> some View {
        let appState = AppState()
        appState.currentBundle = Self.fixtureBundle
        return PlayView().environmentObject(appState)
    }

    private static let fixtureBundle = SongBundle(
        bundleVersion: 1,
        analysisId: "snapshot-fixture-contribute",
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
