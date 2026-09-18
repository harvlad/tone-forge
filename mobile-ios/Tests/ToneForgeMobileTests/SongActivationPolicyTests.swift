// SongActivationPolicyTests.swift
//
// Pins the per-song reset of the note-synth layer (web 5d1e3bd0 /
// desktop 8e56570c parity): the melody guide never survives a song
// switch. D-036 originally claimed this leg needed no iOS port
// because activate() rebuilds melodyPlayer — but rebuilding the
// PLAYER never reset the TOGGLE, which was precisely the desktop
// bug. D-038 supersedes that paragraph; these tests keep it dead.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class SongActivationPolicyTests: XCTestCase {

    func testMelodyGuideNeverSurvivesASongSwitch() {
        XCTAssertFalse(
            SongActivationPolicy.melodyGuideEnabledAfterSongLoad(wasEnabled: true))
        XCTAssertFalse(
            SongActivationPolicy.melodyGuideEnabledAfterSongLoad(wasEnabled: false))
    }

    /// The WIRING, not just the policy: activate() must consult it.
    /// A stem-less fixture keeps the download leg a no-op, so this
    /// runs headless with no network.
    func testActivateResetsMelodyGuideToggle() async throws {
        let tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("song-activation-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: tmpDir) }
        let app = AppState(sessionStoreRoot: tmpDir)

        app.melodyGuideEnabled = true
        await app.activate(bundle: Self.fixtureBundle)

        XCTAssertFalse(
            app.melodyGuideEnabled,
            "melodyGuideEnabled survived a song switch — the next song's"
                + " melody would play on the synth uninvited (D-038)"
        )
    }

    private static let fixtureBundle = SongBundle(
        bundleVersion: 1,
        analysisId: "activation-policy-fixture",
        meta: BundleMeta(
            title: "Activation Fixture",
            artist: "Fixture Artist",
            sourceUrl: "",
            durationSec: 8.0,
            tempoBpm: 120.0,
            detectedKey: "C major"
        ),
        timeline: BundleTimeline(
            chords: [ChordEvent(start: 0, end: 4, symbol: "C")],
            sections: [SectionEvent(start: 0, end: 8, label: "Verse")],
            beats: stride(from: 0.0, to: 8.0, by: 0.5).map { $0 },
            downbeats: stride(from: 0.0, to: 8.0, by: 2.0).map { $0 }
        ),
        stems: [],
        presets: [:]
    )
}
