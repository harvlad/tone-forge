// BrowsePacksSheet.swift
//
// The "Browse Packs" sheet reached from the Play tab's `PackPicker`.
// Sections are ordered by proximity to the current musical moment:
//
//   1. Song DNA  — virtual packs synthesised from `SongBundle.presets`
//                  (populated by `AppState.songDnaPacks`). Empty when
//                  no bundle is loaded. Tapping activates via
//                  `AppState.activateSongDnaPack`.
//   2. Bundled   — offline-first packs shipped with the app. Loaded
//                  via `SampleBank.loadBundled`. Currently just the
//                  StarterPack.
//   3. Curated   — remote catalog served by `/api/sample-packs`.
//                  Each row downloads on tap (streaming progress via
//                  `AppState.curatedDownloads`) and activates once
//                  fully cached. Cached packs re-activate instantly.
//
// The active pack is marked with a check. Tapping a row activates
// the pack + dismisses the sheet; the Play tab sees the change via
// `AppState.activeSamplePack` (ModeCoordinator rebinds the grid).

import SwiftUI
import ToneForgeEngine

struct BrowsePacksSheet: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                songDnaSection
                bundledSection
                curatedSection
            }
            #if os(iOS)
            .listStyle(.insetGrouped)
            #endif
            .navigationTitle("Browse Packs")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .task {
                // Auto-refresh on first appear so the Curated section
                // isn't empty. Subsequent opens are cheap: catalog is
                // small (~200 bytes/pack) and cache-friendly.
                await appState.refreshCuratedCatalog()
            }
        }
    }

    // MARK: - Song DNA

    @ViewBuilder
    private var songDnaSection: some View {
        Section {
            if appState.songDnaPacks.isEmpty {
                Text(appState.currentBundle == nil
                     ? "Load a song from Library to see Song DNA packs."
                     : "Downloading stems… Song DNA appears when the download finishes.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(appState.songDnaPacks) { entry in
                    Button {
                        appState.activateSongDnaPack(entry)
                        dismiss()
                    } label: {
                        packRow(
                            title: entry.displayName,
                            subtitle: "\(entry.chopCount) chop\(entry.chopCount == 1 ? "" : "s")",
                            isActive: appState.activeSamplePack?.pack.packId == entry.pack.pack.packId
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
        } header: {
            Text("Song DNA")
        } footer: {
            if !appState.songDnaPacks.isEmpty {
                Text("Chops sliced from this song's stems. Each pad plays a moment from the original.")
            }
        }
    }

    // MARK: - Bundled

    @ViewBuilder
    private var bundledSection: some View {
        Section {
            Button {
                activateBundled(packId: "starter", displayName: "Starter Pack")
                dismiss()
            } label: {
                packRow(
                    title: "Starter Pack",
                    subtitle: "16 hand-picked samples · offline",
                    isActive: appState.activeSamplePack?.pack.packId == "starter"
                )
            }
            .buttonStyle(.plain)
        } header: {
            Text("Bundled")
        }
    }

    // MARK: - Curated

    @ViewBuilder
    private var curatedSection: some View {
        Section {
            if let error = appState.curatedError {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(error)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        Button("Retry") {
                            Task { await appState.refreshCuratedCatalog() }
                        }
                        .font(.footnote)
                    }
                }
            } else if appState.curatedCatalog.isEmpty {
                Text("Loading catalog…")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(appState.curatedCatalog) { entry in
                    curatedRow(entry: entry)
                }
            }
        } header: {
            Text("Curated")
        } footer: {
            if !appState.curatedCatalog.isEmpty {
                Text("Downloaded packs stay available offline.")
            }
        }
    }

    @ViewBuilder
    private func curatedRow(entry: SamplePackCatalogEntry) -> some View {
        let progress = appState.curatedDownloads[entry.packId]
        let isCached = appState.cachedPackIds.contains(entry.packId)
        let isActive = appState.activeSamplePack?.pack.packId == entry.packId
        Button {
            handleCuratedTap(entry: entry, isCached: isCached)
        } label: {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(entry.name)
                        .font(.body)
                        .foregroundStyle(.primary)
                    Text(curatedSubtitle(entry: entry, progress: progress, isCached: isCached))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if let p = progress, !p.isComplete, p.padsTotal > 0 {
                        ProgressView(
                            value: Double(p.padsCompleted),
                            total: Double(p.padsTotal)
                        )
                        .progressViewStyle(.linear)
                        .padding(.top, 2)
                    }
                }
                Spacer()
                curatedAccessory(progress: progress, isCached: isCached, isActive: isActive)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(progress.map { !$0.isComplete } ?? false)
    }

    @ViewBuilder
    private func curatedAccessory(
        progress: PackDownloadProgress?,
        isCached: Bool,
        isActive: Bool
    ) -> some View {
        if let p = progress, !p.isComplete {
            ProgressView()
                .controlSize(.small)
        } else if isActive {
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(Color.accentColor)
        } else if isCached {
            Image(systemName: "arrow.down.circle.fill")
                .foregroundStyle(.green)
        } else {
            Image(systemName: "arrow.down.circle")
                .foregroundStyle(.secondary)
        }
    }

    private func curatedSubtitle(
        entry: SamplePackCatalogEntry,
        progress: PackDownloadProgress?,
        isCached: Bool
    ) -> String {
        if let p = progress, !p.isComplete {
            return "Downloading… \(p.padsCompleted)/\(p.padsTotal) pads"
        }
        var parts: [String] = ["\(entry.padCount) pad\(entry.padCount == 1 ? "" : "s")"]
        if !entry.tags.isEmpty {
            parts.append(entry.tags.prefix(3).joined(separator: " · "))
        }
        if isCached { parts.append("offline") }
        return parts.joined(separator: " · ")
    }

    private func handleCuratedTap(entry: SamplePackCatalogEntry, isCached: Bool) {
        if isCached {
            appState.activateCuratedPack(packId: entry.packId)
            dismiss()
        } else {
            Task {
                await appState.downloadCuratedPack(entry)
                // Once the download completes, activate immediately.
                if appState.cachedPackIds.contains(entry.packId) {
                    appState.activateCuratedPack(packId: entry.packId)
                    dismiss()
                }
            }
        }
    }

    // MARK: - Row

    private func packRow(title: String, subtitle: String, isActive: Bool) -> some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.body)
                    .foregroundStyle(.primary)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if isActive {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(Color.accentColor)
            }
        }
        .contentShape(Rectangle())
    }

    // MARK: - Helpers

    private func activateBundled(packId: String, displayName: String) {
        guard let bank = appState.sampleBank else { return }
        do {
            let pack = try bank.loadBundled(packId: packId)
            appState.activateSamplePack(pack, stemFiles: [:])
        } catch {
            // Silently ignore — Samples panel will keep whatever was
            // active. Explicit error surfacing lives on AppState for
            // song-load failures; a missing bundled pack is a build
            // problem, not a user problem.
        }
    }
}
