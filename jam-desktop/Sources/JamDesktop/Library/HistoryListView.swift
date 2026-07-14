// HistoryListView.swift
//
// Recent analyses under the intake card. Click loads the song into
// Perform; right-click deletes. Shows the D-024 artist/license line
// when present.

import SwiftUI
import JamDesktopCore
import ToneForgeEngine

struct HistoryListView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var history: HistoryModel
    @EnvironmentObject private var studio: StudioModel

    /// Entry the user clicked — scopes the inline spinner/error to the
    /// row that started the load so a failure isn't silent.
    @State private var loadingEntryId: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Recent songs")
                    .font(.headline)
                Spacer()
                TextField("Search", text: queryBinding)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 180)
                    .onSubmit {
                        Task { await history.refresh(baseURL: model.backendBaseURL) }
                    }
            }

            if history.isLoading && history.entries.isEmpty {
                ProgressView()
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
            } else if let error = history.error {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else if history.entries.isEmpty {
                Text("No songs yet — analyze one above.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                entriesList
            }
        }
    }

    private var entriesList: some View {
        VStack(spacing: 4) {
            ForEach(history.entries) { entry in
                Button {
                    loadingEntryId = entry.id
                    Task { await model.loadSession(analysisId: entry.id) }
                } label: {
                    row(entry)
                }
                .buttonStyle(.plain)
                .disabled(model.isLoadingSession)
                .contextMenu {
                    Button("Open in Studio") {
                        model.view = .studio
                        Task {
                            await studio.load(
                                baseURL: model.backendBaseURL, id: entry.id)
                        }
                    }
                    Button("Delete", role: .destructive) {
                        Task {
                            await history.delete(
                                baseURL: model.backendBaseURL, entryId: entry.id)
                        }
                    }
                }
            }
        }
    }

    private func row(_ entry: HistoryEntry) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(entry.name ?? "Untitled")
                    .font(.body)
                if let subtitle = subtitle(entry) {
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if loadingEntryId == entry.id, !model.isLoadingSession,
                   let error = model.sessionError {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(JamTheme.error)
                }
            }
            Spacer()
            if loadingEntryId == entry.id, model.isLoadingSession {
                ProgressView()
                    .controlSize(.small)
            }
            if let duration = entry.duration {
                Text(timeString(duration))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 8)
        .jamTile()
        .contentShape(Rectangle())
    }

    /// Artist · license (D-024) when curated; falls back to summary.
    private func subtitle(_ entry: HistoryEntry) -> String? {
        var parts: [String] = []
        if let artist = entry.artist, !artist.isEmpty { parts.append(artist) }
        if let license = entry.license, !license.isEmpty { parts.append(license) }
        if parts.isEmpty, let summary = entry.summary, !summary.isEmpty {
            parts.append(summary)
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private func timeString(_ s: Double) -> String {
        let total = max(0, Int(s.rounded(.down)))
        return String(format: "%d:%02d", total / 60, total % 60)
    }

    private var queryBinding: Binding<String> {
        Binding(get: { history.query }, set: { history.query = $0 })
    }
}
