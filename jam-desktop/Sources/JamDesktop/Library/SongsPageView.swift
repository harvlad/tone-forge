// SongsPageView.swift
//
// The full-screen "Songs" page: ONE searchable / filterable / sortable
// table that replaces the cramped Recent-Songs sidebar list AND the Band
// Room card stack. Rows are the union of finished analyses and in-flight
// jobs (SongsModel over /api/library/search, with the live analysis queue
// overlaid); a completing job collapses into its history row. Band Room
// is now the "Processing (N)" chip + the Status column, not a separate
// destination.
//
// Source tabs make the source pluggable: My Library is wired; the Vinyl
// Crate + external CC catalogs are declared coming-soon so they drop in
// behind the same shell. The table is a SwiftUI `Table` — NSTableView-
// backed, so it windows rows instead of rendering an entire large library.

import SwiftUI
import JamDesktopCore
import ToneForgeEngine

struct SongsPageView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var songs: SongsModel
    @EnvironmentObject private var queue: AnalysisQueueModel

    /// Debounce the search field so each keystroke doesn't fire a query.
    @State private var searchDebounce: Task<Void, Never>?

    /// Local edit buffers for the tempo range fields. A numeric TextField
    /// needs real editable state — binding straight through a Double?
    /// filter (formatting on every keystroke) fights the user's typing —
    /// so we commit min/max to the filter only on submit, and mirror
    /// external resets (Clear filters) back into these.
    @State private var tempoMinText = ""
    @State private var tempoMaxText = ""

    var body: some View {
        VStack(spacing: 0) {
            topBar
            Divider().overlay(JamTheme.stroke)
            HStack(spacing: 0) {
                facetRail
                Divider().overlay(JamTheme.stroke)
                mainColumn
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(JamTheme.background)
        .task {
            songs.liveItems = queue.items
            if songs.serverTracks.isEmpty { await songs.reload(baseURL: model.backendBaseURL) }
        }
        // Keep the live overlay fresh: a new upload/URL job shows as a
        // processing row instantly, and running percents track the SSE.
        .onChange(of: queue.items) { _, items in
            songs.liveItems = items
        }
        // A job just finished → the server union now has a history row for
        // it; pull it so the processing row collapses into the done row.
        .onReceive(NotificationCenter.default.publisher(for: .songsShouldRefresh)) { _ in
            Task { await songs.reload(baseURL: model.backendBaseURL) }
        }
    }

    // MARK: - Top bar (title, source tabs, search, sort)

    private var topBar: some View {
        VStack(spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("Songs")
                    .font(.largeTitle.bold())
                if let total = songs.total {
                    Text("\(total)")
                        .font(.title3.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                Spacer()
                sortMenu
            }
            HStack(spacing: 16) {
                sourceTabs
                Spacer()
                searchField
            }
        }
        .padding(.horizontal, 24)
        .padding(.top, 20)
        .padding(.bottom, 14)
    }

    private var sourceTabs: some View {
        HStack(spacing: 6) {
            ForEach(SongsPageView.sourceTabs, id: \.id) { tab in
                let active = songs.source == tab.id
                Button {
                    guard tab.enabled, !active else { return }
                    songs.source = tab.id
                    Task { await songs.reload(baseURL: model.backendBaseURL) }
                } label: {
                    HStack(spacing: 5) {
                        Text(tab.title)
                        if !tab.enabled {
                            Text("Soon")
                                .font(.caption2.weight(.semibold))
                                .padding(.horizontal, 5)
                                .padding(.vertical, 1)
                                .background(Capsule().fill(.white.opacity(0.08)))
                        }
                    }
                    .font(.callout.weight(active ? .semibold : .regular))
                    .foregroundStyle(active ? Color.white : (tab.enabled ? JamTheme.textSecondary : Color.white.opacity(0.3)))
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(
                        RoundedRectangle(cornerRadius: 8)
                            .fill(active ? JamTheme.accent.opacity(0.22) : .clear)
                    )
                }
                .buttonStyle(.plain)
                .disabled(!tab.enabled)
                .help(tab.enabled ? tab.title : "\(tab.title) — coming soon")
            }
        }
    }

    private var searchField: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(.secondary)
            TextField("Search songs…", text: $songs.query)
                .textFieldStyle(.plain)
                .frame(width: 220)
                .onChange(of: songs.query) { _, _ in
                    // 300ms debounce — reload after typing settles.
                    searchDebounce?.cancel()
                    searchDebounce = Task {
                        try? await Task.sleep(nanoseconds: 300_000_000)
                        if !Task.isCancelled {
                            await songs.reload(baseURL: model.backendBaseURL)
                        }
                    }
                }
                .onSubmit {
                    searchDebounce?.cancel()
                    Task { await songs.reload(baseURL: model.backendBaseURL) }
                }
            if !songs.query.isEmpty {
                Button {
                    songs.query = ""
                    Task { await songs.reload(baseURL: model.backendBaseURL) }
                } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 8).fill(JamTheme.surface))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(JamTheme.stroke))
    }

    private var sortMenu: some View {
        Menu {
            ForEach(SongsSort.allCases, id: \.self) { option in
                Button {
                    songs.setSort(option, baseURL: model.backendBaseURL)
                } label: {
                    if songs.sort == option {
                        Label(option.label, systemImage: "checkmark")
                    } else {
                        Text(option.label)
                    }
                }
            }
        } label: {
            Label("Sort: \(songs.sort.label)", systemImage: "arrow.up.arrow.down")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
    }

    // MARK: - Facet rail

    private var facetRail: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                statusChips
                tempoRange
                facetSection(title: "Genre", field: "genre")
                facetSection(title: "Key", field: "key")
                facetSection(title: "Mood", field: "mood")
                facetSection(title: "Tags", field: "tags")

                if !songs.filters.isEmpty || !songs.query.isEmpty {
                    Button {
                        songs.clearFilters(baseURL: model.backendBaseURL)
                    } label: {
                        Label("Clear filters", systemImage: "xmark")
                            .font(.caption)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(JamTheme.accent)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(width: 220)
    }

    private var statusChips: some View {
        VStack(alignment: .leading, spacing: 8) {
            railHeader("Status")
            statusChip("All", value: nil, count: nil)
            statusChip("Processing", value: "processing",
                       count: songs.processingCount == 0 ? nil : songs.processingCount)
            statusChip("Done", value: "done", count: facetCount("status", "done"))
            statusChip("Error", value: "error", count: facetCount("status", "error"))
        }
    }

    private func statusChip(_ label: String, value: String?, count: Int?) -> some View {
        let active = songs.filters.status == value
        return Button {
            songs.setStatusFilter(value, baseURL: model.backendBaseURL)
        } label: {
            HStack(spacing: 6) {
                Text(label).font(.callout)
                Spacer()
                if let count {
                    Text("\(count)")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 7)
                    .fill(active ? JamTheme.accent.opacity(0.22) : .clear)
            )
            .foregroundStyle(active ? Color.white : JamTheme.textPrimary)
        }
        .buttonStyle(.plain)
    }

    private var tempoRange: some View {
        VStack(alignment: .leading, spacing: 8) {
            railHeader("Tempo (BPM)")
            HStack(spacing: 6) {
                tempoField("Min", text: $tempoMinText, onCommit: commitTempo)
                Text("–").foregroundStyle(.secondary)
                tempoField("Max", text: $tempoMaxText, onCommit: commitTempo)
            }
        }
        // Mirror external filter changes (Clear filters / programmatic
        // reset) back into the edit buffers so a cleared field goes blank.
        .onChange(of: songs.filters.tempoMin) { _, v in
            tempoMinText = v.map { String(Int($0)) } ?? ""
        }
        .onChange(of: songs.filters.tempoMax) { _, v in
            tempoMaxText = v.map { String(Int($0)) } ?? ""
        }
    }

    /// Parse both buffers and push the range in one shot. Empty / non-
    /// numeric text becomes nil (that bound is dropped) rather than 0.
    private func commitTempo() {
        let lo = Double(tempoMinText.trimmingCharacters(in: .whitespaces))
        let hi = Double(tempoMaxText.trimmingCharacters(in: .whitespaces))
        songs.setTempoRange(min: lo, max: hi, baseURL: model.backendBaseURL)
    }

    private func tempoField(_ placeholder: String, text: Binding<String>,
                            onCommit: @escaping () -> Void) -> some View {
        TextField(placeholder, text: text)
            .textFieldStyle(.plain)
            .frame(width: 54)
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .background(RoundedRectangle(cornerRadius: 6).fill(JamTheme.surface))
            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(JamTheme.stroke))
            .onSubmit(onCommit)
    }

    @ViewBuilder
    private func facetSection(title: String, field: String) -> some View {
        let buckets = songs.facets[field] ?? []
        if !buckets.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                railHeader(title)
                ForEach(buckets) { bucket in
                    facetPill(field: field, bucket: bucket)
                }
            }
        }
    }

    private func facetPill(field: String, bucket: FacetBucket) -> some View {
        let active = songs.isFacetSelected(field: field, value: bucket.value)
        return Button {
            songs.toggleFacet(field: field, value: bucket.value,
                              baseURL: model.backendBaseURL)
        } label: {
            HStack(spacing: 6) {
                Text(bucket.displayLabel).font(.callout).lineLimit(1)
                Spacer()
                Text("\(bucket.count)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 7)
                    .fill(active ? JamTheme.accent.opacity(0.22) : .clear)
            )
            .foregroundStyle(active ? Color.white : JamTheme.textPrimary)
        }
        .buttonStyle(.plain)
    }

    private func railHeader(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.caption2.weight(.semibold))
            .foregroundStyle(.secondary)
    }

    // MARK: - Main column (table + footer)

    private var mainColumn: some View {
        VStack(spacing: 0) {
            if songs.isLoading && songs.displayTracks.isEmpty {
                loadingState
            } else if songs.displayTracks.isEmpty {
                emptyState
            } else {
                songsTable
                footer
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var songsTable: some View {
        Table(songs.displayTracks) {
            TableColumn("Title") { track in
                HStack(spacing: 10) {
                    ArtworkImage(
                        analysisId: track.historyId ?? track.sourceRef,
                        artist: track.artist,
                        title: track.title,
                        size: 30
                    )
                    Text(track.title ?? "Untitled")
                        .fontWeight(.medium)
                        .lineLimit(1)
                }
            }
            .width(min: 200, ideal: 280)

            TableColumn("Artist") { track in
                Text(track.artist ?? "—")
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            .width(min: 90, ideal: 140)

            TableColumn("Key") { track in
                Text(track.key ?? "—").foregroundStyle(.secondary)
            }
            .width(min: 44, ideal: 70)

            TableColumn("Tempo") { track in
                Text(track.tempoBpm.map { "\(Int($0.rounded()))" } ?? "—")
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            .width(min: 44, ideal: 64)

            TableColumn("Time") { track in
                Text(Self.formatDuration(track.durationS))
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            .width(min: 44, ideal: 64)

            TableColumn("Genre") { track in
                Text(track.genre?.capitalized ?? "—")
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            .width(min: 70, ideal: 110)

            TableColumn("Status") { track in
                StatusBadge(status: track.status, progress: track.progressFraction)
            }
            .width(min: 90, ideal: 120)

            TableColumn("") { track in
                actionCell(track)
            }
            .width(min: 90, ideal: 130)
        }
        .scrollContentBackground(.hidden)
    }

    @ViewBuilder
    private func actionCell(_ track: SourceTrack) -> some View {
        switch track.status {
        case .done, .unknown:
            Button("Open") { open(track) }
                .buttonStyle(.borderedProminent)
                .tint(JamTheme.accent)
                .controlSize(.small)
                .disabled(model.isLoadingSession || (track.historyId == nil && track.sourceRef.isEmpty))

        case .running:
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                if let pct = track.progressFraction {
                    Text("\(Int((pct * 100).rounded()))%")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }

        case .queued:
            Text("Queued")
                .font(.caption)
                .foregroundStyle(.secondary)

        case .error:
            HStack(spacing: 8) {
                Button("Retry") { retry(track) }
                    .controlSize(.small)
                Button("Dismiss") { dismiss(track) }
                    .controlSize(.small)
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var footer: some View {
        HStack {
            if songs.isLoadingMore {
                ProgressView().controlSize(.small)
            } else if songs.hasMore {
                Button("Load more") {
                    Task { await songs.loadMore(baseURL: model.backendBaseURL) }
                }
                .buttonStyle(.plain)
                .foregroundStyle(JamTheme.accent)
            }
            Spacer()
            if let err = songs.error {
                Label(err, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(JamTheme.error)
                    .lineLimit(1)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
    }

    private var loadingState: some View {
        VStack(spacing: 12) {
            ProgressView()
            Text("Loading songs…").font(.callout).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var emptyState: some View {
        VStack(spacing: 14) {
            ContentUnavailableView(
                songs.filters.isEmpty && songs.query.isEmpty
                    ? "No songs yet"
                    : "No matches",
                systemImage: "music.note.list",
                description: Text(
                    songs.filters.isEmpty && songs.query.isEmpty
                        ? "Analyze a song from the Intake view — it lands here."
                        : "Try clearing a filter or search term."
                )
            )
            if !songs.filters.isEmpty || !songs.query.isEmpty {
                Button("Clear filters") {
                    songs.clearFilters(baseURL: model.backendBaseURL)
                }
            } else {
                Button("Back to Intake") { model.view = .intake }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Actions

    /// Deep-open a finished analysis — unchanged /api/history/{id} path.
    private func open(_ track: SourceTrack) {
        guard let id = track.historyId ?? (track.sourceRef.isEmpty ? nil : track.sourceRef) else { return }
        Task { await model.loadSession(analysisId: id) }
    }

    /// Retry a failed row through the one ingest door (content-hash dedupe
    /// reuses an already-analyzed track), then refresh.
    private func retry(_ track: SourceTrack) {
        Task {
            _ = await songs.ingest(baseURL: model.backendBaseURL, track: track)
            await songs.reload(baseURL: model.backendBaseURL)
        }
    }

    /// Dismiss an errored row: if it's a live queue item, drop it from the
    /// queue (which also persists the dismissal); then refresh.
    private func dismiss(_ track: SourceTrack) {
        if let item = queue.items.first(where: { SongsModel.liveKey($0) == track.mergeKey }) {
            queue.dismiss(id: item.id)
        }
        Task { await songs.reload(baseURL: model.backendBaseURL) }
    }

    // MARK: - Helpers

    private func facetCount(_ field: String, _ value: String) -> Int? {
        songs.facets[field]?.first(where: { $0.value == value })?.count
    }

    static func formatDuration(_ seconds: Double?) -> String {
        guard let s = seconds, s > 0 else { return "—" }
        let mins = Int(s) / 60
        let secs = Int(s) % 60
        return String(format: "%d:%02d", mins, secs)
    }

    // Source tabs: My Library wired; the rest declared coming-soon so the
    // pluggable shell is visible before those sources land.
    struct SourceTab { let id: SourceId; let title: String; let enabled: Bool }
    static let sourceTabs: [SourceTab] = [
        SourceTab(id: .library, title: "My Library", enabled: true),
        SourceTab(id: .crate, title: "Vinyl Crate", enabled: false),
        SourceTab(id: .jamendo, title: "Jamendo", enabled: false),
        SourceTab(id: .ccmixter, title: "ccMixter", enabled: false),
        SourceTab(id: .device, title: "This Mac", enabled: false),
    ]
}

// MARK: - Status badge

private struct StatusBadge: View {
    let status: TrackStatus
    let progress: Double?

    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(label).font(.caption)
        }
        .foregroundStyle(color)
    }

    private var label: String {
        switch status {
        case .done, .unknown: return "Ready"
        case .running:
            if let p = progress { return "Running \(Int((p * 100).rounded()))%" }
            return "Running"
        case .queued: return "Queued"
        case .error: return "Error"
        }
    }

    private var color: Color {
        switch status {
        case .done, .unknown: return JamTheme.brandGreenDark
        case .running: return JamTheme.accent
        case .queued: return .orange
        case .error: return JamTheme.error
        }
    }
}

// MARK: - Cross-view refresh signal

public extension Notification.Name {
    /// Posted when a background analysis completes (RootView's
    /// queue.onJobCompleted) so an open Songs page pulls the new history
    /// row and the processing row collapses into it.
    static let songsShouldRefresh = Notification.Name("jamn.songsShouldRefresh")
}
