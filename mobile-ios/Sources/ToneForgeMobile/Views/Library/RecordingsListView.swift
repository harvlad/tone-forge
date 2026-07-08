// RecordingsListView.swift
//
// The Library tab's Recordings segment (D-022) — saved layers for
// the loaded song plus song-less sketches. Extracted from the
// deleted ProfileView. Two sections: "Song Layers" lists every saved
// LayerTimeline for the current song (`AppState.savedLayers`);
// "Sketches" lists song-less takes under the `__sketch__` sentinel
// (`AppState.savedSketchLayers`) — a sketch is a take recorded on
// the Contribute tab with no song loaded. Both share the row UI
// (play toggle, rename, share, delete); upload + m4a export are
// song-only.

import SwiftUI
import ToneForgeEngine
#if canImport(UIKit)
import UIKit
#endif

struct RecordingsListView: View {
    @EnvironmentObject private var appState: AppState

    @State private var renamingLayerId: String? = nil
    @State private var renameText: String = ""
    /// Wraps the URL of the just-rendered m4a so `.sheet(item:)` can
    /// present a UIActivityViewController with the exported file.
    @State private var m4aShareItem: ShareFileItem? = nil

    var body: some View {
        List {
            layersSection
            sketchLayersSection
        }
        #if os(iOS)
        .listStyle(.insetGrouped)
        #endif
        #if canImport(UIKit)
        .sheet(item: $m4aShareItem) { item in
            ActivityShareSheet(activityItems: [item.url])
        }
        #endif
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
                Image(systemName: isActive ? "pause.circle.fill" : "play.circle.fill")
                    .font(.title2)
                    .foregroundStyle(isActive ? Color.accentColor : .secondary)
            }
            .buttonStyle(.borderless)

            VStack(alignment: .leading, spacing: 2) {
                if renamingLayerId == layer.layerId {
                    TextField("Layer name", text: $renameText)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { commitRename(layer, isSketch: isSketch) }
                } else {
                    Text(layer.name)
                        .font(.body)
                }
                Text(subtitle(for: layer))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

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
                    Image(systemName: "ellipsis.circle")
                        .foregroundStyle(.secondary)
                }
            }
        }
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
