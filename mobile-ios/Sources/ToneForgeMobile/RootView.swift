// RootView.swift
//
// Top-level TabView. Three tabs — the contribution restructure folded
// the old Sketch tab into Play (D-016: sketch is just the Play
// surface with no song loaded), and the old Search tab was dropped:
// it queried the exact same GET /api/history?q= endpoint as Library,
// whose search field now live-searches with the same debounce.
//
//   Library — recent analyses + downloads + search (entry point for
//             songs)
//   Play    — the contribution instrument (NowPlaying + AppMode +
//             8×8 ModeGridView + transport)
//   Profile — backend URL, audio params, saved layers
//
// Loading a song from Library flips selection to Play so the user
// lands on the surface where they actually play/contribute.

import SwiftUI
import ToneForgeEngine
#if canImport(UIKit)
import UIKit
#endif

public struct RootView: View {
    @EnvironmentObject private var appState: AppState
    @State private var selection: Int = 0

    public init() {}

    public var body: some View {
        TabView(selection: $selection) {
            LibraryView(onActivate: { selection = 1 })
                .tabItem { Label("Library", systemImage: "music.note.list") }
                .tag(0)

            PlayView()
                .tabItem { Label("Play", systemImage: "play.circle.fill") }
                .tag(1)

            ProfileView()
                .tabItem { Label("Profile", systemImage: "person.circle") }
                .tag(2)
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

/// Recent-analyses list backed by GET /api/history. Tapping a row
/// downloads the bundle + stems and switches to the Play tab.
struct LibraryView: View {
    @EnvironmentObject private var appState: AppState

    @State private var query: String = ""
    @State private var entries: [HistoryEntry] = []
    @State private var isLoading: Bool = false
    @State private var fetchError: String?
    /// Live-search debounce token — bumped on every keystroke; only
    /// the latest task actually issues the network call (inherited
    /// from the removed Search tab).
    @State private var searchToken: UUID = UUID()
    @State private var quickLoadId: String = ""
    @State private var backendText: String = ""

    // Import flow (Music library / Files → attestation → analyze).
    // The analyze transport is stubbed under `-uitest-stub-import`.
    @StateObject private var importer = ImportCoordinator(
        analyzeClient: UITestSupport.makeAnalyzeClient()
    )
    @State private var showMusicPicker = false
    @State private var showFilePicker = false

    /// Called once a bundle activates so the parent can flip tabs to
    /// Play.
    let onActivate: () -> Void

    private var client: HistoryClient { HistoryClient() }

    var body: some View {
        NavigationStack {
            List {
                uitestStubImportSection
                backendSection
                if !entries.isEmpty {
                    Section("Recent") {
                        ForEach(entries) { entry in
                            entryRow(entry)
                                .swipeActions(edge: .trailing) {
                                    Button(role: .destructive) {
                                        Task { await delete(entry) }
                                    } label: {
                                        Label("Delete", systemImage: "trash")
                                    }
                                }
                        }
                    }
                } else if isLoading {
                    Section {
                        HStack {
                            ProgressView()
                            Text("Loading history…").foregroundStyle(.secondary)
                        }
                    }
                } else {
                    Section {
                        Text("No history yet — analyze a song from the backend and it will show up here.")
                            .foregroundStyle(.secondary)
                            .font(.callout)
                    }
                }

                if let err = fetchError {
                    Section("Error") { Text(err).foregroundStyle(.red) }
                }

                if appState.isDownloading, !appState.downloadProgress.isEmpty {
                    Section("Downloading stems") {
                        ForEach(appState.downloadProgress.keys.sorted(), id: \.self) { role in
                            if let prog = appState.downloadProgress[role] {
                                stemProgressRow(role: role, progress: prog)
                            }
                        }
                    }
                }

                if let err = appState.loadingError {
                    Section("Load error") { Text(err).foregroundStyle(.red) }
                }

                if let bundle = appState.currentBundle {
                    Section("Loaded") {
                        LabeledContent("Title", value: bundle.meta.title)
                        LabeledContent("Duration", value: String(format: "%.1fs", bundle.meta.durationSec))
                        LabeledContent("Stems", value: String(bundle.stems.count))
                        Button("Open in Play") { onActivate() }
                    }
                }
            }
            .searchable(text: $query, prompt: "Search songs")
            .onSubmit(of: .search) {
                Task { await reload() }
            }
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
            .navigationTitle("Library")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    importMenu
                }
            }
            .task {
                if entries.isEmpty { await reload() }
            }
            .onAppear {
                if backendText.isEmpty {
                    backendText = appState.backendBaseURL.absoluteString
                }
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

    // MARK: - Import entry points

    private var importMenu: some View {
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
        } label: {
            Label("Import", systemImage: "plus")
        }
        .accessibilityIdentifier("import-menu")
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

    // MARK: - Backend URL editing

    private var backendURLChanged: Bool {
        !backendText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func commitBackendURL() {
        let trimmed = backendText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let url = URL(string: trimmed) else { return }
        appState.backendBaseURL = url
        Task { await reload() }
    }

    // MARK: - Sections / rows

    /// DEBUG-only: staging-URL override + raw analysisId quick-load.
    /// Release builds use `AppConfig.defaultBackendURL` with no editor.
    @ViewBuilder
    private var backendSection: some View {
        #if DEBUG
        Section("Backend (debug)") {
            HStack {
                TextField("Base URL", text: $backendText)
                    .autocorrectionDisabled()
                    #if os(iOS)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.URL)
                    .submitLabel(.done)
                    #endif
                    .onSubmit { commitBackendURL() }
                Button("Set") { commitBackendURL() }
                    .buttonStyle(.borderless)
                    .disabled(!backendURLChanged)
            }
            HStack {
                TextField("Quick-load analysisId", text: $quickLoadId)
                    .autocorrectionDisabled()
                    #if os(iOS)
                    .textInputAutocapitalization(.never)
                    #endif
                Button("Load") {
                    let id = quickLoadId.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !id.isEmpty else { return }
                    Task {
                        await appState.loadBundle(analysisId: id)
                        if appState.loadingError == nil { onActivate() }
                    }
                }
                .buttonStyle(.borderless)
                .disabled(quickLoadId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || appState.isDownloading)
            }
        }
        #endif
    }

    @ViewBuilder
    private func entryRow(_ entry: HistoryEntry) -> some View {
        Button {
            Task {
                await appState.loadBundle(analysisId: entry.id)
                if appState.loadingError == nil { onActivate() }
            }
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(entry.name ?? "Untitled")
                    .font(.body.weight(.medium))
                    .foregroundStyle(.primary)
                HStack(spacing: 8) {
                    if let type = entry.detectedType, !type.isEmpty {
                        Text(type).font(.caption).foregroundStyle(.secondary)
                    }
                    if let dur = entry.duration {
                        Text(formatDuration(dur)).font(.caption).foregroundStyle(.secondary)
                    }
                    if let amp = entry.ampFamily, !amp.isEmpty {
                        Text(amp).font(.caption).foregroundStyle(.secondary)
                    }
                }
                if let summary = entry.summary, !summary.isEmpty {
                    Text(summary).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(appState.isDownloading)
    }

    private func stemProgressRow(role: String, progress: BundleStore.StemProgress) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(role.capitalized)
                Spacer()
                if progress.isComplete {
                    Text("Done").font(.caption).foregroundStyle(.secondary)
                } else if progress.bytesTotal > 0 {
                    let pct = Int(Double(progress.bytesDownloaded) / Double(progress.bytesTotal) * 100)
                    Text("\(pct)%").font(.caption).foregroundStyle(.secondary)
                } else {
                    Text(formatBytes(progress.bytesDownloaded))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            ProgressView(
                value: Double(progress.bytesDownloaded),
                total: Double(max(progress.bytesTotal, progress.bytesDownloaded, 1))
            )
        }
    }

    private func formatDuration(_ seconds: Double) -> String {
        let s = max(0, Int(seconds.rounded(.down)))
        return String(format: "%d:%02d", s / 60, s % 60)
    }

    private func formatBytes(_ bytes: Int64) -> String {
        let mb = Double(bytes) / 1024.0 / 1024.0
        if mb >= 1 { return String(format: "%.1f MB", mb) }
        return String(format: "%.0f KB", Double(bytes) / 1024.0)
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

    private func reload() async {
        isLoading = true
        fetchError = nil
        defer { isLoading = false }
        do {
            let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
            entries = try await client.fetch(
                baseURL: appState.backendBaseURL,
                query: q.isEmpty ? nil : q,
                limit: 50
            )
        } catch {
            fetchError = error.localizedDescription
        }
    }
}

