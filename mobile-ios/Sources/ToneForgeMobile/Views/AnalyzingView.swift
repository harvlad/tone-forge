// AnalyzingView.swift
//
// Progress sheet for an in-flight import. Indeterminate while
// transcoding/loading, determinate with the backend's message while
// uploading, error + Retry on failure. Swipe-dismiss is disabled
// while work is in flight so an accidental swipe can't orphan an
// upload.

import SwiftUI

struct AnalyzingView: View {
    @ObservedObject var importer: ImportCoordinator

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                trackCard
                phaseContent
                Spacer()
            }
            .padding(.top, 32)
            .padding(.horizontal)
            .navigationTitle("Analysing")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(cancelLabel) { importer.dismiss() }
                }
            }
        }
        .interactiveDismissDisabled(isWorking)
        .accessibilityIdentifier("analyzing-view")
    }

    /// Swipe-dismiss stays disabled only while dismissing could lose
    /// local work (pre-submit). Once .analysing, the job is server-owned
    /// and closing the sheet is safe.
    private var isWorking: Bool {
        switch importer.phase {
        case .transcoding, .uploading, .loading: return true
        default: return false
        }
    }

    private var cancelLabel: String {
        switch importer.phase {
        case .done, .failed, .analysing: return "Close"
        default: return "Cancel"
        }
    }

    private var trackCard: some View {
        HStack(spacing: 12) {
            Image(systemName: "waveform")
                .font(.title)
                .foregroundStyle(.tint)
            VStack(alignment: .leading, spacing: 2) {
                Text(importer.trackTitle.isEmpty ? "Importing" : importer.trackTitle)
                    .font(.headline)
                    .lineLimit(2)
                Text("Analysing for JAM — stems, chords, chops")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding()
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    private var phaseContent: some View {
        switch importer.phase {
        case .idle, .awaitingAttestation:
            EmptyView()

        case .transcoding:
            VStack(spacing: 8) {
                ProgressView()
                Text("Preparing audio…")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

        case .uploading(let message, let percent):
            VStack(spacing: 8) {
                if let percent {
                    ProgressView(value: min(max(percent, 0), 100), total: 100)
                } else {
                    ProgressView()
                }
                Text(message)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }

        case .analysing(let message, let percent):
            VStack(spacing: 12) {
                // Upload captured — the file never needs re-sending.
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.icloud.fill")
                        .foregroundStyle(.green)
                    Text("Upload complete")
                        .font(.subheadline.weight(.semibold))
                }
                if let percent {
                    ProgressView(value: min(max(percent, 0), 100), total: 100)
                } else {
                    ProgressView()
                }
                Text(message)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                Text("Analysis runs on the server — you can close this and keep playing. The song appears in your Library when it's ready.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }

        case .loading:
            VStack(spacing: 8) {
                ProgressView()
                Text("Loading song…")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

        case .done:
            VStack(spacing: 8) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.largeTitle)
                    .foregroundStyle(.green)
                Text("Ready to play")
                    .font(.callout)
            }

        case .failed(let message):
            VStack(spacing: 12) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.largeTitle)
                    .foregroundStyle(.orange)
                Text(message)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                Button("Retry") { importer.retry() }
                    .buttonStyle(.borderedProminent)
            }
        }
    }
}
