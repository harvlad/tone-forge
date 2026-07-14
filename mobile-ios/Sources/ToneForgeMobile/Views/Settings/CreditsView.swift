// CreditsView.swift
//
// Aggregate attribution screen (D-024), reached from Settings →
// Legal & compliance → "Credits & licenses". Two sections:
//
//   Songs        — history entries with a non-empty `license`
//                  (curated CC tracks; user-owned imports carry no
//                  license and are deliberately absent)
//   Sample packs — license + provenance strings from each installed
//                  pack manifest (bundled starter + cached curated)
//
// Read-only; the per-song credit line on the Now Playing header stays
// the primary attribution surface, this is the roll-up.

import SwiftUI
import ToneForgeEngine

struct CreditsView: View {
    @EnvironmentObject private var appState: AppState

    @State private var licensedSongs: [HistoryEntry] = []
    @State private var isLoadingSongs = true
    @State private var songsError: String?

    var body: some View {
        List {
            songsSection
            packsSection
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .background(TFTheme.background)
        .navigationTitle("Credits & licenses")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .task { await loadSongs() }
    }

    // MARK: - Songs

    private var songsSection: some View {
        Section {
            if isLoadingSongs {
                HStack {
                    ProgressView()
                    Text("Loading…").foregroundStyle(.secondary)
                }
            } else if let songsError {
                Text(songsError).font(.caption).foregroundStyle(.red)
            } else if licensedSongs.isEmpty {
                Text("No licensed songs yet. Demo tracks you import will be credited here.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(licensedSongs) { entry in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(entry.name ?? "Untitled")
                            .font(.body)
                        Text(songSubtitle(entry))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        } header: {
            Text("Songs")
        } footer: {
            Text("Songs you import yourself aren't listed — only licensed demo tracks need credit.")
        }
    }

    private func songSubtitle(_ entry: HistoryEntry) -> String {
        var parts: [String] = []
        if let artist = entry.artist, !artist.isEmpty { parts.append(artist) }
        if let license = entry.license, !license.isEmpty { parts.append(license) }
        return parts.joined(separator: " · ")
    }

    /// History rows with a non-empty license (curated CC tracks).
    private func loadSongs() async {
        isLoadingSongs = true
        songsError = nil
        defer { isLoadingSongs = false }
        do {
            let client = HistoryClient(timeout: AppConfig.historyTimeout)
            let entries = try await client.fetch(
                baseURL: appState.backendBaseURL, limit: 200
            )
            licensedSongs = entries.filter { !($0.license ?? "").isEmpty }
        } catch {
            songsError = error.localizedDescription
        }
    }

    // MARK: - Sample packs

    /// License/provenance rows for every installed pack manifest —
    /// the bundled starter plus any cached curated packs. Loaded
    /// synchronously: manifests are ~1 KB local JSON files.
    private var installedPacks: [SamplePack] {
        guard let bank = appState.sampleBank else { return [] }
        var packs: [SamplePack] = []
        if let starter = try? bank.loadBundled(packId: "starter") {
            packs.append(starter.pack)
        }
        for packId in bank.listCachedPackIds() {
            guard let resolved = try? bank.loadCached(packId: packId) else { continue }
            packs.append(resolved.pack)
        }
        return packs
    }

    private var packsSection: some View {
        Section {
            let packs = installedPacks
            if packs.isEmpty {
                Text("No sample packs installed.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(packs, id: \.packId) { pack in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(pack.name)
                            .font(.body)
                        if let license = pack.license, !license.isEmpty {
                            Text(license)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        if let provenance = pack.provenance, !provenance.isEmpty {
                            Text(provenance)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        } header: {
            Text("Sample packs")
        }
    }
}
