// RecordingsListView.swift
//
// The Library tab's Recordings segment (D-022). Three sections:
//   "Audio Recordings" — actual m4a captures of the session's output
//     cut by the bottom transport's Record button (`AppState
//     .savedAudioTakes`). These are SOUND, not events; they play back
//     on a plain AVAudioPlayer, independent of any song.
//   "Song Layers" — saved LayerTimelines (replayable events) for the
//     current song (`AppState.savedLayers`).
//   "Sketches" — song-less event takes under the `__sketch__` sentinel
//     (`AppState.savedSketchLayers`).
// The two event sections share the layer row UI (play toggle, rename,
// share, delete); upload + m4a export are song-only.

import SwiftUI
import AVFoundation
import ToneForgeEngine
#if canImport(UIKit)
import UIKit
#endif

struct RecordingsListView: View {
    @EnvironmentObject private var appState: AppState

    @State private var renamingLayerId: String? = nil
    @State private var renameText: String = ""
    /// Inline-rename target for an audio take (separate id space from
    /// the layer rows above).
    @State private var renamingTakeId: UUID? = nil
    @State private var takeRenameText: String = ""
    /// Plays back recorded audio takes off a simple AVAudioPlayer —
    /// takes are finished files, so they need no engine graph.
    @StateObject private var takePlayer = AudioTakePlayer()
    /// Wraps the URL of the just-rendered m4a so `.sheet(item:)` can
    /// present a UIActivityViewController with the exported file.
    @State private var m4aShareItem: ShareFileItem? = nil

    var body: some View {
        List {
            audioTakesSection
            layersSection
            sketchLayersSection
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(TFTheme.background)
        .onDisappear { takePlayer.stop() }
        #if canImport(UIKit)
        .sheet(item: $m4aShareItem) { item in
            ActivityShareSheet(activityItems: [item.url])
        }
        #endif
    }

    // MARK: - Audio takes section

    @ViewBuilder
    private var audioTakesSection: some View {
        Section {
            if appState.savedAudioTakes.isEmpty {
                Text("No recordings yet. Hit Record on the transport bar to capture what you hear — song, pads and effects together.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(appState.savedAudioTakes) { take in
                    audioTakeRow(take)
                        .tfLibraryRowChrome()
                }
            }
        } header: {
            Text("Audio Recordings")
        } footer: {
            if !appState.savedAudioTakes.isEmpty {
                Text("Recorded straight off the session's audio output. Play them back here, or share the .m4a.")
            }
        }
    }

    @ViewBuilder
    private func audioTakeRow(_ take: AudioTake) -> some View {
        let isPlaying = takePlayer.playingId == take.id
        HStack(spacing: 12) {
            Button {
                takePlayer.toggle(take)
            } label: {
                RoundedRectangle(cornerRadius: 10)
                    .fill(tileGradient(for: take.id.uuidString))
                    .frame(width: 52, height: 52)
                    .overlay(
                        Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                            .font(.title3)
                            .foregroundStyle(.white)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(TFTheme.stroke, lineWidth: 1)
                    )
            }
            .buttonStyle(.borderless)

            VStack(alignment: .leading, spacing: 2) {
                if renamingTakeId == take.id {
                    TextField("Recording name", text: $takeRenameText)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { commitTakeRename(take) }
                } else {
                    Text(take.title)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(TFTheme.textPrimary)
                        .lineLimit(1)
                }
                Text(takeSubtitle(take))
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            Menu {
                Button(renamingTakeId == take.id ? "Save name" : "Rename") {
                    if renamingTakeId == take.id {
                        commitTakeRename(take)
                    } else {
                        renamingTakeId = take.id
                        takeRenameText = take.title
                    }
                }
                #if canImport(UIKit)
                Button {
                    m4aShareItem = ShareFileItem(url: take.fileURL)
                } label: {
                    Label("Share", systemImage: "square.and.arrow.up")
                }
                #endif
                Button("Delete", role: .destructive) {
                    if isPlaying { takePlayer.stop() }
                    appState.deleteAudioTake(id: take.id)
                    if renamingTakeId == take.id { renamingTakeId = nil }
                }
            } label: {
                Image(systemName: "ellipsis")
                    .rotationEffect(.degrees(90))
                    .foregroundStyle(TFTheme.textSecondary)
                    .frame(width: 30, height: 30)
                    .contentShape(Rectangle())
            }
        }
        .tfLibraryCard(active: isPlaying)
        .contentShape(Rectangle())
    }

    private func takeSubtitle(_ take: AudioTake) -> String {
        var parts = [formatDuration(take.durationSec)]
        parts.append(Self.takeDateFormatter.string(from: take.createdAt))
        return parts.joined(separator: " · ")
    }

    private static let takeDateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .short
        return f
    }()

    private func commitTakeRename(_ take: AudioTake) {
        let trimmed = takeRenameText.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty {
            appState.renameAudioTake(id: take.id, to: trimmed)
        }
        renamingTakeId = nil
        takeRenameText = ""
    }

    // MARK: - Layers section

    @ViewBuilder
    private var layersSection: some View {
        Section {
            if appState.currentBundle == nil {
                Text("Load a song to see saved layers.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else if appState.savedLayers.isEmpty {
                Text("No layers yet. Hit Record on the Contribute tab to capture a performance.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(appState.savedLayers, id: \.layerId) { layer in
                    layerRow(layer)
                        .tfLibraryRowChrome()
                }
            }
        } header: {
            Text("Song Layers")
        } footer: {
            if !appState.savedLayers.isEmpty {
                Text("Toggle a layer to hear it play back over the song. Layers persist across launches.")
            }
        }
    }

    /// Song-less takes recorded on the Contribute tab with no song
    /// loaded (sentinel `__sketch__`). Always visible — sketches
    /// aren't tied to whatever bundle happens to be active.
    @ViewBuilder
    private var sketchLayersSection: some View {
        Section {
            if appState.savedSketchLayers.isEmpty {
                Text("No sketches yet. Eject the song and hit Record on the Contribute tab to capture one.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(appState.savedSketchLayers, id: \.layerId) { layer in
                    layerRow(layer, isSketch: true)
                        .tfLibraryRowChrome()
                }
            }
        } header: {
            Text("Sketches")
        } footer: {
            if !appState.savedSketchLayers.isEmpty {
                Text("Takes recorded on the Contribute tab with no song loaded. They replay over the metronome grid; playing one while a song is loaded silences the song's stems.")
            }
        }
    }

    @ViewBuilder
    private func layerRow(_ layer: LayerTimeline, isSketch: Bool = false) -> some View {
        let isActive = appState.activePlaybackLayerIds.contains(layer.layerId)
        HStack(spacing: 12) {
            Button {
                if isSketch {
                    appState.toggleSketchLayerPlayback(layerId: layer.layerId)
                } else {
                    appState.toggleLayerPlayback(layerId: layer.layerId)
                }
            } label: {
                RoundedRectangle(cornerRadius: 10)
                    .fill(tileGradient(for: layer.layerId))
                    .frame(width: 52, height: 52)
                    .overlay(
                        Image(systemName: isActive ? "pause.fill" : "play.fill")
                            .font(.title3)
                            .foregroundStyle(.white)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(TFTheme.stroke, lineWidth: 1)
                    )
            }
            .buttonStyle(.borderless)

            VStack(alignment: .leading, spacing: 2) {
                if renamingLayerId == layer.layerId {
                    TextField("Layer name", text: $renameText)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { commitRename(layer, isSketch: isSketch) }
                } else {
                    Text(layer.name)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(TFTheme.textPrimary)
                        .lineLimit(1)
                }
                Text(subtitle(for: layer))
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            Menu {
                Button(renamingLayerId == layer.layerId ? "Save name" : "Rename") {
                    if renamingLayerId == layer.layerId {
                        commitRename(layer, isSketch: isSketch)
                    } else {
                        renamingLayerId = layer.layerId
                        renameText = layer.name
                    }
                }
                if !isSketch {
                    // Upload + m4a render are song-coupled (backend
                    // keys on analysisId; offline render needs the
                    // song's stem context) — sketch rows omit them.
                    uploadMenuItem(for: layer)
                }
                shareMenuItem(for: layer)
                if !isSketch {
                    exportM4AMenuItem(for: layer)
                }
                Button("Delete", role: .destructive) {
                    if isSketch {
                        appState.deleteSketchLayer(layerId: layer.layerId)
                    } else {
                        appState.deleteLayer(layerId: layer.layerId)
                    }
                    if renamingLayerId == layer.layerId {
                        renamingLayerId = nil
                    }
                }
            } label: {
                if appState.uploadingLayerIds.contains(layer.layerId)
                    || appState.exportingLayerIds.contains(layer.layerId) {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "ellipsis")
                        .rotationEffect(.degrees(90))
                        .foregroundStyle(TFTheme.textSecondary)
                        .frame(width: 30, height: 30)
                        .contentShape(Rectangle())
                }
            }
        }
        .tfLibraryCard(active: isActive)
        .contentShape(Rectangle())
    }

    /// Push-to-backend menu row. Disabled while an upload for this
    /// layer is in flight; label swaps to "Uploaded" once the round
    /// trip succeeds so repeat taps read as a re-sync.
    @ViewBuilder
    private func uploadMenuItem(for layer: LayerTimeline) -> some View {
        let isUploading = appState.uploadingLayerIds.contains(layer.layerId)
        let isUploaded = appState.uploadedLayerIds.contains(layer.layerId)
        Button {
            Task { await appState.uploadLayer(layerId: layer.layerId) }
        } label: {
            if isUploading {
                Label("Uploading…", systemImage: "arrow.up.circle")
            } else if isUploaded {
                Label("Re-upload", systemImage: "checkmark.circle")
            } else {
                Label("Upload to backend", systemImage: "arrow.up.circle")
            }
        }
        .disabled(isUploading)
    }

    /// Offline-render this layer to .m4a and pop the standard iOS share
    /// sheet with the rendered file. Disabled while the render is in
    /// flight (`exportingLayerIds` includes this layer). Uses the
    /// active sample pack for pad audio.
    @ViewBuilder
    private func exportM4AMenuItem(for layer: LayerTimeline) -> some View {
        let isExporting = appState.exportingLayerIds.contains(layer.layerId)
        Button {
            Task {
                if let url = await appState.exportLayerToM4A(layerId: layer.layerId) {
                    m4aShareItem = ShareFileItem(url: url)
                }
            }
        } label: {
            if isExporting {
                Label("Rendering m4a…", systemImage: "waveform")
            } else {
                Label("Export as m4a", systemImage: "waveform")
            }
        }
        .disabled(isExporting)
    }

    /// Standard iOS share sheet — writes the timeline JSON to a temp
    /// file and hands the URL to `ShareLink`. Users can AirDrop it,
    /// mail it, save to Files, etc.
    @ViewBuilder
    private func shareMenuItem(for layer: LayerTimeline) -> some View {
        if let url = shareURL(for: layer) {
            ShareLink(item: url,
                      preview: SharePreview(layer.name)) {
                Label("Share", systemImage: "square.and.arrow.up")
            }
        }
    }

    /// Cached per-layer temp-file URL for `ShareLink`. Computed lazily
    /// on menu open — `ShareLink` needs the URL to exist at construction
    /// time, so we materialize the JSON up front.
    private func shareURL(for layer: LayerTimeline) -> URL? {
        appState.exportLayerToTempFile(layerId: layer.layerId)
    }

    private func subtitle(for layer: LayerTimeline) -> String {
        let events = layer.events.count
        let dur = formatDuration(layer.durationSec)
        var parts: [String] = ["\(events) event\(events == 1 ? "" : "s")", dur]
        // Sketch metadata (tempo grid the take was recorded against).
        if let bpm = layer.sketchTempoBpm {
            parts.append("\(Int(bpm)) BPM")
        }
        if let numerator = layer.sketchTimeSigNumerator {
            parts.append(SketchSettingsStore.timeSigLabel(numerator))
        }
        // Prefer the human-readable pack name captured at record time;
        // packIds like `song-derived:xyz` are a fallback.
        if let pack = layer.packName ?? layer.activePackId {
            parts.append(pack)
        }
        return parts.joined(separator: " · ")
    }

    /// Stable per-layer hue — hashes the layerId and spins the hue
    /// wheel, matching ArtworkView's art-less fallback so recordings
    /// get distinctive, consistent tiles.
    private func tileGradient(for id: String) -> LinearGradient {
        var seed = 0
        for scalar in id.unicodeScalars {
            seed = (seed &* 31) &+ Int(scalar.value)
        }
        let hue = Double(abs(seed) % 360) / 360.0
        return LinearGradient(
            colors: [
                Color(hue: hue, saturation: 0.55, brightness: 0.45),
                Color(hue: hue, saturation: 0.65, brightness: 0.20),
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    private func formatDuration(_ sec: Double) -> String {
        let clamped = max(0, sec)
        let m = Int(clamped) / 60
        let s = Int(clamped) % 60
        return String(format: "%d:%02d", m, s)
    }

    private func commitRename(_ layer: LayerTimeline, isSketch: Bool = false) {
        let trimmed = renameText.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty {
            if isSketch {
                appState.renameSketchLayer(layerId: layer.layerId, to: trimmed)
            } else {
                appState.renameLayer(layerId: layer.layerId, to: trimmed)
            }
        }
        renamingLayerId = nil
        renameText = ""
    }
}

/// Plays back recorded audio takes on a standalone AVAudioPlayer. Takes
/// are finished m4a files, so they play through the media services
/// without touching the live AVAudioEngine graph. `playingId` drives
/// the row's play/pause glyph; the delegate clears it when playback
/// runs out so the button doesn't stick on "pause".
@MainActor
final class AudioTakePlayer: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published private(set) var playingId: UUID?
    private var player: AVAudioPlayer?

    /// Play the take, or stop if it's the one already playing.
    func toggle(_ take: AudioTake) {
        if playingId == take.id {
            stop()
            return
        }
        stop()
        do {
            let p = try AVAudioPlayer(contentsOf: take.fileURL)
            p.delegate = self
            guard p.play() else { return }
            player = p
            playingId = take.id
        } catch {
            playingId = nil
        }
    }

    func stop() {
        player?.stop()
        player = nil
        playingId = nil
    }

    nonisolated func audioPlayerDidFinishPlaying(
        _ player: AVAudioPlayer, successfully flag: Bool
    ) {
        Task { @MainActor in
            self.player = nil
            self.playingId = nil
        }
    }
}
