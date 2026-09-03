// IntakeView.swift
//
// "What are we playing?" — parity with the web intake card:
// engine banner, URL paste, file drop/browse (with ownership
// attestation), curated demo tracks, instrument selector, and the
// recent-history list underneath. Kicking off any flow flips the app
// to the Band Room where progress renders.

import SwiftUI
import JamDesktopCore

struct IntakeView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var intake: IntakeModel
    @EnvironmentObject private var queue: AnalysisQueueModel
    @EnvironmentObject private var history: HistoryModel

    @State private var sourceUrl = ""
    @State private var showingDemoTracks = false

    var body: some View {
        ScrollView {
            VStack(spacing: 40) {
                // Hero headline
                VStack(spacing: 8) {
                    Text("What are we playing?")
                        .font(.system(size: 42, weight: .bold))
                        .foregroundStyle(.white)

                    (Text("Upload a song.")
                        .foregroundStyle(.white)
                     + Text(" Become part of it.")
                        .foregroundStyle(.white.opacity(0.5)))
                        .font(.system(size: 20, weight: .regular))
                }
                .frame(maxWidth: .infinity)
                .padding(.top, 32)

                // Upload card
                VStack(spacing: 24) {
                    // Music icon
                    Circle()
                        .fill(JamTheme.accent.opacity(0.2))
                        .frame(width: 64, height: 64)
                        .overlay {
                            Image(systemName: "music.note")
                                .font(.system(size: 24))
                                .foregroundStyle(JamTheme.accent)
                        }

                    VStack(spacing: 6) {
                        Text("Upload a song")
                            .font(.title2.bold())
                            .foregroundStyle(.white)
                        Text("Supported: MP3 \u{2022} WAV \u{2022} M4A")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }

                    // Drop zone
                    UploadDropZone { fileURL in
                        startUpload(fileURL)
                    }

                    // Engine status
                    HStack(spacing: 8) {
                        Circle()
                            .fill(engineOnline ? Color.green : Color.orange)
                            .frame(width: 8, height: 8)
                        Text(engineOnline ? "Ready" : "Offline")
                            .font(.subheadline)
                            .foregroundStyle(.white)
                    }

                    // Honest per-backend: the local-analysis promise is
                    // only true when the backend IS this computer.
                    Text(isLocalBackend
                         ? "Audio stays on your computer."
                         : "Uploads securely to your Jamn server for analysis.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(32)
                .frame(maxWidth: 600)
                .background(
                    RoundedRectangle(cornerRadius: JamTheme.cardCornerRadius)
                        .fill(JamTheme.surface)
                        .overlay(
                            RoundedRectangle(cornerRadius: JamTheme.cardCornerRadius)
                                .strokeBorder(JamTheme.accent.opacity(0.4), lineWidth: 1)
                        )
                        .shadow(color: JamTheme.accent.opacity(0.3), radius: 20, x: 0, y: 0)
                )

                // Start Jam button
                Button {
                    showingDemoTracks = true
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "play.fill")
                        Text("Start Jam")
                    }
                    .font(.headline)
                    .foregroundStyle(.white)
                    .padding(.horizontal, 32)
                    .padding(.vertical, 14)
                    .background(JamTheme.accentGradient, in: Capsule())
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 40)
            .padding(.vertical, 24)
            .frame(maxWidth: .infinity)
        }
        .sheet(isPresented: $showingDemoTracks) {
            DemoTracksSheet { track in
                showingDemoTracks = false
                // Check if already analyzed - load directly instead of re-analyzing
                if let existing = history.entries.first(where: { $0.name == track.title }) {
                    Task {
                        await model.loadSession(analysisId: existing.id)
                    }
                } else {
                    queue.enqueueDemoImport(
                        baseURL: model.backendBaseURL,
                        trackId: track.id,
                        title: track.title
                    )
                    model.view = .bandRoom
                }
            }
        }
        .task {
            // Both were one-shots: engine status pinned "Offline"
            // across a backend restart, and songs uploaded from the
            // PHONE never appeared until app relaunch. Poll while the
            // view lives (status every 10s, history every 30s).
            var tick = 0
            while !Task.isCancelled {
                await intake.refreshEngineStatus(baseURL: model.backendBaseURL)
                if tick % 3 == 0 {
                    await history.refresh(baseURL: model.backendBaseURL)
                }
                tick += 1
                try? await Task.sleep(nanoseconds: 10_000_000_000)
            }
        }
    }

    // MARK: - Helpers

    private var engineOnline: Bool {
        intake.engineStatus?.online ?? true
    }

    private var isLocalBackend: Bool {
        let host = model.backendBaseURL.host ?? ""
        return host == "127.0.0.1" || host == "localhost"
    }

    // MARK: - Flow starts

    private func startURLAnalysis() {
        let trimmed = sourceUrl.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        queue.enqueueURL(baseURL: model.backendBaseURL, sourceUrl: trimmed)
        sourceUrl = ""
        model.view = .bandRoom
    }

    private func startUpload(_ fileURL: URL) {
        queue.enqueueUpload(
            baseURL: model.backendBaseURL,
            fileURL: fileURL,
            filename: fileURL.lastPathComponent,
            attested: intake.attested
        )
        model.view = .bandRoom
    }

}
