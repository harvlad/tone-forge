// JamScreenSnapshotTests.swift
//
// Golden-PNG snapshots of the Jam tab (D-022) at the two reference
// devices from the plan:
//
//   iPad Pro 12.9"   1024×1366 pt
//   iPhone 15 Pro     393×852 pt
//
// The AppState is seeded with a deterministic fixture bundle — no
// audio boot, no network. Goldens live in Fixtures/Goldens/ and are
// recorded via the TEST_RUNNER_TONEFORGE_SNAPSHOT_* env vars (see
// SnapshotAsserting.swift). On macOS `swift test` these skip: the
// harness renders through UIKit and goldens are pinned to an iOS
// simulator runtime.

import XCTest
import SwiftUI
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class JamScreenSnapshotTests: XCTestCase {

    func testPlayScreenIPadPro12_9() throws {
        #if canImport(UIKit)
        try assertSnapshot(
            of: makePlayScreen(),
            size: CGSize(width: 1024, height: 1366),
            named: "jam-ipad-pro-12-9"
        )
        #else
        throw XCTSkip("Snapshot tests need UIKit — run via xcodebuild test on an iOS simulator.")
        #endif
    }

    func testPlayScreenIPhone15Pro() throws {
        #if canImport(UIKit)
        try assertSnapshot(
            of: makePlayScreen(),
            size: CGSize(width: 393, height: 852),
            named: "jam-iphone-15-pro"
        )
        #else
        throw XCTSkip("Snapshot tests need UIKit — run via xcodebuild test on an iOS simulator.")
        #endif
    }

    // MARK: - Fixture

    /// JamTabView over an AppState with a song loaded but no audio /
    /// network activity. `currentBundle` is assigned directly (not via
    /// `activate`) so nothing downloads. The persisted pad mode is
    /// pinned to `.pads` (and Hold off) so a leaked blob from another
    /// suite or a previous run can't flip the surface (D-012 trap).
    private func makePlayScreen() -> some View {
        let appState = AppState()
        appState.currentBundle = Self.fixtureBundle
        appState.jamSettings.padMode = .pads
        appState.jamSettings.holdEnabled = false
        return JamTabView().environmentObject(appState)
    }

    /// Deterministic bundle exercising the header (title/key/tempo),
    /// section chips, and the chord timeline. No stems — the grids
    /// render their unloaded/placeholder states.
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
