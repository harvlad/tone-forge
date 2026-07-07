// MusicLibraryPickerView.swift
//
// Music-library track picker for import. Analysable tracks are
// tappable; DRM-protected and not-downloaded tracks render greyed out
// and disabled with the reason from `LibraryTrack.unavailabilityReason`
// ("streaming (DRM) — not analysable" / "not downloaded — not
// analysable"). The filtering policy itself lives on LibraryTrack in
// ToneForgeEngine.

import SwiftUI
import ToneForgeEngine

struct MusicLibraryPickerView: View {
    @Environment(\.dismiss) private var dismiss

    let source: any MediaLibrarySource
    let onSelect: (LibraryTrack) -> Void

    @State private var authorization: MediaLibraryAuthorization = .notDetermined
    @State private var tracks: [LibraryTrack] = []
    @State private var query = ""

    var body: some View {
        NavigationStack {
            Group {
                switch authorization {
                case .authorized:
                    trackList
                case .notDetermined:
                    ProgressView("Requesting access…")
                case .denied, .restricted:
                    deniedView
                }
            }
            .navigationTitle("Music Library")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
        .task {
            authorization = source.authorization
            if authorization == .notDetermined {
                authorization = await source.requestAuthorization()
            }
            if authorization == .authorized {
                tracks = source.allTracks()
            }
        }
    }

    private var filteredTracks: [LibraryTrack] {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return tracks }
        return tracks.filter {
            $0.title.localizedCaseInsensitiveContains(q)
                || $0.artist.localizedCaseInsensitiveContains(q)
        }
    }

    private var trackList: some View {
        List(filteredTracks) { track in
            trackRow(track)
        }
        .searchable(text: $query, prompt: "Search songs")
        .overlay {
            if tracks.isEmpty {
                ContentUnavailableView(
                    "No songs",
                    systemImage: "music.note",
                    description: Text("Your music library has no songs on this device.")
                )
            }
        }
    }

    @ViewBuilder
    private func trackRow(_ track: LibraryTrack) -> some View {
        Button {
            onSelect(track)
            dismiss()
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(track.title)
                    .font(.body.weight(.medium))
                HStack(spacing: 8) {
                    Text(track.artist)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(formatDuration(track.durationSec))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let reason = track.unavailabilityReason {
                    Text(reason)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .opacity(track.isAnalysable ? 1 : 0.4)
        }
        .buttonStyle(.plain)
        .disabled(!track.isAnalysable)
    }

    private var deniedView: some View {
        VStack(spacing: 12) {
            ContentUnavailableView(
                "No library access",
                systemImage: "music.note.house",
                description: Text(
                    "Allow music library access in Settings to import songs you own."
                )
            )
            #if os(iOS)
            Button("Open Settings") {
                if let url = URL(string: UIApplication.openSettingsURLString) {
                    UIApplication.shared.open(url)
                }
            }
            .buttonStyle(.borderedProminent)
            #endif
        }
    }

    private func formatDuration(_ seconds: Double) -> String {
        let s = max(0, Int(seconds.rounded(.down)))
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}
