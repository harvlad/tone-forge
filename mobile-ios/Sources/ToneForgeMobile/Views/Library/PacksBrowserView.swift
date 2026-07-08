// PacksBrowserView.swift
//
// The pack browser body — filter chip bar + Song DNA / Bundled /
// Curated sections. Extracted from BrowsePacksSheet (D-022) so the
// same browsing UI serves two hosts:
//
//   - BrowsePacksSheet: modal reached from the Contribute tab's
//     PackPicker / category cards (dismisses on activation);
//   - Library tab, Packs segment: browsing in place (no dismissal).
//
// Sections are ordered by proximity to the current musical moment:
//
//   1. Song DNA  — virtual packs synthesised from `SongBundle.presets`
//                  (populated by `AppState.songDnaPacks`). Empty when
//                  no bundle is loaded. Tapping activates via
//                  `AppState.activateSongDnaPack`.
//   2. Bundled   — offline-first packs shipped with the app. Loaded
//                  via `SampleBank.loadBundled`. Currently just the
//                  StarterPack.
//   3. Curated   — remote catalog served by `/api/sample-packs`.
//                  Each row downloads on tap (streaming progress via
//                  `AppState.curatedDownloads`) and activates once
//                  fully cached. Cached packs re-activate instantly.
//
// Filter chips (All / For You / family / genres / moods) narrow the
// Curated section; facets derive from the catalog itself. Cover art
// via AsyncImage with a family-tint fallback; preview playback via a
// single AVPlayer (PackPreviewPlayer). `initialFamily` seeds the
// filter when opened from a CategoryCards card.
//
// The active pack is marked with a check. `onActivated` fires after
// any pack activates so a sheet host can dismiss itself.

import SwiftUI
import ToneForgeEngine

struct PacksBrowserView: View {
    @EnvironmentObject private var appState: AppState

    @StateObject private var previewPlayer = PackPreviewPlayer()
    @State private var filter: PackFilter

    private let initialFamily: SampleFamily?
    /// Fired after a pack activates (sheet hosts dismiss; the Library
    /// segment just stays put).
    private let onActivated: (() -> Void)?

    init(initialFamily: SampleFamily? = nil,
         onActivated: (() -> Void)? = nil) {
        self.initialFamily = initialFamily
        self.onActivated = onActivated
        _filter = State(initialValue:
            initialFamily.map { .family($0) } ?? .all)
    }

    var body: some View {
        VStack(spacing: 0) {
            filterChipBar
            List {
                songDnaSection
                bundledSection
                curatedSection
            }
            #if os(iOS)
            .listStyle(.insetGrouped)
            #endif
        }
        .task {
            // Auto-refresh on first appear so the Curated section
            // isn't empty. Subsequent opens are cheap: catalog is
            // small (~200 bytes/pack) and cache-friendly.
            await appState.refreshCuratedCatalog()
        }
        .onDisappear { previewPlayer.stop() }
    }

    // MARK: - Filter model

    /// Curated-section filter. Facets are derived from the catalog
    /// itself, so new genres/moods in catalog.json grow chips with no
    /// client change.
    enum PackFilter: Hashable {
        case all
        /// Song-DNA family heuristic: packs whose family matches a
        /// family present in the current song's DNA packs.
        case forYou
        case family(SampleFamily)
        case genre(String)
        case mood(String)
    }

    private var availableFilters: [PackFilter] {
        var filters: [PackFilter] = [.all]
        if !appState.songDnaPacks.isEmpty { filters.append(.forYou) }
        if let family = initialFamily { filters.append(.family(family)) }
        let catalog = appState.curatedCatalog
        let genres = Set(catalog.flatMap(\.genres)).sorted()
        let moods = Set(catalog.flatMap(\.moods)).sorted()
        filters.append(contentsOf: genres.map(PackFilter.genre))
        filters.append(contentsOf: moods.map(PackFilter.mood))
        return filters
    }

    private func label(for filter: PackFilter) -> String {
        switch filter {
        case .all: return "All"
        case .forYou: return "For You"
        case .family(let f): return CategoryCards.title(for: f)
        case .genre(let g): return g.capitalized
        case .mood(let m): return m.capitalized
        }
    }

    private func matches(_ entry: SamplePackCatalogEntry) -> Bool {
        switch filter {
        case .all:
            return true
        case .forYou:
            let dnaFamilies = Set(
                appState.songDnaPacks.map { $0.pack.pack.family }
            )
            // No song DNA yet → nothing to personalise on; show all.
            return dnaFamilies.isEmpty || dnaFamilies.contains(entry.family)
        case .family(let family):
            return entry.family == family
        case .genre(let genre):
            return entry.genres.contains(genre)
        case .mood(let mood):
            return entry.moods.contains(mood)
        }
    }

    private var filterChipBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(availableFilters, id: \.self) { f in
                    Button {
                        filter = f
                    } label: {
                        Text(label(for: f))
                            .font(TFTheme.chipFont)
                            .tfChip(active: filter == f)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
        }
    }

    // MARK: - Song DNA

    @ViewBuilder
    private var songDnaSection: some View {
        Section {
            if appState.songDnaPacks.isEmpty {
                Text(appState.currentBundle == nil
                     ? "Load a song from Library to see Song DNA packs."
                     : "Downloading stems… Song DNA appears when the download finishes.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(appState.songDnaPacks) { entry in
                    Button {
                        appState.activateSongDnaPack(entry)
                        onActivated?()
                    } label: {
                        packRow(
                            title: entry.displayName,
                            subtitle: "\(entry.chopCount) chop\(entry.chopCount == 1 ? "" : "s")",
                            isActive: appState.activeSamplePack?.pack.packId == entry.pack.pack.packId
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
        } header: {
            Text("Song DNA")
        } footer: {
            if !appState.songDnaPacks.isEmpty {
                Text("Chops sliced from this song's stems. Each pad plays a moment from the original.")
            }
        }
    }

    // MARK: - Bundled

    @ViewBuilder
    private var bundledSection: some View {
        Section {
            Button {
                activateBundled(packId: "starter", displayName: "Starter Pack")
                onActivated?()
            } label: {
                packRow(
                    title: "Starter Pack",
                    subtitle: "16 hand-picked samples · offline",
                    isActive: appState.activeSamplePack?.pack.packId == "starter"
                )
            }
            .buttonStyle(.plain)
        } header: {
            Text("Bundled")
        }
    }

    // MARK: - Curated

    @ViewBuilder
    private var curatedSection: some View {
        let filtered = appState.curatedCatalog.filter(matches)
        Section {
            if let error = appState.curatedError {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(error)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        Button("Retry") {
                            Task { await appState.refreshCuratedCatalog() }
                        }
                        .font(.footnote)
                    }
                }
            } else if appState.curatedCatalog.isEmpty {
                Text("Loading catalog…")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else if filtered.isEmpty {
                Text("No packs match this filter.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(filtered) { entry in
                    curatedRow(entry: entry)
                }
            }
        } header: {
            Text("Curated")
        } footer: {
            if !appState.curatedCatalog.isEmpty {
                Text("Downloaded packs stay available offline.")
            }
        }
    }

    @ViewBuilder
    private func curatedRow(entry: SamplePackCatalogEntry) -> some View {
        let progress = appState.curatedDownloads[entry.packId]
        let isCached = appState.cachedPackIds.contains(entry.packId)
        let isActive = appState.activeSamplePack?.pack.packId == entry.packId
        Button {
            handleCuratedTap(entry: entry, isCached: isCached)
        } label: {
            HStack(spacing: 12) {
                coverThumb(entry: entry)
                VStack(alignment: .leading, spacing: 2) {
                    Text(entry.name)
                        .font(.body)
                        .foregroundStyle(.primary)
                    if let tagline = entry.description, !tagline.isEmpty {
                        Text(tagline)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                    Text(curatedSubtitle(entry: entry, progress: progress, isCached: isCached))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    if let p = progress, !p.isComplete, p.padsTotal > 0 {
                        ProgressView(
                            value: Double(p.padsCompleted),
                            total: Double(p.padsTotal)
                        )
                        .progressViewStyle(.linear)
                        .padding(.top, 2)
                    }
                }
                Spacer()
                if let previewURL = resolvedURL(entry.previewUrl) {
                    previewButton(packId: entry.packId, url: previewURL)
                }
                curatedAccessory(progress: progress, isCached: isCached, isActive: isActive)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(progress.map { !$0.isComplete } ?? false)
    }

    /// Cover art thumbnail — AsyncImage over the catalog coverUrl with
    /// a family-tinted placeholder for packs without art (or while
    /// loading / on failure).
    @ViewBuilder
    private func coverThumb(entry: SamplePackCatalogEntry) -> some View {
        let fallback = RoundedRectangle(cornerRadius: 8)
            .fill(TFTheme.familyTint(entry.family).opacity(0.25))
            .overlay(
                Image(systemName: CategoryCards.icon(for: entry.family))
                    .font(.body)
                    .foregroundStyle(TFTheme.familyTint(entry.family))
            )
        Group {
            if let url = resolvedURL(entry.coverUrl) {
                AsyncImage(url: url) { phase in
                    if let image = phase.image {
                        image.resizable().scaledToFill()
                    } else {
                        fallback
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 8))
            } else {
                fallback
            }
        }
        .frame(width: 44, height: 44)
    }

    private func previewButton(packId: String, url: URL) -> some View {
        let isPlaying = previewPlayer.playingPackId == packId
        return Button {
            previewPlayer.toggle(packId: packId, url: url)
        } label: {
            Image(systemName: isPlaying
                ? "stop.circle.fill" : "play.circle")
                .font(.title3)
                .foregroundStyle(isPlaying ? Color.accentColor : .secondary)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(isPlaying ? "Stop preview" : "Play preview")
    }

    /// Catalog URLs may be relative ("/api/sample-packs/x/cover") or
    /// absolute; resolve against the configured backend.
    private func resolvedURL(_ raw: String?) -> URL? {
        guard let raw, !raw.isEmpty else { return nil }
        return URL(string: raw, relativeTo: appState.backendBaseURL)
    }

    @ViewBuilder
    private func curatedAccessory(
        progress: PackDownloadProgress?,
        isCached: Bool,
        isActive: Bool
    ) -> some View {
        if let p = progress, !p.isComplete {
            ProgressView()
                .controlSize(.small)
        } else if isActive {
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(Color.accentColor)
        } else if isCached {
            Image(systemName: "arrow.down.circle.fill")
                .foregroundStyle(.green)
        } else {
            Image(systemName: "arrow.down.circle")
                .foregroundStyle(.secondary)
        }
    }

    private func curatedSubtitle(
        entry: SamplePackCatalogEntry,
        progress: PackDownloadProgress?,
        isCached: Bool
    ) -> String {
        if let p = progress, !p.isComplete {
            return "Downloading… \(p.padsCompleted)/\(p.padsTotal) pads"
        }
        var parts: [String] = ["\(entry.padCount) pad\(entry.padCount == 1 ? "" : "s")"]
        if !entry.genres.isEmpty {
            parts.append(entry.genres.prefix(2).joined(separator: " · "))
        } else if !entry.tags.isEmpty {
            parts.append(entry.tags.prefix(3).joined(separator: " · "))
        }
        if isCached { parts.append("offline") }
        return parts.joined(separator: " · ")
    }

    private func handleCuratedTap(entry: SamplePackCatalogEntry, isCached: Bool) {
        previewPlayer.stop()
        if isCached {
            appState.activateCuratedPack(packId: entry.packId)
            onActivated?()
        } else {
            Task {
                await appState.downloadCuratedPack(entry)
                // Once the download completes, activate immediately.
                if appState.cachedPackIds.contains(entry.packId) {
                    appState.activateCuratedPack(packId: entry.packId)
                    onActivated?()
                }
            }
        }
    }

    // MARK: - Row

    private func packRow(title: String, subtitle: String, isActive: Bool) -> some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.body)
                    .foregroundStyle(.primary)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if isActive {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(Color.accentColor)
            }
        }
        .contentShape(Rectangle())
    }

    // MARK: - Helpers

    private func activateBundled(packId: String, displayName: String) {
        guard let bank = appState.sampleBank else { return }
        do {
            let pack = try bank.loadBundled(packId: packId)
            appState.activateSamplePack(pack, stemFiles: [:])
        } catch {
            // Silently ignore — Samples panel will keep whatever was
            // active. Explicit error surfacing lives on AppState for
            // song-load failures; a missing bundled pack is a build
            // problem, not a user problem.
        }
    }
}
