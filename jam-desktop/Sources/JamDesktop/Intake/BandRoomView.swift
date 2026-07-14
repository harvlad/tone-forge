// BandRoomView.swift
//
// The analysis queue: one card per import (URL, upload, demo track).
// Nothing here ever navigates on its own — a finished analysis waits
// with an explicit Open button so the user can keep jamming in Perform
// while more songs cook in the background.

import SwiftUI
import JamDesktopCore

struct BandRoomView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var queue: AnalysisQueueModel

    var body: some View {
        VStack(spacing: 16) {
            header

            if queue.items.isEmpty {
                emptyState
            } else {
                ScrollView {
                    VStack(spacing: 12) {
                        ForEach(queue.items) { item in
                            QueueJobCard(item: item)
                        }
                    }
                    .frame(maxWidth: 560)
                    .frame(maxWidth: .infinity)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
        .task {
            await queue.refreshFromServer(baseURL: model.backendBaseURL)
        }
    }

    private var header: some View {
        HStack {
            Text("Band Room")
                .font(.largeTitle.bold())
            Spacer()
            if queue.items.contains(where: { !$0.status.isActive }) {
                Button("Clear finished") {
                    queue.clearFinished()
                }
            }
        }
        .frame(maxWidth: 560)
    }

    private var emptyState: some View {
        VStack(spacing: 14) {
            ContentUnavailableView(
                "Nothing in progress",
                systemImage: "music.note.list",
                description: Text("Start a song from the Intake view.")
            )
            Button("Back to Intake") {
                model.view = .intake
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - Queue card

private struct QueueJobCard: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var queue: AnalysisQueueModel

    let item: AnalysisQueueItem

    /// Whether this card kicked off the current/most recent session
    /// load — keeps another card's failure from rendering here.
    @State private var openAttempted = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(item.title)
                    .font(.headline)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                Button("Dismiss") {
                    queue.dismiss(id: item.id)
                }
                .buttonStyle(.plain)
                .font(.caption)
                .foregroundStyle(.secondary)
            }

            statusBody
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .jamCard()
    }

    @ViewBuilder
    private var statusBody: some View {
        switch item.status {
        case let .queued(position):
            HStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text(position.map { "Waiting for engine — #\($0) in queue" }
                    ?? "Waiting for engine")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

        case let .running(message, percent):
            VStack(alignment: .leading, spacing: 6) {
                ProgressView(value: percent.map { $0 / 100 })
                    .progressViewStyle(.linear)
                HStack {
                    Text(message.isEmpty ? "Working…" : message)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Spacer()
                    if let percent {
                        Text("\(Int(percent.rounded()))%")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
            }

        case let .done(historyId):
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 12) {
                    Label("Ready to play", systemImage: "checkmark.circle.fill")
                        .font(.subheadline)
                        .foregroundStyle(.green)
                    Spacer()
                    Button("Open") {
                        openAttempted = true
                        Task {
                            // loadSession flips to .perform on success.
                            await model.loadSession(analysisId: historyId)
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.isLoadingSession)
                }
                if openAttempted, model.isLoadingSession {
                    ProgressView("Loading song…")
                        .controlSize(.small)
                } else if openAttempted, let error = model.sessionError {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(JamTheme.error)
                }
            }

        case let .error(message):
            Label(message, systemImage: "exclamationmark.triangle")
                .font(.subheadline)
                .foregroundStyle(JamTheme.error)
        }
    }
}
