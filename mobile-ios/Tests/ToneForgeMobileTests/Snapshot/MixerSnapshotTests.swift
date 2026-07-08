// MixerSnapshotTests.swift
//
// Golden-PNG snapshots of the restyled Mixer sheet (Phase 11) —
// horizontal channel strips with vertical faders, dB readouts, S/M
// buttons, plus the Your Layer and Master strips.
//
// Same harness as the other snapshot suites: deterministic seeded
// state (StemPlayer.seedStemStatesForSnapshot — no audio graph, no
// files), goldens recorded via the TEST_RUNNER_TONEFORGE_SNAPSHOT_*
// env vars, skipped on macOS `swift test`.

import XCTest
import SwiftUI
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class MixerSnapshotTests: XCTestCase {

    func testMixerIPhone15Pro() throws {
        #if canImport(UIKit)
        try assertSnapshot(
            of: makeMixer(),
            size: CGSize(width: 393, height: 852),
            named: "mixer-iphone-15-pro"
        )
        #else
        throw XCTSkip("Snapshot tests need UIKit — run via xcodebuild test on an iOS simulator.")
        #endif
    }

    func testMixerIPadPro12_9() throws {
        #if canImport(UIKit)
        try assertSnapshot(
            of: makeMixer(),
            size: CGSize(width: 1024, height: 1366),
            named: "mixer-ipad-pro-12-9"
        )
        #else
        throw XCTSkip("Snapshot tests need UIKit — run via xcodebuild test on an iOS simulator.")
        #endif
    }

    // MARK: - Fixture

    /// MixerView over an AppState whose StemPlayer is seeded with a
    /// representative mix: unity drums, soloed bass, muted half-gain
    /// vocals, and a zero-gain (−∞ dB) other stem.
    private func makeMixer() -> some View {
        let appState = AppState()
        appState.stemPlayer.seedStemStatesForSnapshot([
            .init(role: "drums", gain: 1.0, isMuted: false, isSoloed: false),
            .init(role: "bass", gain: 0.8, isMuted: false, isSoloed: true),
            .init(role: "vocals", gain: 0.5, isMuted: true, isSoloed: false),
            .init(role: "other", gain: 0.0, isMuted: false, isSoloed: false),
        ])
        return MixerView().environmentObject(appState)
    }
}
