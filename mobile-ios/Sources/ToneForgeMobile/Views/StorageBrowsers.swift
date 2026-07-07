// StorageBrowsers.swift
//
// Settings → Storage (P7): one browser per on-device collection —
//
//   Samples   Documents/samples   PadSampleStore (mic/vocoder/baked)
//   Sessions  Documents/sessions  SessionStore (event JSON, no audio)
//   Bounces   Documents/bounces   BounceStore (rendered WAV/M4A)
//
// Each browser lists rows newest-first with per-row delete plus a
// confirmed "delete all". Sessions are also actionable from here —
// this is their only shelf: replay toggles through
// `AppState.toggleSessionReplay`, and the row menu offline-bounces
// to WAV/M4A and hands the file to the share sheet (spinner +
// disabled actions while `bouncingSessionIds` contains the row).

import SwiftUI
import ToneForgeEngine

// MARK: - Settings section

/// The "Storage" section of SettingsView. Split into its own view
/// so the nested stores are `@ObservedObject`-observed (the parent
/// only observes AppState) and the counts/sizes stay live.
struct StorageSection: View {
    @EnvironmentObject private var appState: AppState
    @ObservedObject var sampleStore: PadSampleStore
    @ObservedObject var bounceStore: BounceStore

    var body: some View {
        Section {
            NavigationLink {
                SamplesBrowserView(store: sampleStore)
            } label: {
                LabeledContent(
                    "Samples",
                    value: summary(
                        count: sampleStore.samples.count,
                        bytes: sampleStore.totalBytes()
                    )
                )
            }
            .accessibilityIdentifier("settings-storage-samples-link")

            NavigationLink {
                SessionsBrowserView()
            } label: {
                LabeledContent(
                    "Sessions",
                    value: "\(appState.savedSessions.count)"
                )
            }
            .accessibilityIdentifier("settings-storage-sessions-link")

            NavigationLink {
                BouncesBrowserView(store: bounceStore)
            } label: {
                LabeledContent(
                    "Bounces",
                    value: summary(
                        count: bounceStore.bounces.count,
                        bytes: bounceStore.totalBytes()
                    )
                )
            }
            .accessibilityIdentifier("settings-storage-bounces-link")
        } header: {
            Text("Storage")
        } footer: {
            Text("Everything here lives on this device only. Mic and vocoder audio is never uploaded.")
        }
    }

    private func summary(count: Int, bytes: Int64) -> String {
        count == 0 ? "0" : "\(count) · \(byteString(bytes))"
    }
}

// MARK: - Samples

/// Browser over locally-captured pad samples. Deletion routes
/// through `ModeCoordinator.deleteLocalSample` so any pad bound to
/// the sample is unassigned first.
struct SamplesBrowserView: View {
    @EnvironmentObject private var appState: AppState
    @ObservedObject var store: PadSampleStore
    @State private var showDeleteAllConfirm = false

    var body: some View {
        List {
            if store.samples.isEmpty {
                Text("No samples yet. Capture one with the mic or vocoder on the Play tab.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                Section {
                    ForEach(store.samples, id: \.id) { meta in
                        sampleRow(meta)
                            .swipeActions(edge: .trailing) {
                                Button("Delete", role: .destructive) {
                                    appState.modeCoordinator
                                        .deleteLocalSample(id: meta.id)
                                }
                            }
                    }
                } footer: {
                    Text("\(store.samples.count) sample\(store.samples.count == 1 ? "" : "s") · \(byteString(store.totalBytes())) · stored on this device only")
                }
            }
        }
        .navigationTitle("Samples")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .toolbar { deleteAllToolbarItem }
        .confirmationDialog(
            "Delete all samples?",
            isPresented: $showDeleteAllConfirm,
            titleVisibility: .visible
        ) {
            Button("Delete all samples", role: .destructive) {
                appState.deleteAllLocalSamples()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Removes every mic, vocoder and baked sample from this device and clears the pads they were assigned to. This can't be undone.")
        }
        .onAppear { store.reload() }
    }

    private var deleteAllToolbarItem: some ToolbarContent {
        ToolbarItem(placement: .primaryAction) {
            Button(role: .destructive) {
                showDeleteAllConfirm = true
            } label: {
                Label("Delete All", systemImage: "trash")
            }
            .disabled(store.samples.isEmpty)
            .accessibilityIdentifier("storage-samples-delete-all")
        }
    }

    private func sampleRow(_ meta: PadSampleMetadata) -> some View {
        HStack(spacing: 12) {
            Image(systemName: sourceIcon(meta.source))
                .foregroundStyle(tint(meta.colorHint))
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(classLabel(meta.effectiveClass))
                    .font(.body)
                Text(subtitle(meta))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func subtitle(_ meta: PadSampleMetadata) -> String {
        [
            sourceLabel(meta.source),
            String(format: "%.1f s", meta.durationSec),
            meta.createdAt.formatted(date: .abbreviated, time: .shortened),
        ].joined(separator: " · ")
    }

    private func sourceIcon(_ source: PadSampleMetadata.Source) -> String {
        switch source {
        case .mic:      return "mic.fill"
        case .vocoded:  return "waveform"
        case .songChop: return "music.note"
        }
    }

    private func sourceLabel(_ source: PadSampleMetadata.Source) -> String {
        switch source {
        case .mic:      return "Mic"
        case .vocoded:  return "Vocoder"
        case .songChop: return "Song chop"
        }
    }

    private func classLabel(_ sampleClass: SampleClass) -> String {
        switch sampleClass {
        case .vocalChop:     return "Vocal chop"
        case .percussion:    return "Percussion"
        case .sustainedNote: return "Sustained note"
        case .texture:       return "Texture"
        case .phrase:        return "Phrase"
        case .speechWord:    return "Speech"
        case .unknown:       return "Sample"
        }
    }

    /// Grid tint hex (0xRRGGBB) → Color; matches the pad colouring.
    private func tint(_ hex: UInt32) -> Color {
        Color(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}

// MARK: - Sessions

/// Browser + shelf for saved session captures. Rows replay through
/// the transport and bounce offline to shareable audio files.
struct SessionsBrowserView: View {
    @EnvironmentObject private var appState: AppState
    @StateObject private var attestation = AttestationStore()
    @State private var showDeleteAllConfirm = false
    /// URL of a just-rendered bounce for the share sheet (arrives
    /// asynchronously, hence `.sheet(item:)` + ActivityShareSheet).
    @State private var bounceShareItem: ShareFileItem? = nil

    var body: some View {
        List {
            if appState.savedSessions.isEmpty {
                Text("No sessions yet. Hit Record on the Play tab to capture a take.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                Section {
                    ForEach(appState.savedSessions, id: \.sessionId) { session in
                        sessionRow(session)
                    }
                } footer: {
                    Text("\(appState.savedSessions.count) session\(appState.savedSessions.count == 1 ? "" : "s") · event data only, no audio. Bounce a session to get an audio file.")
                }
            }
        }
        .navigationTitle("Sessions")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .toolbar { deleteAllToolbarItem }
        .confirmationDialog(
            "Delete all sessions?",
            isPresented: $showDeleteAllConfirm,
            titleVisibility: .visible
        ) {
            Button("Delete all sessions", role: .destructive) {
                appState.deleteAllSessions()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Removes every recorded take from this device. Bounced audio files are kept. This can't be undone.")
        }
        #if canImport(UIKit)
        .sheet(item: $bounceShareItem) { item in
            ActivityShareSheet(activityItems: [item.url])
        }
        #endif
    }

    private var deleteAllToolbarItem: some ToolbarContent {
        ToolbarItem(placement: .primaryAction) {
            Button(role: .destructive) {
                showDeleteAllConfirm = true
            } label: {
                Label("Delete All", systemImage: "trash")
            }
            .disabled(appState.savedSessions.isEmpty)
            .accessibilityIdentifier("storage-sessions-delete-all")
        }
    }

    @ViewBuilder
    private func sessionRow(_ session: SessionCapture) -> some View {
        let isReplaying = appState.replayingSessionId == session.sessionId
        let isBouncing = appState.bouncingSessionIds
            .contains(session.sessionId)
        HStack(spacing: 12) {
            Button {
                appState.toggleSessionReplay(sessionId: session.sessionId)
            } label: {
                Image(systemName: isReplaying
                    ? "pause.circle.fill" : "play.circle.fill")
                    .font(.title2)
                    .foregroundStyle(
                        isReplaying ? Color.accentColor : .secondary)
            }
            .buttonStyle(.borderless)

            VStack(alignment: .leading, spacing: 2) {
                Text(session.capturedAt.formatted(
                    date: .abbreviated, time: .shortened))
                    .font(.body)
                Text(subtitle(for: session))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Menu {
                bounceMenuItems(for: session, isBouncing: isBouncing)
                Button("Delete", role: .destructive) {
                    appState.deleteSession(sessionId: session.sessionId)
                }
            } label: {
                if isBouncing {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "ellipsis.circle")
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    /// Bounce actions. WAV is the bit-identical render; M4A is the
    /// share-friendly AAC. "Include original song" appears only when
    /// the session's song is the loaded one (its stems are what's
    /// cached) AND the ownership attestation was accepted — the
    /// renderer re-checks and throws regardless.
    @ViewBuilder
    private func bounceMenuItems(
        for session: SessionCapture, isBouncing: Bool
    ) -> some View {
        Button {
            bounce(session, format: .wav)
        } label: {
            Label(isBouncing ? "Bouncing…" : "Bounce to WAV",
                  systemImage: "waveform")
        }
        .disabled(isBouncing)

        Button {
            bounce(session, format: .m4aAAC256)
        } label: {
            Label("Bounce to M4A", systemImage: "waveform.badge.plus")
        }
        .disabled(isBouncing)

        if session.songBackendId != nil,
           session.songBackendId == appState.currentBundle?.analysisId,
           attestation.isAccepted {
            Button {
                bounce(session, format: .wav, includeSong: true)
            } label: {
                Label("Bounce with song (WAV)", systemImage: "music.note")
            }
            .disabled(isBouncing)
        }
    }

    private func bounce(
        _ session: SessionCapture,
        format: BounceFormat,
        includeSong: Bool = false
    ) {
        Task {
            if let url = await appState.bounceSession(
                sessionId: session.sessionId,
                includeOriginalSong: includeSong,
                format: format
            ) {
                bounceShareItem = ShareFileItem(url: url)
            }
        }
    }

    private func subtitle(for session: SessionCapture) -> String {
        var parts: [String] = [
            session.appMode.displayName,
            "\(session.events.count) event\(session.events.count == 1 ? "" : "s")",
            durationString(session.durationSec),
        ]
        parts.append(session.songBackendId == nil ? "Sketch" : "Song")
        if let bpm = session.tempoBpm {
            parts.append("\(Int(bpm)) BPM")
        }
        return parts.joined(separator: " · ")
    }
}

// MARK: - Bounces

/// Browser over rendered bounce files. These are plain audio files —
/// rows share via ShareLink (URL exists up front) and delete via
/// swipe or the confirmed delete-all.
struct BouncesBrowserView: View {
    @ObservedObject var store: BounceStore
    @State private var showDeleteAllConfirm = false

    var body: some View {
        List {
            if store.bounces.isEmpty {
                Text("No bounces yet. Bounce a session from the Sessions browser to render one.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                Section {
                    ForEach(store.bounces) { file in
                        bounceRow(file)
                            .swipeActions(edge: .trailing) {
                                Button("Delete", role: .destructive) {
                                    store.delete(url: file.url)
                                }
                            }
                    }
                } footer: {
                    Text("\(store.bounces.count) file\(store.bounces.count == 1 ? "" : "s") · \(byteString(store.totalBytes()))")
                }
            }
        }
        .navigationTitle("Bounces")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .toolbar { deleteAllToolbarItem }
        .confirmationDialog(
            "Delete all bounces?",
            isPresented: $showDeleteAllConfirm,
            titleVisibility: .visible
        ) {
            Button("Delete all bounces", role: .destructive) {
                store.deleteAll()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Removes every rendered audio file from this device. Their sessions are kept and can be bounced again. This can't be undone.")
        }
        .onAppear { store.reload() }
    }

    private var deleteAllToolbarItem: some ToolbarContent {
        ToolbarItem(placement: .primaryAction) {
            Button(role: .destructive) {
                showDeleteAllConfirm = true
            } label: {
                Label("Delete All", systemImage: "trash")
            }
            .disabled(store.bounces.isEmpty)
            .accessibilityIdentifier("storage-bounces-delete-all")
        }
    }

    private func bounceRow(_ file: BounceStore.BounceFile) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "waveform")
                .foregroundStyle(.secondary)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(file.name)
                    .font(.body)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text("\(file.createdAt.formatted(date: .abbreviated, time: .shortened)) · \(byteString(file.bytes))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            ShareLink(item: file.url, preview: SharePreview(file.name)) {
                Image(systemName: "square.and.arrow.up")
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.borderless)
        }
    }
}

// MARK: - Shared formatting

private let byteFormatter: ByteCountFormatter = {
    let formatter = ByteCountFormatter()
    formatter.countStyle = .file
    return formatter
}()

private func byteString(_ bytes: Int64) -> String {
    byteFormatter.string(fromByteCount: bytes)
}

private func durationString(_ seconds: Double) -> String {
    let clamped = max(0, seconds)
    let minutes = Int(clamped) / 60
    let secs = Int(clamped) % 60
    return String(format: "%d:%02d", minutes, secs)
}
