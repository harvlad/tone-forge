// MixerSnapshotTests.swift
//
// Golden-PNG snapshots of the Mixer sheet (mockup-driven rows
// restyle) — vertical channel rows with tinted role icons, S/M
// buttons, horizontal sliders, and dB readouts, plus the Your Layer
// and Master rows.
//
// Renders MixerBody directly (renderForSnapshot: true), NOT
// MixerView: ImageRenderer can't flatten NavigationStack (UIKit-
// backed — full-frame "prohibited" placeholder) and leaves ScrollView
// content blank, so the fixture bypasses both. (The Sliders inside
// the rows are UISlider-backed and still render as small placeholders
// — the card layout, icons, buttons, and readouts are what these
// goldens pin.)
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

    /// MixerBody (see header — no NavigationStack) over an AppState
    /// whose StemPlayer is seeded with a representative mix: unity
    /// drums, soloed bass, muted half-gain vocals, and a zero-gain
    /// (−∞ dB) other stem.
    private func makeMixer() -> some View {
        let appState = AppState()
        appState.stemPlayer.seedStemStatesForSnapshot([
            .init(role: "drums", gain: 1.0, isMuted: false, isSoloed: false),
            .init(role: "bass", gain: 0.8, isMuted: false, isSoloed: true),
            .init(role: "vocals", gain: 0.5, isMuted: true, isSoloed: false),
            .init(role: "other", gain: 0.0, isMuted: false, isSoloed: false),
        ])
        return MixerBody(
            stemPlayer: appState.stemPlayer,
            sampleSettings: appState.sampleSettings,
            fxSettingsStore: appState.fxSettingsStore,
            renderForSnapshot: true,
            initialSegment: .levels
        )
        .frame(maxHeight: .infinity, alignment: .top)
        .background(TFTheme.background)
        .environmentObject(appState)
    }
}
