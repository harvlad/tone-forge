// CCTracksSheet.swift
//
// Curated CC0/CC-BY demo-track picker (D-024). Lists the server's
// catalog (GET /api/cc-tracks) with license badges and full credit;
// tapping a track starts a server-side import (POST
// /api/cc-tracks/{id}/import) and hands the returned job id to the
// parent, which feeds it into ImportCoordinator.startCuratedJob so
// the user gets the normal analysis progress sheet.
//
// No attestation gate here: the server owns the audio and the
// license, so there is nothing for the user to attest to.

import SwiftUI
import ToneForgeEngine

struct CCTracksSheet: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    /// Fired when the server accepted the import. The sheet dismisses
    /// itself first so the progress sheet can present cleanly.
    let onImportStarted: (_ jobId: String, _ track: CCTrack) -> Void

    /// Transport seam for tests.
    var client: any CCTrackProviding = BackendCCTrackClient()

    @State private var tracks: [CCTrack] = []
    @State private var isLoading = true
    @State private var errorMessage: String?
    /// Track id with an import POST in flight — rows disable while set.
    @State private var importingTrackId: String?

    var body: some View {
        NavigationStack {
            Group {
                if isLoading {
                    ProgressView("Loading demo tracks…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let errorMessage {
                    VStack(spacing: 12) {
                        Text(errorMessage)
                            .foregroundStyle(.red)
                            .font(.callout)
                            .multilineTextAlignment(.center)
                        Button("Retry") {
                            Task { await load() }
                        }
                    }
                    .padding()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if tracks.isEmpty {
                    Text("No demo tracks published yet.")
                        .foregroundStyle(TFTheme.textSecondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List(tracks) { track in
                        trackRow(track)
                            .listRowBackground(TFTheme.surface)
                    }
                    .listStyle(.plain)
                    .scrollContentBackground(.hidden)
                }
            }
            .background(TFTheme.background)
            .navigationTitle("Demo Tracks")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
            .task { await load() }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - Rows

    private func trackRow(_ track: CCTrack) -> some View {
        Button {
            Task { await startImport(track) }
        } label: {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(track.title)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(TFTheme.textPrimary)
                        .lineLimit(1)
                    if !subtitle(track).isEmpty {
                        Text(subtitle(track))
                            .font(.caption)
                            .foregroundStyle(TFTheme.textSecondary)
                            .lineLimit(2)
                    }
                }
                Spacer(minLength: 8)
                if importingTrackId == track.id {
                    ProgressView().controlSize(.small)
                } else {
                    if !track.license.isEmpty {
                        Text(track.license)
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(TFTheme.textSecondary)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .overlay(Capsule().stroke(TFTheme.stroke, lineWidth: 1))
                    }
                    if let dur = track.durationSec, dur > 0 {
                        Text(formatDuration(dur))
                            .font(.caption)
                            .foregroundStyle(TFTheme.textSecondary)
                            .monospacedDigit()
                    }
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(importingTrackId != nil)
        .accessibilityIdentifier("cctrack-\(track.id)")
    }

    /// "Artist · description" — whichever parts exist.
    private func subtitle(_ track: CCTrack) -> String {
        var parts: [String] = []
        if !track.artist.isEmpty { parts.append(track.artist) }
        if !track.description.isEmpty { parts.append(track.description) }
        return parts.joined(separator: " · ")
    }

    private func formatDuration(_ seconds: Double) -> String {
        let s = max(0, Int(seconds.rounded(.down)))
        return String(format: "%d:%02d", s / 60, s % 60)
    }

    // MARK: - Network

    private func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            tracks = try await client.fetchCatalog(baseURL: appState.backendBaseURL)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func startImport(_ track: CCTrack) async {
        guard importingTrackId == nil else { return }
        importingTrackId = track.id
        defer { importingTrackId = nil }
        do {
            let jobId = try await client.startImport(
                baseURL: appState.backendBaseURL, trackId: track.id
            )
            dismiss()
            onImportStarted(jobId, track)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
