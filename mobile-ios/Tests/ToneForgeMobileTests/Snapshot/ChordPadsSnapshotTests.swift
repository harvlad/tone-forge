// ChordPadsSnapshotTests.swift
//
// Golden-PNG snapshots of the Chord Pads surface (Phase 12) — the
// current-chord strip, the [Momentary | Latch] + octave controls,
// and the 4×4 diatonic chord grid for the D-minor fixture
// (Dm/Gm/Bb/C timeline per the mockup). Rendered directly: the
// standalone surface has no tab of its own under D-022 (it folds
// into Jam as a pad-mode toggle in Phase 5).
//
// Same harness as the other snapshot suites: deterministic fixture
// bundle, no audio boot, no network; goldens recorded via the
// TEST_RUNNER_TONEFORGE_SNAPSHOT_* env vars, skipped on macOS
// `swift test`.

import XCTest
import SwiftUI
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class ChordPadsSnapshotTests: XCTestCase {

    func testChordPadsIPadPro12_9() throws {
        #if canImport(UIKit)
        try assertSnapshot(
            of: makeChordPadsScreen(),
            size: CGSize(width: 1024, height: 1366),
            named: "chordpads-ipad-pro-12-9"
        )
        #else
        throw XCTSkip("Snapshot tests need UIKit — run via xcodebuild test on an iOS simulator.")
        #endif
    }

    func testChordPadsIPhone15Pro() throws {
        #if canImport(UIKit)
        try assertSnapshot(
            of: makeChordPadsScreen(),
            size: CGSize(width: 393, height: 852),
            named: "chordpads-iphone-15-pro"
        )
        #else
        throw XCTSkip("Snapshot tests need UIKit — run via xcodebuild test on an iOS simulator.")
        #endif
    }

    // MARK: - Fixture

    /// ChordPadsView over an AppState with the D-minor fixture
    /// loaded. Rendered without tab chrome — the surface is reachable
    /// only through Jam once the Phase 5 toggle lands.
    private func makeChordPadsScreen() -> some View {
        let appState = AppState()
        appState.currentBundle = Self.fixtureBundle
        return ChordPadsView(
            controller: appState.chordPadController,
            sampleSettings: appState.sampleSettings
        )
        .background(TFTheme.background.ignoresSafeArea())
        .environmentObject(appState)
    }

    private static let fixtureBundle = SongBundle(
        bundleVersion: 1,
        analysisId: "snapshot-fixture-chordpads",
        meta: BundleMeta(
            title: "Snapshot Song",
            artist: "Fixture Artist",
            sourceUrl: "",
            durationSec: 180.0,
            tempoBpm: 120.0,
            detectedKey: "D minor"
        ),
        timeline: BundleTimeline(
            chords: [
                ChordEvent(start: 0, end: 4, symbol: "Dm"),
                ChordEvent(start: 4, end: 8, symbol: "Gm"),
                ChordEvent(start: 8, end: 12, symbol: "Bb"),
                ChordEvent(start: 12, end: 16, symbol: "C"),
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
