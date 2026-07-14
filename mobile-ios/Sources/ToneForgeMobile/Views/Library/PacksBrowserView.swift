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

/// Packs browser segment picker (D-022 Phase 8).
enum PacksBrowserSegment: String, CaseIterable, Identifiable {
    case packs = "Packs"
    case mySamples = "My Samples"

    var id: String { rawValue }
}

struct PacksBrowserView: View {
    @EnvironmentObject private var appState: AppState

    @StateObject private var previewPlayer = PackPreviewPlayer()
    @State private var filter: PackFilter
    /// D-022 Phase 8: segment selection.
    @State private var segment: PacksBrowserSegment = .packs
    /// Downloaded pack opened for pad-by-pad audition (nil = closed).
    @State private var detailPack: SamplePackCatalogEntry?

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
            segmentPicker
            segmentContent
        }
        .task {
            // Auto-refresh on first appear so the Curated section
            // isn't empty. Subsequent opens are cheap: catalog is
            // small (~200 bytes/pack) and cache-friendly.
            await appState.refreshCuratedCatalog()
        }
        .onDisappear { previewPlayer.stop() }
        .sheet(item: $detailPack) { entry in
            PackDetailSheet(
                entry: entry,
                isActive: appState.activeSamplePack?.pack.packId == entry.packId,
                onActivate: {
                    appState.activateCuratedPack(packId: entry.packId)
                    onActivated?()
                    detailPack = nil
                }
            )
            .environmentObject(appState)
        }
    }

    // MARK: - Segment picker

    private var segmentPicker: some View {
        HStack(spacing: 8) {
            ForEach(PacksBrowserSegment.allCases) { seg in
                Button {
                    segment = seg
                } label: {
                    Text(seg.rawValue)
                        .font(TFTheme.chipFont)
                        .tfChip(active: segment == seg)
                }
                .buttonStyle(.plain)
            }
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    // MARK: - Segment content

    @ViewBuilder
    private var segmentContent: some View {
        switch segment {
        case .packs:
            packsContent
        case .mySamples:
            MySamplesContent()
        }
    }

    private var packsContent: some View {
        VStack(spacing: 0) {
            filterChipBar
            List {
                songDnaSection
                bundledSection
                curatedSection
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(TFTheme.background)
        }
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
                    .tfLibraryRowChrome()
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
            .tfLibraryRowChrome()
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
                        .tfLibraryRowChrome()
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
            .tfLibraryCard(active: isActive)
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
        .frame(width: 52, height: 52)
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
            // Active pack: filled check in the accent color.
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(Color.accentColor)
        } else if isCached {
            // Downloaded but not active: outline check (distinct from
            // the download arrow so "already have it" doesn't read as
            // "tap to download").
            Image(systemName: "checkmark.circle")
                .foregroundStyle(.green)
        } else {
            // Not downloaded: the tap-to-download arrow.
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
            // Open the detail sheet to audition pads before activating.
            detailPack = entry
        } else {
            Task {
                await appState.downloadCuratedPack(entry)
                // Once cached, open the detail sheet for preview.
                if appState.cachedPackIds.contains(entry.packId) {
                    detailPack = entry
                }
            }
        }
    }

    // MARK: - Row

    private func packRow(title: String, subtitle: String, isActive: Bool) -> some View {
        HStack(spacing: 12) {
            RoundedRectangle(cornerRadius: 8)
                .fill(TFTheme.faderTint.opacity(0.22))
                .frame(width: 52, height: 52)
                .overlay(
                    Image(systemName: "waveform")
                        .foregroundStyle(TFTheme.faderTint)
                )
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)
                    .lineLimit(1)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            if isActive {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(Color.accentColor)
            }
        }
        .tfLibraryCard(active: isActive)
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

// MARK: - Pack detail / preview sheet

/// Opened after a curated pack is cached. Lists the pack's pads with a
/// per-pad play button (auditions via `previewChopReference(.packPad)`
/// without switching the active pack) plus an Activate button.
private struct PackDetailSheet: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    let entry: SamplePackCatalogEntry
    let isActive: Bool
    let onActivate: () -> Void

    @State private var pads: [SamplePad] = []
    @State private var loadError: String?
    /// padIdx currently auditioning (nil = none).
    @State private var playingPadIdx: Int?

    private let columns = [GridItem(.adaptive(minimum: 140), spacing: 12)]

    var body: some View {
        NavigationStack {
            ScrollView {
                if let loadError {
                    Text(loadError)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .padding()
                } else {
                    LazyVGrid(columns: columns, spacing: 12) {
                        ForEach(pads, id: \.padIdx) { pad in
                            padTile(pad)
                        }
                    }
                    .padding(16)
                }
            }
            .background(TFTheme.background)
            .navigationTitle(entry.name)
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isActive ? "Active" : "Activate", action: onActivate)
                        .disabled(isActive)
                }
            }
        }
        .onAppear(perform: loadPads)
        .onDisappear { appState.modeCoordinator.stopPreviewPad() }
    }

    private func padTile(_ pad: SamplePad) -> some View {
        let isPlaying = playingPadIdx == pad.padIdx
        let tint = TFTheme.familyTint(pad.family)
        return Button {
            togglePreview(pad)
        } label: {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Image(systemName: CategoryCards.icon(for: pad.family))
                        .foregroundStyle(tint)
                    Spacer()
                    Image(systemName: isPlaying ? "stop.circle.fill" : "play.circle.fill")
                        .font(.title3)
                        .foregroundStyle(isPlaying ? Color.accentColor : tint)
                }
                Text(pad.name)
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)
                    .lineLimit(1)
                Text(CategoryCards.title(for: pad.family))
                    .font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 12)
                    .fill(tint.opacity(isPlaying ? 0.28 : 0.14))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(isPlaying ? tint : TFTheme.stroke, lineWidth: 1)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func togglePreview(_ pad: SamplePad) {
        if playingPadIdx == pad.padIdx {
            appState.modeCoordinator.stopPreviewPad()
            playingPadIdx = nil
            return
        }
        appState.previewChopReference(.packPad(packId: entry.packId, padIdx: pad.padIdx))
        playingPadIdx = pad.padIdx
        // Revert the tile to its play icon when the one-shot finishes.
        if let dur = appState.previewPadDurationSec(packId: entry.packId, padIdx: pad.padIdx) {
            let idx = pad.padIdx
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: UInt64(dur * 1_000_000_000))
                if playingPadIdx == idx { playingPadIdx = nil }
            }
        }
    }

    private func loadPads() {
        guard let bank = appState.sampleBank else {
            loadError = "Sample bank unavailable."
            return
        }
        do {
            let resolved = try bank.loadCached(packId: entry.packId)
            pads = resolved.pack.pads.sorted { $0.padIdx < $1.padIdx }
        } catch {
            loadError = "Could not load pack: \(error.localizedDescription)"
        }
    }
}

// MARK: - My Samples segment

/// Inline samples browser for the Packs tab My Samples segment (D-022
/// Phase 8). Mirrors SamplesBrowserView from StorageBrowsers but
/// embedded without a NavigationLink wrapper.
private struct MySamplesContent: View {
    @EnvironmentObject private var appState: AppState
    @State private var showDeleteAllConfirm = false

    private var store: PadSampleStore { appState.padSampleStore }

    var body: some View {
        List {
            if store.samples.isEmpty {
                Text("No samples yet. Capture one with the mic or vocoder on the Contribute tab.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .tfLibraryRowChrome()
            } else {
                Section {
                    ForEach(store.samples, id: \.id) { meta in
                        sampleRow(meta)
                            .tfLibraryRowChrome()
                            .swipeActions(edge: .trailing) {
                                Button("Delete", role: .destructive) {
                                    appState.modeCoordinator.deleteLocalSample(id: meta.id)
                                }
                            }
                    }
                } footer: {
                    Text("\(store.samples.count) sample\(store.samples.count == 1 ? "" : "s") · \(byteString(store.totalBytes()))")
                }

                Button("Delete all samples", role: .destructive) {
                    showDeleteAllConfirm = true
                }
                .frame(maxWidth: .infinity, alignment: .center)
                .tfLibraryRowChrome()
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(TFTheme.background)
        .confirmationDialog(
            "Delete all samples?",
            isPresented: $showDeleteAllConfirm,
            titleVisibility: .visible
        ) {
            Button("Delete all", role: .destructive) {
                store.samples.forEach {
                    appState.modeCoordinator.deleteLocalSample(id: $0.id)
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This deletes all locally recorded samples. Any pads using them will be unassigned.")
        }
    }

    private func sampleRow(_ meta: PadSampleMetadata) -> some View {
        HStack(spacing: 12) {
            RoundedRectangle(cornerRadius: 10)
                .fill(tint(meta.colorHint).opacity(0.22))
                .frame(width: 52, height: 52)
                .overlay(
                    Image(systemName: sourceIcon(meta.source))
                        .font(.title3)
                        .foregroundStyle(tint(meta.colorHint))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .stroke(TFTheme.stroke, lineWidth: 1)
                )
            VStack(alignment: .leading, spacing: 2) {
                Text(classLabel(meta.effectiveClass))
                    .font(.body.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)
                    .lineLimit(1)
                Text(sampleSubtitle(meta))
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
        }
        .tfLibraryCard()
        .contentShape(Rectangle())
    }

    private func sampleSubtitle(_ meta: PadSampleMetadata) -> String {
        [
            sourceLabel(meta.source),
            String(format: "%.1f s", meta.durationSec),
            meta.createdAt.formatted(date: .abbreviated, time: .shortened),
        ].joined(separator: " · ")
    }

    private func sourceIcon(_ source: PadSampleMetadata.Source) -> String {
        switch source {
        case .mic:      return "mic.fill"
        case .vocoded:  return "waveform"
        case .songChop: return "music.note"
        }
    }

    private func sourceLabel(_ source: PadSampleMetadata.Source) -> String {
        switch source {
        case .mic:      return "Mic"
        case .vocoded:  return "Vocoder"
        case .songChop: return "Song chop"
        }
    }

    private func classLabel(_ sampleClass: SampleClass) -> String {
        switch sampleClass {
        case .vocalChop:     return "Vocal chop"
        case .percussion:    return "Percussion"
        case .sustainedNote: return "Sustained note"
        case .texture:       return "Texture"
        case .phrase:        return "Phrase"
        case .speechWord:    return "Speech"
        case .unknown:       return "Sample"
        }
    }

    /// Grid tint hex (0xRRGGBB) → Color.
    private func tint(_ hex: UInt32) -> Color {
        Color(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}

/// Human-readable byte string (KB/MB).
private func byteString(_ bytes: Int64) -> String {
    if bytes < 1024 { return "\(bytes) B" }
    if bytes < 1_048_576 { return String(format: "%.1f KB", Double(bytes) / 1024) }
    return String(format: "%.1f MB", Double(bytes) / 1_048_576)
}
