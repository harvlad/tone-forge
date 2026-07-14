// RecordingsListView.swift
//
// Saved layer takes (P4): list of SessionCaptures with replay,
// bounce-to-WAV and delete actions. Presented as a sheet from the
// toolbar. Replay rewinds the song and re-fires the take's pad
// events against the current grid; bounce renders offline and
// reveals the WAV in Finder.

import SwiftUI
import AppKit
import ToneForgeEngine
import JamDesktopCore

struct RecordingsListView: View {
    @EnvironmentObject private var session: SessionController
    @Environment(\.dismiss) private var dismiss

    @State private var bouncingSessionId: UUID?
    @State private var bounceError: String?

    private var recording: RecordingModel { session.recording }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if recording.recordings.isEmpty {
                emptyState
            } else {
                list
            }
            if let bounceError {
                Text(bounceError)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .padding(8)
            }
        }
        .frame(width: 460, height: 380)
        .background(JamTheme.background)
        .onAppear { recording.refresh() }
    }

    private var header: some View {
        HStack {
            Text("Recordings")
                .font(.headline)
            Spacer()
            Button { dismiss() } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.escape, modifiers: [])
        }
        .padding(12)
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "record.circle")
                .font(.largeTitle)
                .foregroundStyle(.secondary)
            Text("No takes yet")
                .foregroundStyle(.secondary)
            Text("Arm the record button in the transport bar, then play pads.")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var list: some View {
        List(recording.recordings, id: \.sessionId) { take in
            row(take)
        }
        .listStyle(.inset)
        .scrollContentBackground(.hidden)
    }

    private func row(_ take: SessionCapture) -> some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(take.capturedAt, format: .dateTime
                    .month(.abbreviated).day().hour().minute())
                    .font(.callout)
                Text("\(take.events.count) events · \(durationString(take))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()

            if recording.replayingSessionId == take.sessionId {
                Button {
                    session.stopReplay()
                } label: {
                    Image(systemName: "stop.fill")
                }
                .help("Stop replay")
            } else {
                Button {
                    session.startReplay(take)
                } label: {
                    Image(systemName: "play.fill")
                }
                .disabled(session.attachedAnalysisId == nil)
                .help("Replay over the song")
            }

            Button {
                bounce(take)
            } label: {
                if bouncingSessionId == take.sessionId {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "square.and.arrow.down")
                }
            }
            .disabled(bouncingSessionId != nil
                || session.attachedAnalysisId == nil)
            .help("Bounce to WAV")

            Button(role: .destructive) {
                recording.delete(take)
            } label: {
                Image(systemName: "trash")
            }
            .help("Delete take")
        }
        .buttonStyle(.borderless)
        .padding(.vertical, 2)
    }

    private func bounce(_ take: SessionCapture) {
        bouncingSessionId = take.sessionId
        bounceError = nil
        Task {
            defer { bouncingSessionId = nil }
            do {
                let url = try await session.bounce(take)
                NSWorkspace.shared.activateFileViewerSelecting([url])
            } catch {
                bounceError = error.localizedDescription
            }
        }
    }

    private func durationString(_ take: SessionCapture) -> String {
        let s = take.durationSec
        return String(format: "%d:%04.1f", Int(s) / 60,
                      s.truncatingRemainder(dividingBy: 60))
    }
}
