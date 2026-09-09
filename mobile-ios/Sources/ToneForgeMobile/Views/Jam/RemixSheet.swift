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
                borrowSection
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
            // "Applied: …" confirmation, pinned so it never scrolls out of
            // view. Every transform here lands on a surface that may be
            // silent right now (paused mix, idle sequencer) — field reports
            // read that as "remix did nothing" — so success is stated, not
            // inferred from the audio. Cleared when the next transform starts.
            .safeAreaInset(edge: .bottom) {
                if let msg = appState.remixApplied {
                    Text(msg)
                        .font(.footnote)
                        .foregroundStyle(TFTheme.accent)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        .background(.regularMaterial)
                        .accessibilityAddTraits(.updatesFrequently)
                }
            }
        }
        .task { await loadCandidates() }
        .task { await loadBorrowCandidates() }
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

    // MARK: - Borrow (real loops from other songs)

    @State private var borrowStem = "drums"
    @State private var borrowCandidates: [BorrowCandidate] = []
    @State private var borrowLoaded = false

    private var borrowSection: some View {
        Section {
            Picker("Borrow", selection: $borrowStem) {
                Text("Beat").tag("drums")
                Text("Bass").tag("bass")
                Text("Chords").tag("other")
                // Melody = the vocals stem's harmonic-matched topline —
                // parity with the web kit.js picker and the Launchpad
                // Add-Song sheet.
                Text("Melody").tag("vocals")
            }
            .pickerStyle(.segmented)
            .onChange(of: borrowStem) { _ in
                borrowLoaded = false
                Task { await loadBorrowCandidates() }
            }
            if !borrowLoaded {
                HStack {
                    ProgressView().controlSize(.small)
                    Text("Finding compatible loops…")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            } else if borrowCandidates.isEmpty {
                Text(borrowStem == "drums"
                     ? "Analyze more songs to borrow beats."
                     : "No harmonically compatible songs yet — analyze more.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(borrowCandidates.prefix(6)) { c in
                Button {
                    appState.loadBorrowLoops(donorId: c.entryId, stem: borrowStem)
                } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(c.name).lineLimit(1)
                            Text(borrowStem == "drums"
                                 ? "\(Int(c.tempo)) bpm"
                                 : "\(c.key ?? "?") · \(Int(c.tempo)) bpm")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if appState.borrowBusyDonor == c.entryId {
                            HStack(spacing: 6) {
                                ProgressView().controlSize(.small)
                                Text("Rendering…").font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        } else if borrowStem != "drums", c.harmonic >= 0.9 {
                            Text("harmonizes").font(.caption2)
                                .foregroundStyle(TFTheme.accent)
                        } else if borrowStem != "drums", c.harmonic >= 0.75 {
                            Text("fits").font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .disabled(appState.borrowBusyDonor != nil)
            }
        } header: {
            Text("Borrow")
        } footer: {
            Text("Real loops from songs that harmonize with this one (by chord content, not just key). This song's sections fill the top pads (blue), the borrowed song's the bottom (amber) — jump between sections of either.")
        }
    }

    private func loadBorrowCandidates() async {
        borrowCandidates = await appState.fetchBorrowCandidates(stem: borrowStem)
        borrowLoaded = true
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
                          detail: c.classes.count >= 3 ? "full kit" : nil,
                          donorName: c.name)
            }
        } header: {
            Text("Re-Drum")
        } footer: {
            Text("Keep this song's groove, play it on another song's drums — in the mix AND on your pads. First use per kit renders on the server; give it a few seconds.")
        }
    }

    private func redrumRow(title: String, kit: String,
                           detail: String? = nil,
                           donorName: String? = nil) -> some View {
        Button {
            appState.applyRedrum(kit: kit, donorName: donorName)
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
        guard let analysisId = appState.currentBundle?.analysisId else {
            // Silent return here = a dead button; say why nothing happened.
            appState.remixError = "No song loaded."
            return
        }
        packDownloading = true
        appState.remixError = nil
        appState.remixApplied = nil
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
                // The zip only lives in tmp until shared — without this
                // line the row silently morphs and the download reads as
                // "nothing happened".
                appState.remixApplied =
                    "Instrument Pack ready — tap Save Instrument Pack to save or share the .sfz zip."
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
