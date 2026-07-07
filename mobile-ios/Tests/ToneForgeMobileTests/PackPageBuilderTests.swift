// PackPageBuilderTests.swift
//
// Enumeration + ordering rules for the pack-carousel page list.
// `PackPageBuilder.build` is a pure function, so these tests need no
// AppState, no audio engine, and no disk I/O.
//
// Coverage:
//   - Display order: Song DNA (input order) → bundled → cached
//     curated (catalog order) → cached-but-not-in-catalog (alpha).
//   - Catalog supplies curated display names; stragglers fall back
//     to the packId.
//   - Catalog entries that aren't downloaded are NOT pages.
//   - Duplicate packIds keep the first (highest-priority) source.

import XCTest
@testable import ToneForgeMobile
import ToneForgeEngine

final class PackPageBuilderTests: XCTestCase {

    // MARK: - Fixtures

    private func songDna(_ presetKey: String, packId: String, name: String) -> SongDnaPack {
        let pack = SamplePack(packId: packId, name: name, family: .vocals, pads: [])
        return SongDnaPack(
            presetKey: presetKey,
            stem: "vocals",
            sliceMode: "chord",
            displayName: name,
            chopCount: 0,
            pack: ResolvedSamplePack(pack: pack, padFileURLs: [:])
        )
    }

    private func catalogEntry(_ packId: String, name: String) -> SamplePackCatalogEntry {
        SamplePackCatalogEntry(packId: packId, name: name, family: .pads, padCount: 16)
    }

    // MARK: - Ordering

    func testFullOrderingSongDnaThenBundledThenCurated() {
        let pages = PackPageBuilder.build(
            songDnaPacks: [
                songDna("vocals:chord", packId: "song-derived:a1:vocals-chord", name: "Vocals — chord"),
                songDna("drums:beat", packId: "song-derived:a1:drums-beat", name: "Drums — beat"),
            ],
            bundled: [(packId: "starter", name: "Starter")],
            cachedPackIds: ["zz-offline", "shoegaze"],
            catalog: [
                catalogEntry("shoegaze", name: "Shoegaze Textures"),
                catalogEntry("lofi", name: "Lo-Fi Dust"),  // not downloaded
            ]
        )

        XCTAssertEqual(pages.map(\.id), [
            "song-derived:a1:vocals-chord",
            "song-derived:a1:drums-beat",
            "starter",
            "shoegaze",
            "zz-offline",
        ])
        XCTAssertEqual(pages.map(\.displayName), [
            "Vocals — chord",
            "Drums — beat",
            "Starter",
            "Shoegaze Textures",
            "zz-offline",   // straggler falls back to packId
        ])
    }

    func testSourcesAreTaggedCorrectly() {
        let pages = PackPageBuilder.build(
            songDnaPacks: [songDna("vocals:chord", packId: "sd1", name: "V")],
            bundled: [(packId: "starter", name: "Starter")],
            cachedPackIds: ["shoegaze"],
            catalog: [catalogEntry("shoegaze", name: "Shoegaze")]
        )
        XCTAssertEqual(pages.count, 3)
        XCTAssertEqual(pages[0].source, .songDna(presetKey: "vocals:chord"))
        XCTAssertEqual(pages[1].source, .bundled)
        XCTAssertEqual(pages[2].source, .curated)
    }

    // MARK: - Exclusion + fallback

    func testNotDownloadedCatalogEntriesAreNotPages() {
        let pages = PackPageBuilder.build(
            songDnaPacks: [],
            bundled: [],
            cachedPackIds: [],
            catalog: [catalogEntry("lofi", name: "Lo-Fi Dust")]
        )
        XCTAssertTrue(pages.isEmpty)
    }

    func testCachedButNotInCatalogComesLastAlphabetical() {
        let pages = PackPageBuilder.build(
            songDnaPacks: [],
            bundled: [],
            cachedPackIds: ["delta", "alpha", "shoegaze"],
            catalog: [catalogEntry("shoegaze", name: "Shoegaze")]
        )
        XCTAssertEqual(pages.map(\.id), ["shoegaze", "alpha", "delta"])
        XCTAssertEqual(pages[1].displayName, "alpha")
        XCTAssertEqual(pages[2].displayName, "delta")
    }

    // MARK: - Dedupe

    func testDuplicatePackIdsKeepFirstSource() {
        // Same packId appearing as both song-DNA and cached curated
        // (pathological, but the builder must not emit two pages with
        // the same TabView tag — SwiftUI selection would break).
        let pages = PackPageBuilder.build(
            songDnaPacks: [songDna("vocals:chord", packId: "dup", name: "DNA Dup")],
            bundled: [(packId: "dup", name: "Bundled Dup")],
            cachedPackIds: ["dup"],
            catalog: [catalogEntry("dup", name: "Curated Dup")]
        )
        XCTAssertEqual(pages.count, 1)
        XCTAssertEqual(pages[0].displayName, "DNA Dup")
        XCTAssertEqual(pages[0].source, .songDna(presetKey: "vocals:chord"))
    }

    func testEmptyEverythingProducesNoPages() {
        let pages = PackPageBuilder.build(
            songDnaPacks: [], bundled: [], cachedPackIds: [], catalog: []
        )
        XCTAssertTrue(pages.isEmpty)
    }
}
