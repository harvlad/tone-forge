// DemoTracksSheet.swift
//
// Curated CC demo tracks (D-024): every row shows full attribution
// (artist, license) exactly like the web/mobile pickers. Selecting a
// row starts a server-side import.

import SwiftUI
import JamDesktopCore
import ToneForgeEngine

struct DemoTracksSheet: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var intake: IntakeModel
    @Environment(\.dismiss) private var dismiss

    let onSelect: (CCTrack) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Demo tracks")
                    .font(.title2.bold())
                Spacer()
                Button("Close") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding()

            Divider()

            content
        }
        .frame(width: 480, height: 420)
        .background(JamTheme.background)
        .preferredColorScheme(.dark)
        .tint(JamTheme.accent)
        .task {
            await intake.loadDemoTracks(baseURL: model.backendBaseURL)
        }
    }

    @ViewBuilder
    private var content: some View {
        if let error = intake.demoTracksError {
            ContentUnavailableView(
                "Couldn't load tracks",
                systemImage: "exclamationmark.triangle",
                description: Text(error)
            )
        } else if intake.demoTracks.isEmpty {
            ProgressView("Loading…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            List(intake.demoTracks) { track in
                Button {
                    onSelect(track)
                } label: {
                    trackRow(track)
                }
                .buttonStyle(.plain)
            }
            .listStyle(.inset)
        }
    }

    private func trackRow(_ track: CCTrack) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(track.title)
                    .font(.headline)
                Spacer()
                if let duration = track.durationSec {
                    Text(timeString(duration))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            // D-024 credit line: artist + license always visible.
            Text(creditLine(track))
                .font(.caption)
                .foregroundStyle(.secondary)
            if !track.description.isEmpty {
                Text(track.description)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .lineLimit(2)
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }

    private func creditLine(_ track: CCTrack) -> String {
        var parts: [String] = []
        if !track.artist.isEmpty { parts.append(track.artist) }
        if !track.license.isEmpty { parts.append(track.license) }
        return parts.joined(separator: " · ")
    }

    private func timeString(_ s: Double) -> String {
        let total = max(0, Int(s.rounded(.down)))
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}
