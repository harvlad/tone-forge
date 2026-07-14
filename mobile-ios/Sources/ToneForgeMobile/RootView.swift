// RootView.swift
//
// Top-level TabView — the D-022 five-tab shell ("Every mode. No
// scrolling."). Each former Play-tab surface is a first-class tab:
//
//   Learn      — guided practice for the loaded song
//   Jam        — jam-in-key pads (Chord Pads folds in as a toggle)
//   Contribute — the 8×8 sample/hybrid grid (sketch when no song)
//   Mixer      — per-stem levels (FX segment lands in Phase 8)
//   Library    — recent analyses + downloads + search
//
// Selection lives on AppState.selectedTab (persisted via
// SampleSettingsStore.appTabRaw); the tab→engine-mode policy runs in
// AppState.applySelectedTab, never here. Loading a song from Library
// deep-links back to the last performance tab.

import SwiftUI
import ToneForgeEngine
#if canImport(UIKit)
import UIKit
#endif

public struct RootView: View {
    @EnvironmentObject private var appState: AppState

    public init() {}

    public var body: some View {
        TabView(selection: $appState.selectedTab) {
            LearnTabView()
                .tabItem {
                    Label(AppTab.learn.title,
                          systemImage: AppTab.learn.systemImage)
                }
                .tag(AppTab.learn)

            JamTabView()
                .tabItem {
                    Label(AppTab.jam.title,
                          systemImage: AppTab.jam.systemImage)
                }
                .tag(AppTab.jam)

            ContributeTabView()
                .tabItem {
                    Label(AppTab.contribute.title,
                          systemImage: AppTab.contribute.systemImage)
                }
                .tag(AppTab.contribute)

            MixerTabView()
                .tabItem {
                    Label(AppTab.mixer.title,
                          systemImage: AppTab.mixer.systemImage)
                }
                .tag(AppTab.mixer)

            // LibraryView hosts its own NavigationStack (searchable +
            // toolbar), so it skips TabScaffold.
            LibraryView(onActivate: { appState.openSong() })
                .tabItem {
                    Label(AppTab.library.title,
                          systemImage: AppTab.library.systemImage)
                }
                .tag(AppTab.library)
        }
        // P7: Launchpad underpower warning floats over every tab —
        // the performer needs to see it wherever they are.
        .overlay(alignment: .top) {
            if appState.underpowerBannerVisible {
                BannerView(
                    icon: "bolt.trianglebadge.exclamationmark",
                    title: "Launchpad power issue suspected",
                    message: "The Launchpad keeps dropping its "
                        + "connection. iPhones can't power it alone — "
                        + "use a POWERED USB hub or the Launchpad's "
                        + "own supply.",
                    onDismiss: { appState.dismissUnderpowerBanner() }
                )
            }
        }
        .animation(.easeInOut(duration: 0.2),
                   value: appState.underpowerBannerVisible)
        // Design mockups are dark-only; forcing the scheme keeps the
        // grid canvas, cards and system chrome coherent regardless of
        // the device setting.
        .preferredColorScheme(.dark)
    }
}

// MARK: - Library

/// The Library tab (D-022): segmented Songs | Packs | Recordings.
///
/// Songs is the recent-analyses list backed by GET /api/history —
/// tapping a row downloads the bundle + stems and deep-links back to
/// the last performance tab. When the backend is unreachable (off the
/// home LAN) a Downloaded section lists the locally persisted bundles
/// instead, activated straight from the on-device cache (D-021).
/// Packs re-hosts PacksBrowserView (shared with BrowsePacksSheet);
/// Recordings lists saved layers + sketches (ex-ProfileView).
struct LibraryView: View {
    @EnvironmentObject private var appState: AppState

    @State private var segment: LibrarySegment = .songs
    @State private var query: String = ""
    @State private var entries: [HistoryEntry] = []
    /// Locally cached bundles. Populated eagerly at the top of
    /// reload() so downloaded songs are tappable even while the
    /// history fetch is still in flight; after a successful fetch the
    /// list is trimmed to bundles the server no longer lists (online,
    /// the rest already appear in Recent).
    @State private var localBundles: [SongBundle] = []
    @State private var isLoading: Bool = false
    @State private var fetchError: String?
    /// Live-search debounce token — bumped on every keystroke; only
    /// the latest task actually issues the network call (inherited
    /// from the removed Search tab).
    @State private var searchToken: UUID = UUID()

    // Import flow (Music library / Files → attestation → analyze).
    // The analyze transport is stubbed under `-uitest-stub-import`.
    @StateObject private var importer = ImportCoordinator(
        jobClient: UITestSupport.makeJobClient()
    )
    @State private var showMusicPicker = false
    @State private var showFilePicker = false
    @State private var showCCTracks = false
    @State private var showSettings = false

    /// Called once a bundle activates so the parent can flip to a
    /// performance tab.
    let onActivate: () -> Void

    private var client: HistoryClient { HistoryClient(timeout: AppConfig.historyTimeout) }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                libraryHeader

                Picker("Library section", selection: $segment) {
                    ForEach(LibrarySegment.allCases) { seg in
                        Text(seg.title).tag(seg)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 16)
                .padding(.vertical, 8)

                switch segment {
                case .songs:
                    songsList
                case .packs:
                    PacksBrowserView()
                case .recordings:
                    RecordingsListView()
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar(.hidden, for: .navigationBar)
            .sheet(isPresented: $showSettings) { SettingsView() }
            .task {
                if entries.isEmpty { await reload() }
            }
            // Cross-device pickup: signing in (or out) changes what
            // the backend returns for this bearer, so refresh.
            // onReceive (not onChange): accountStore is a nested
            // ObservableObject, so its @Published writes don't
            // re-render this view on their own.
            .onReceive(
                appState.accountStore.$profile
                    .dropFirst()
                    .removeDuplicates()
            ) { _ in
                Task { await reload() }
            }
            .onAppear {
                importer.onLoaded = {
                    importer.dismiss()
                    onActivate()
                }
            }
            .sheet(isPresented: $showMusicPicker) {
                musicPickerSheet
            }
            #if os(iOS)
            .sheet(isPresented: $showFilePicker) {
                DocumentPickerView { url in
                    importer.start(source: .fileURL(url), appState: appState)
                }
            }
            #endif
            // Curated CC demo tracks (D-024): server-side import, so
            // the sheet hands back a running job id — no attestation,
            // no transcode, straight into the progress sheet.
            .sheet(isPresented: $showCCTracks) {
                CCTracksSheet { jobId, track in
                    importer.startCuratedJob(
                        jobId: jobId, title: track.title, appState: appState
                    )
                }
            }
            .sheet(
                isPresented: Binding(
                    get: { importer.phase == .awaitingAttestation },
                    set: { if !$0 { importer.attestationCancelled() } }
                )
            ) {
                AttestationSheet(
                    store: importer.attestation,
                    onAccept: { importer.attestationAccepted() },
                    onCancel: { importer.attestationCancelled() }
                )
            }
            .sheet(
                isPresented: Binding(
                    get: { importer.isImporting },
                    set: { if !$0 { importer.dismiss() } }
                )
            ) {
                AnalyzingView(importer: importer)
            }
        }
    }

    // MARK: - Songs segment

    private var songsList: some View {
            List {
                uitestStubImportSection

                if !entries.isEmpty {
                    ForEach(entries) { entry in
                        entryRow(entry)
                            .tfLibraryRowChrome()
                            .swipeActions(edge: .trailing) {
                                Button(role: .destructive) {
                                    Task { await delete(entry) }
                                } label: {
                                    Label("Delete", systemImage: "trash")
                                }
                            }
                    }
                } else if isLoading {
                    HStack {
                        ProgressView()
                        Text("Loading history…").foregroundStyle(.secondary)
                    }
                    .tfLibraryRowChrome()
                } else if localBundles.isEmpty {
                    Text("No history yet — analyze a song from the backend and it will show up here.")
                        .foregroundStyle(.secondary)
                        .font(.callout)
                        .tfLibraryRowChrome()
                }

                if !localBundles.isEmpty {
                    ForEach(localBundles, id: \.analysisId) { bundle in
                        localBundleRow(bundle)
                            .tfLibraryRowChrome()
                    }
                }

                if let err = fetchError {
                    Text(err).foregroundStyle(.red).tfLibraryRowChrome()
                }

                if let err = appState.loadingError {
                    Text(err).foregroundStyle(.red).tfLibraryRowChrome()
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(TFTheme.background)
            // Live search as you type, 300 ms debounced so we don't
            // spam the server on every keystroke. Clearing the query
            // (or cancelling search) restores the full Recent list.
            .onChange(of: query) { _, _ in
                let token = UUID()
                searchToken = token
                Task {
                    try? await Task.sleep(nanoseconds: 300_000_000)
                    guard searchToken == token else { return }
                    await reload()
                }
            }
            .refreshable { await reload() }
    }

    // MARK: - Import entry points

    /// Toolbar "+" beside the Library title. Same import menu that
    /// used to live in the bottom card; keeps the `import-menu`
    /// identifier for UI tests.
    private var addSongButton: some View {
        Menu {
            Button {
                showMusicPicker = true
            } label: {
                Label("From Music Library", systemImage: "music.note")
            }
            Button {
                showFilePicker = true
            } label: {
                Label("From Files", systemImage: "folder")
            }
            Button {
                showCCTracks = true
            } label: {
                Label("Demo Tracks", systemImage: "sparkles")
            }
        } label: {
            Image(systemName: "plus")
                .font(.body.weight(.semibold))
                .foregroundStyle(TFTheme.textPrimary)
        }
        .accessibilityIdentifier("import-menu")
    }

    // MARK: - Custom header

    /// Own title row: large "Library" with the settings gear + add
    /// menu on the same baseline (native large-title toolbar can't sit
    /// the buttons on the title row). Search lives here too so the
    /// hidden nav bar doesn't cost the `.searchable` field.
    private var libraryHeader: some View {
        VStack(spacing: 10) {
            HStack {
                Text("Library")
                    .font(.largeTitle.bold())
                    .foregroundStyle(TFTheme.textPrimary)
                Spacer()
                HStack(spacing: 18) {
                    settingsButton
                    addSongButton
                }
            }
            if segment == .songs {
                searchField
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
    }

    private var searchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(TFTheme.textSecondary)
            TextField("Search songs", text: $query)
                .textFieldStyle(.plain)
                .foregroundStyle(TFTheme.textPrimary)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .onSubmit { Task { await reload() } }
            if !query.isEmpty {
                Button {
                    query = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(TFTheme.textSecondary)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(TFTheme.surface, in: Capsule())
        .overlay(Capsule().stroke(TFTheme.stroke, lineWidth: 1))
    }

    /// Settings gear (D-022 update: moved here from the per-tab Now
    /// Playing header).
    private var settingsButton: some View {
        Button {
            showSettings = true
        } label: {
            Image(systemName: "gearshape")
                .font(.body)
                .foregroundStyle(TFTheme.textPrimary)
        }
        .accessibilityLabel("Settings")
        .accessibilityIdentifier("library-settings-button")
    }

    @ViewBuilder
    private var musicPickerSheet: some View {
        #if os(iOS)
        let source = MPMediaLibrarySource()
        MusicLibraryPickerView(source: source) { track in
            // Capture album art now — the ArtworkStore key
            // (historyId) only exists once analysis completes, so the
            // coordinator holds the JPEG until then.
            importer.pendingArtworkData = source
                .artwork(forTrackId: track.id, size: CGSize(width: 600, height: 600))?
                .jpegData(compressionQuality: 0.85)
            importer.start(source: .mediaItem(track), appState: appState)
        }
        #else
        EmptyView()
        #endif
    }

    // MARK: - UI-test stub import

    /// Only visible when launched with `-uitest-stub-import`: drives
    /// the real ImportCoordinator (attestation gate included) with a
    /// baked WAV + stubbed analyze transport. See UITestSupport.
    @ViewBuilder
    private var uitestStubImportSection: some View {
        if UITestSupport.stubImportEnabled {
            Section {
                Button("UITest Import") {
                    guard let url = try? UITestSupport.writeStubWAV() else { return }
                    importer.start(source: .fileURL(url), appState: appState)
                }
                .accessibilityIdentifier("uitest-import-row")
            }
        }
    }

    // MARK: - Sections / rows

    /// Vertical three-dot menu on the trailing edge of a library card.
    @ViewBuilder
    private func rowMenu<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        Menu {
            content()
        } label: {
            Image(systemName: "ellipsis")
                .rotationEffect(.degrees(90))
                .font(.body)
                .foregroundStyle(TFTheme.textSecondary)
                .frame(width: 30, height: 30)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    /// Single-line subtitle for a history row. No artist field on
    /// HistoryEntry, so fall back to the detected type / amp family.
    private func entrySubtitle(_ entry: HistoryEntry) -> String? {
        if let type = entry.detectedType, !type.isEmpty {
            return type.capitalized
        }
        if let amp = entry.ampFamily, !amp.isEmpty { return amp }
        if let summary = entry.summary, !summary.isEmpty { return summary }
        return nil
    }

    @ViewBuilder
    private func entryRow(_ entry: HistoryEntry) -> some View {
        let isActive = appState.currentBundle?.analysisId == entry.id
        // A cold first load runs JSON fetch + a stem download that emits
        // no incremental bytes, so `downloadFraction` is nil until the
        // first stem lands. `loadingBundleId` flips the instant the row
        // is tapped, driving an indeterminate spinner over that gap.
        let isLoadingRow = appState.loadingBundleId == entry.id
        // While this song's stems download, show a single aggregate
        // progress on its own row instead of a stack of per-stem rows.
        let loadingFraction: Double? = isLoadingRow ? appState.downloadFraction : nil
        Button {
            Task {
                // onReady fires the instant the song is activated —
                // before the stem download — so Learn opens right away
                // instead of minutes later.
                await appState.loadBundle(analysisId: entry.id,
                                          onReady: onActivate)
            }
        } label: {
            HStack(spacing: 12) {
                ArtworkView(analysisId: entry.id,
                            title: entry.name ?? "Untitled",
                            size: 52)
                VStack(alignment: .leading, spacing: 2) {
                    Text(entry.name ?? "Untitled")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(TFTheme.textPrimary)
                        .lineLimit(1)
                    if let frac = loadingFraction {
                        rowProgressBar(frac)
                    } else if isLoadingRow {
                        Text("Downloading…")
                            .font(.caption)
                            .foregroundStyle(TFTheme.textSecondary)
                            .lineLimit(1)
                    } else if let sub = entrySubtitle(entry) {
                        Text(sub)
                            .font(.caption)
                            .foregroundStyle(TFTheme.textSecondary)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: 8)
                if let frac = loadingFraction {
                    Text("\(Int(frac * 100))%")
                        .font(.caption)
                        .foregroundStyle(TFTheme.textSecondary)
                        .monospacedDigit()
                } else if isLoadingRow {
                    ProgressView()
                        .controlSize(.small)
                } else if let dur = entry.duration {
                    Text(formatDuration(dur))
                        .font(.caption)
                        .foregroundStyle(TFTheme.textSecondary)
                        .monospacedDigit()
                }
                rowMenu {
                    Button(role: .destructive) {
                        Task { await delete(entry) }
                    } label: {
                        Label("Delete", systemImage: "trash")
                    }
                }
            }
            .tfLibraryCard(active: isActive)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(appState.isDownloading)
    }

    /// Slim determinate bar shown inline on the song row that is
    /// currently downloading its stems.
    private func rowProgressBar(_ fraction: Double) -> some View {
        ProgressView(value: fraction)
            .progressViewStyle(.linear)
            .tint(Color.accentColor)
            .frame(height: 3)
    }

    /// Row for a locally persisted bundle (offline fallback). Activates
    /// straight from the cache — no manifest fetch — so it works with
    /// the backend unreachable.
    private func localBundleRow(_ bundle: SongBundle) -> some View {
        let isActive = appState.currentBundle?.analysisId == bundle.analysisId
        let isLoadingRow = appState.loadingBundleId == bundle.analysisId
        let loadingFraction: Double? = isLoadingRow ? appState.downloadFraction : nil
        return Button {
            Task {
                await appState.loadCachedBundle(bundle, onReady: onActivate)
            }
        } label: {
            HStack(spacing: 12) {
                ArtworkView(analysisId: bundle.analysisId,
                            title: bundle.meta.title,
                            artist: bundle.meta.artist.isEmpty ? nil : bundle.meta.artist,
                            size: 52)
                VStack(alignment: .leading, spacing: 2) {
                    Text(bundle.meta.title.isEmpty ? "Untitled" : bundle.meta.title)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(TFTheme.textPrimary)
                        .lineLimit(1)
                    if let frac = loadingFraction {
                        rowProgressBar(frac)
                    } else if !bundle.meta.artist.isEmpty {
                        Text(bundle.meta.artist)
                            .font(.caption)
                            .foregroundStyle(TFTheme.textSecondary)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: 8)
                if let frac = loadingFraction {
                    Text("\(Int(frac * 100))%")
                        .font(.caption)
                        .foregroundStyle(TFTheme.textSecondary)
                        .monospacedDigit()
                } else if isLoadingRow {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Text(formatDuration(bundle.meta.durationSec))
                        .font(.caption)
                        .foregroundStyle(TFTheme.textSecondary)
                        .monospacedDigit()
                }
                rowMenu {
                    Button(role: .destructive) {
                        Task { await deleteBundle(bundle) }
                    } label: {
                        Label("Delete", systemImage: "trash")
                    }
                }
            }
            .tfLibraryCard(active: isActive)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(appState.isDownloading)
    }

    private func formatDuration(_ seconds: Double) -> String {
        let s = max(0, Int(seconds.rounded(.down)))
        return String(format: "%d:%02d", s / 60, s % 60)
    }

    /// Server + local + in-memory delete via AppState. On failure the
    /// row stays and the error surfaces in the Error section.
    private func delete(_ entry: HistoryEntry) async {
        fetchError = nil
        do {
            try await appState.deleteAnalysis(analysisId: entry.id)
            entries.removeAll { $0.id == entry.id }
        } catch {
            fetchError = error.localizedDescription
        }
    }

    /// Delete a locally cached bundle (server + local + in-memory).
    private func deleteBundle(_ bundle: SongBundle) async {
        fetchError = nil
        do {
            try await appState.deleteAnalysis(analysisId: bundle.analysisId)
            localBundles.removeAll { $0.analysisId == bundle.analysisId }
        } catch {
            fetchError = error.localizedDescription
        }
    }

    private func reload() async {
        isLoading = true
        fetchError = nil
        defer { isLoading = false }
        // Surface the on-device cache immediately — before the network
        // round-trip — so downloaded songs are tappable even while the
        // history fetch is in flight (or hanging against an
        // unreachable debug host, see D-021).
        let cached = (try? appState.bundleStore.listLocalBundles()) ?? []
        localBundles = cached
        do {
            let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
            entries = try await client.fetch(
                baseURL: appState.backendBaseURL,
                query: q.isEmpty ? nil : q,
                limit: 50
            )
            // Online: history rows cover the same songs, so only keep
            // cached bundles the server no longer lists.
            let listed = Set(entries.map(\.id))
            localBundles = cached.filter { !listed.contains($0.analysisId) }
        } catch {
            fetchError = error.localizedDescription
            localBundles = cached
        }
    }
}

