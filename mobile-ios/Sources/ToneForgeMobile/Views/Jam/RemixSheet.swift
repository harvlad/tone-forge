// RemixSheet.swift
//
// The one slide-up home for every one-click transform — "sampling like
// DJs used to", automated. Replaces scattering more chips/buttons across
// an already-dense Jam surface: one Remix entry point, one sheet.
//
//   Kits      : Auto Kit / Drum Kit / Flip (kind= variants of /kit)
//   Feel      : Humanize — the song's own micro-timing on running sequences
//   Re-Drum   : this groove on another song's drums (server-ranked donors)
//   Export    : Instrument Pack (.sfz zip) via share sheet
//
// All rows degrade gracefully: server 422s surface as a one-line error,
// nothing here blocks jamming.

import SwiftUI
import ToneForgeEngine

struct RemixSheet: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var candidates: [RedrumCandidate] = []
    @State private var candidatesLoaded = false
    @State private var packFileURL: URL?
    @State private var packDownloading = false

    var body: some View {
        NavigationStack {
            List {
                kitsSection
                feelSection
                redrumSection
                exportSection
                if let err = appState.remixError ?? appState.autoKitError {
                    Section {
                        Text(err).font(.footnote).foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Remix")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .task { await loadCandidates() }
    }

    // MARK: - Kits

    private var activePackId: String {
        appState.activeSamplePack?.pack.packId ?? ""
    }

    private func kitRow(_ title: String, icon: String, kind: String,
                        prefix: String, subtitle: String) -> some View {
        Button {
            appState.loadAutoKit(kind: kind)
        } label: {
            HStack {
                Label(title, systemImage: icon)
                Spacer()
                // Spinner ONLY on the row that's loading — a shared flag
                // painted every row busy (field-reported confusion).
                if appState.autoKitLoading && appState.lastKitKind == kind {
                    ProgressView().controlSize(.small)
                } else if activePackId.hasPrefix(prefix) {
                    Image(systemName: "checkmark")
                        .foregroundStyle(TFTheme.accent)
                }
            }
        }
        .disabled(appState.currentBundle == nil || appState.autoKitLoading)
        .listRowSubtitle(subtitle)
    }

    private var kitsSection: some View {
        Section("Pads") {
            kitRow("Auto Kit", icon: "wand.and.stars", kind: "auto",
                   prefix: "auto-",
                   subtitle: "The song's best loops, color-coded")
            kitRow("Drum Kit", icon: "circle.grid.3x3.fill", kind: "drums",
                   prefix: "drumkit-",
                   subtitle: "Its kick, snare and hats as clean one-shots")
            kitRow("Flip", icon: "shuffle", kind: "flip",
                   prefix: "flip-",
                   subtitle: "A new beat built from the song's own DNA")
        }
    }

    // MARK: - Feel

    private var feelSection: some View {
        Section("Feel") {
            Button {
                appState.toggleHumanize()
            } label: {
                HStack {
                    Label("Humanize", systemImage: "waveform.path")
                    Spacer()
                    if appState.remixBusy == "humanize" {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: appState.remixHumanizeOn
                              ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(appState.remixHumanizeOn
                                             ? TFTheme.accent : .secondary)
                    }
                }
            }
            .disabled(appState.currentBundle == nil
                      || appState.remixBusy == "humanize")
            .listRowSubtitle("Sequences swing with this song's own timing")
        }
    }

    // MARK: - Re-Drum

    private var redrumSection: some View {
        Section {
            if appState.redrumActiveKit != nil {
                Button {
                    appState.clearRedrum()
                } label: {
                    HStack {
                        Label("Original drums",
                              systemImage: "arrow.uturn.backward")
                        Spacer()
                        if appState.redrumBusyKit == "original" {
                            ProgressView().controlSize(.small)
                        }
                    }
                }
                .disabled(appState.redrumBusyKit != nil)
            }
            redrumRow(title: "Tightened (own kit)", kit: "self")
            if !candidatesLoaded {
                HStack {
                    ProgressView().controlSize(.small)
                    Text("Finding kit donors…")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            } else if candidates.isEmpty {
                Text("Analyze more songs to unlock cross-song kits.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(candidates.prefix(6)) { c in
                redrumRow(title: c.name, kit: "song:\(c.entryId)",
                          detail: c.classes.count >= 3 ? "full kit" : nil)
            }
        } header: {
            Text("Re-Drum")
        } footer: {
            Text("Keep this song's groove, play it on another song's drums. First use per kit renders on the server — give it a few seconds.")
        }
    }

    private func redrumRow(title: String, kit: String,
                           detail: String? = nil) -> some View {
        Button {
            appState.applyRedrum(kit: kit)
        } label: {
            HStack {
                Label(title, systemImage: "arrow.triangle.2.circlepath")
                    .lineLimit(1)
                Spacer()
                if appState.redrumBusyKit == kit {
                    // First use per kit renders server-side (~10-60 s) and
                    // downloads a full stem — say so instead of a bare
                    // spinner the user can misread as a hang.
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text("Rendering…")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                } else if let detail, appState.redrumBusyKit == nil {
                    Text(detail).font(.caption2).foregroundStyle(.secondary)
                }
                if appState.redrumBusyKit != kit,
                   appState.redrumActiveKit == kit {
                    Image(systemName: "checkmark")
                        .foregroundStyle(TFTheme.accent)
                }
            }
        }
        .disabled(appState.currentBundle == nil
                  || appState.redrumBusyKit != nil)
    }

    // MARK: - Export

    private var exportSection: some View {
        Section {
            if let url = packFileURL {
                ShareLink(item: url) {
                    Label("Save Instrument Pack", systemImage: "square.and.arrow.up")
                }
            } else {
                Button {
                    downloadInstrumentPack()
                } label: {
                    HStack {
                        Label("Instrument Pack", systemImage: "pianokeys")
                        Spacer()
                        if packDownloading { ProgressView().controlSize(.small) }
                    }
                }
                .disabled(appState.currentBundle == nil || packDownloading)
            }
        } footer: {
            Text("The song as a playable sampler patch (.sfz): drums on keys, bass and chord stab playable chromatically.")
        }
    }

    // MARK: - Data

    private func loadCandidates() async {
        candidates = await appState.fetchRedrumCandidates()
        candidatesLoaded = true
    }

    private func downloadInstrumentPack() {
        guard let analysisId = appState.currentBundle?.analysisId else { return }
        packDownloading = true
        appState.remixError = nil
        let base = appState.backendBaseURL
        Task { @MainActor in
            defer { packDownloading = false }
            do {
                let url = base.appendingPathComponent("api/song")
                    .appendingPathComponent(analysisId)
                    .appendingPathComponent("instrument-pack")
                var request = URLRequest(url: url)
                AuthContext.shared.apply(to: &request)
                let (data, response) = try await URLSession.shared.data(for: request)
                if let http = response as? HTTPURLResponse,
                   !(200..<300).contains(http.statusCode) {
                    throw RemixClientError.httpStatus(http.statusCode)
                }
                let dest = FileManager.default.temporaryDirectory
                    .appendingPathComponent("Instrument-\(analysisId.prefix(8)).zip")
                try data.write(to: dest, options: .atomic)
                packFileURL = dest
            } catch {
                appState.remixError = error.localizedDescription
            }
        }
    }
}

// MARK: - Row subtitle helper

private extension View {
    /// Second-line caption under a list row's label, matching the sheet's
    /// quiet explanatory tone without fighting List's layout.
    func listRowSubtitle(_ text: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            self
            Text(text).font(.caption2).foregroundStyle(.secondary)
        }
    }
}
