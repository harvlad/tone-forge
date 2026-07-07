// PackPages.swift
//
// Page model for the multi-pack carousel (Sketch tab + Play tab's
// Samples panel). A "page" is any pack the app can load right now:
//
//   1. Song DNA packs synthesised from the loaded bundle's presets
//      (stems already downloaded),
//   2. bundled packs shipped in app resources (Starter),
//   3. curated packs the user has downloaded to disk.
//
// Not-yet-downloaded curated packs are NOT pages — they stay
// download-then-activate rows in BrowsePacksSheet, and appear as a
// new page the moment their download completes (the page list is
// derived from @Published AppState collections).
//
// `PackPageBuilder.build` is a pure function so page enumeration +
// ordering is unit-testable without AppState or an audio engine.

import Foundation
import ToneForgeEngine

/// One swipeable page in the pack carousel.
public struct PackPage: Identifiable, Equatable, Sendable {
    public enum Source: Equatable, Sendable {
        /// Song-derived pack; `presetKey` locates the SongDnaPack
        /// entry (which owns the ResolvedSamplePack + stem slices).
        case songDna(presetKey: String)
        /// Shipped in app resources (Starter).
        case bundled
        /// Downloaded curated pack on disk.
        case curated
    }

    /// packId — stable page identity, used as the TabView selection
    /// tag and as the scheduler's pack key.
    public let id: String
    public let displayName: String
    public let source: Source

    public init(id: String, displayName: String, source: Source) {
        self.id = id
        self.displayName = displayName
        self.source = source
    }
}

public enum PackPageBuilder {

    /// Enumerate carousel pages in display order:
    ///
    ///   Song DNA (bundle order) → bundled → cached curated
    ///
    /// Cached curated packs follow catalog order when the catalog
    /// knows them (and take the catalog display name); cached packs
    /// absent from the catalog (offline boot, delisted pack) come
    /// last, alphabetically, with the packId as a fallback name.
    public static func build(
        songDnaPacks: [SongDnaPack],
        bundled: [(packId: String, name: String)],
        cachedPackIds: [String],
        catalog: [SamplePackCatalogEntry]
    ) -> [PackPage] {
        var pages: [PackPage] = []
        var seen: Set<String> = []

        for entry in songDnaPacks {
            let id = entry.pack.pack.packId
            guard seen.insert(id).inserted else { continue }
            pages.append(PackPage(
                id: id,
                displayName: entry.displayName,
                source: .songDna(presetKey: entry.presetKey)
            ))
        }

        for (packId, name) in bundled {
            guard seen.insert(packId).inserted else { continue }
            pages.append(PackPage(
                id: packId,
                displayName: name,
                source: .bundled
            ))
        }

        let cached = Set(cachedPackIds)
        // Catalog-known cached packs first, in catalog order.
        for entry in catalog where cached.contains(entry.packId) {
            guard seen.insert(entry.packId).inserted else { continue }
            pages.append(PackPage(
                id: entry.packId,
                displayName: entry.name,
                source: .curated
            ))
        }
        // Cached-but-not-in-catalog stragglers, alphabetical.
        for packId in cachedPackIds.sorted() {
            guard seen.insert(packId).inserted else { continue }
            pages.append(PackPage(
                id: packId,
                displayName: packId,
                source: .curated
            ))
        }

        return pages
    }
}
