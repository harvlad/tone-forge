// LibraryTrackTests.swift
//
// The DRM/download analysability policy on LibraryTrack, exercised
// through a fake MediaLibrarySource (no MediaPlayer involved).

import XCTest
@testable import ToneForgeEngine

private struct FakeLibrarySource: MediaLibrarySource {
    var authorization: MediaLibraryAuthorization = .authorized
    var tracks: [LibraryTrack] = []

    func requestAuthorization() async -> MediaLibraryAuthorization { authorization }
    func allTracks() -> [LibraryTrack] { tracks }
}

final class LibraryTrackTests: XCTestCase {

    private func track(
        id: String, isProtected: Bool, hasAsset: Bool
    ) -> LibraryTrack {
        LibraryTrack(
            id: id,
            title: "Track \(id)",
            artist: "Artist",
            durationSec: 180,
            isProtected: isProtected,
            assetURL: hasAsset ? URL(fileURLWithPath: "/music/\(id).m4a") : nil
        )
    }

    func testOnlyUnprotectedLocalTracksAreAnalysable() async {
        let source = FakeLibrarySource(tracks: [
            track(id: "local", isProtected: false, hasAsset: true),
            track(id: "drm", isProtected: true, hasAsset: true),
            track(id: "cloud", isProtected: false, hasAsset: false),
            track(id: "drm-cloud", isProtected: true, hasAsset: false),
        ])

        let analysable = source.allTracks().filter(\.isAnalysable)

        XCTAssertEqual(analysable.map(\.id), ["local"])
    }

    func testDRMTrackReasonMentionsStreamingNotDownload() {
        let drm = track(id: "drm", isProtected: true, hasAsset: true)
        XCTAssertFalse(drm.isAnalysable)
        XCTAssertEqual(drm.unavailabilityReason, "streaming (DRM) — not analysable")
    }

    func testUndownloadedTrackGetsDistinctReason() {
        // No asset URL but NOT protected: this is the user's own
        // (e.g. purchased) music that just isn't on the device —
        // must not be mislabelled as DRM.
        let cloud = track(id: "cloud", isProtected: false, hasAsset: false)
        XCTAssertFalse(cloud.isAnalysable)
        XCTAssertEqual(cloud.unavailabilityReason, "not downloaded — not analysable")
    }

    func testProtectedWinsOverMissingAssetForReason() {
        let both = track(id: "x", isProtected: true, hasAsset: false)
        XCTAssertEqual(both.unavailabilityReason, "streaming (DRM) — not analysable")
    }

    func testAnalysableTrackHasNoReason() {
        let ok = track(id: "ok", isProtected: false, hasAsset: true)
        XCTAssertTrue(ok.isAnalysable)
        XCTAssertNil(ok.unavailabilityReason)
    }

    func testAuthorizationRoundTrip() async {
        let denied = FakeLibrarySource(authorization: .denied)
        let granted = FakeLibrarySource(authorization: .authorized)

        let a = await denied.requestAuthorization()
        let b = await granted.requestAuthorization()

        XCTAssertEqual(a, .denied)
        XCTAssertEqual(b, .authorized)
    }
}
