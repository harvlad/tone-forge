// AttributionCreditTests.swift
//
// D-024 attribution coverage on the mobile side:
//   - ImportCoordinator.attributionFields captures title/artist from
//     a Music-library source (and skips empties) so they survive the
//     tag-stripping WAV transcode as form fields.
//   - TabScaffold's credit-line helpers implement the render rule:
//     credit only when license is non-empty; verbatim attribution
//     wins over the synthesized "Artist — Title (LICENSE)" form;
//     link prefers sourceUrl over licenseUrl.

import XCTest
import SwiftUI
@testable import ToneForgeMobile
@testable import ToneForgeEngine

final class AttributionCreditTests: XCTestCase {

    // MARK: - ImportCoordinator.attributionFields

    @MainActor
    func testMediaItemSourceYieldsTitleAndArtistFields() async {
        let track = LibraryTrack(
            id: "t1", title: "Night Drive", artist: "Some Artist",
            durationSec: 200, isProtected: false, assetURL: nil
        )
        let coordinator = ImportCoordinator()
        let fields = await coordinator.attributionFields(for: .mediaItem(track))
        XCTAssertEqual(fields.map(\.name), ["title", "artist"])
        XCTAssertEqual(fields.map(\.value), ["Night Drive", "Some Artist"])
    }

    @MainActor
    func testMediaItemSourceSkipsEmptyArtist() async {
        let track = LibraryTrack(
            id: "t2", title: "Untagged", artist: "",
            durationSec: 10, isProtected: false, assetURL: nil
        )
        let coordinator = ImportCoordinator()
        let fields = await coordinator.attributionFields(for: .mediaItem(track))
        XCTAssertEqual(fields.map(\.name), ["title"])
    }

    @MainActor
    func testFileURLWithoutTagsYieldsNoFields() async {
        // A bare WAV has no common metadata — nothing to send.
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("attr-test-\(UUID().uuidString).wav")
        try? Data([0x52, 0x49, 0x46, 0x46]).write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }
        let coordinator = ImportCoordinator()
        let fields = await coordinator.attributionFields(for: .fileURL(url))
        XCTAssertTrue(fields.isEmpty)
    }

    // MARK: - Credit-line render rule

    private typealias Scaffold = TabScaffold<EmptyView, EmptyView>

    private func meta(
        title: String = "Night Drive",
        artist: String = "Some Artist",
        sourceUrl: String = "",
        license: String? = nil,
        licenseUrl: String? = nil,
        attribution: String? = nil
    ) -> BundleMeta {
        BundleMeta(
            title: title, artist: artist, sourceUrl: sourceUrl,
            durationSec: 60, tempoBpm: nil, detectedKey: nil,
            license: license, licenseUrl: licenseUrl,
            attribution: attribution
        )
    }

    func testNoCreditWithoutLicense() {
        XCTAssertNil(Scaffold.creditLine(for: meta()))
        XCTAssertNil(Scaffold.creditLine(for: meta(license: "")))
        XCTAssertNil(Scaffold.creditLine(for: nil))
    }

    func testVerbatimAttributionWins() {
        let m = meta(license: "CC-BY", attribution: "“Night Drive” by Some Artist (CC BY)")
        XCTAssertEqual(
            Scaffold.creditLine(for: m),
            "“Night Drive” by Some Artist (CC BY)"
        )
    }

    func testSynthesizedCreditWhenNoAttribution() {
        XCTAssertEqual(
            Scaffold.creditLine(for: meta(license: "CC0")),
            "Some Artist — Night Drive (CC0)"
        )
        XCTAssertEqual(
            Scaffold.creditLine(for: meta(artist: "", license: "CC0")),
            "Night Drive (CC0)"
        )
    }

    func testCreditURLPrefersSourceOverLicense() {
        let m = meta(
            sourceUrl: "https://example.org/night-drive",
            license: "CC-BY",
            licenseUrl: "https://creativecommons.org/licenses/by/4.0/"
        )
        XCTAssertEqual(
            Scaffold.creditURL(for: m)?.absoluteString,
            "https://example.org/night-drive"
        )
        let fallback = meta(
            license: "CC-BY",
            licenseUrl: "https://creativecommons.org/licenses/by/4.0/"
        )
        XCTAssertEqual(
            Scaffold.creditURL(for: fallback)?.absoluteString,
            "https://creativecommons.org/licenses/by/4.0/"
        )
    }

    func testCreditURLNilWithoutLicenseOrURLs() {
        XCTAssertNil(Scaffold.creditURL(for: meta(sourceUrl: "https://example.org/x")))
        XCTAssertNil(Scaffold.creditURL(for: meta(license: "CC0")))
    }
}
