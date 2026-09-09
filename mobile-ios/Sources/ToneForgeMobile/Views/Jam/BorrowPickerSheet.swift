// BorrowPickerSheet.swift
//
// "Add from another song" — the DJ cross-song sampling picker, promoted
// to a first-class action ON THE LAUNCHPAD surface (the Jam .samples pad
// view). Mirrors the web kit.js "+ Add from another song" picker: a Part
// selector (Beat / Bass / Chords / Melody) plus a candidate song list
// (name + key/tempo + a key-match hint), and picking a donor loads its
// real, tempo/key-matched loops onto the pads via the exact same mount
// path the Remix-sheet Borrow row uses (AppState.loadBorrowLoops →
// RemixClient.fetchBorrowPack). Nothing here re-implements pad triggering;
// it only sources a new SamplePack onto the existing Launchpad.
//
//   Beat   → stem "drums"  (tempo-matched)
//   Bass   → stem "bass"   (harmonic-matched)
//   Chords → stem "other"  (harmonic-matched)
//   Melody → stem "vocals" (harmonic-matched toplines) — NEW, parity with web
//
// Degrades gracefully: a stem with no compatible donors shows a one-line
// "analyze more songs" hint; a server error surfaces on appState.remixError.

import SwiftUI
import ToneForgeEngine

struct BorrowPickerSheet: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    /// The stem this Part maps to. "drums" is rhythmic (tempo only); the
    /// rest are melodic (server ranks by chord content, so we show key and
    /// a harmonize/fits hint). "vocals" = Melody, harmonic-matched toplines.
    @State private var stem = "drums"
    @State private var candidates: [BorrowCandidate] = []
    @State private var loaded = false

    private var isRhythmic: Bool { stem == "drums" }

    var body: some View {
        NavigationStack {
            List {
                // Optional Session key/BPM target — OFF by default. When on,
                // the candidate list and the borrowed loops conform to the
                // target instead of this song; the primary song is never
                // retimed/repitched. Changing it re-scopes the candidates.
                SessionTargetControls { reloadCandidates() }
                partSection
                candidateSection
                if let err = appState.remixError {
                    Section {
                        Text(err).font(.footnote).foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Add from another song")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
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
        .task { await load() }
    }

    // MARK: - Part selector (Beat / Bass / Chords / Melody)

    private var partSection: some View {
        Section {
            Picker("Part", selection: $stem) {
                Text("Beat").tag("drums")
                Text("Bass").tag("bass")
                Text("Chords").tag("other")
                Text("Melody").tag("vocals")
            }
            .pickerStyle(.segmented)
            .onChange(of: stem) { _ in
                loaded = false
                Task { await load() }
            }
        } footer: {
            Text("Real loops from your other analyzed songs, locked to this song's tempo. Beat matches by tempo; Bass, Chords and Melody match by chord content (not just key). This song's sections fill the top pads, the borrowed song's the bottom — jump between either.")
        }
    }

    // MARK: - Candidate song list

    private var candidateSection: some View {
        Section {
            if !loaded {
                HStack {
                    ProgressView().controlSize(.small)
                    Text("Finding compatible loops…")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            } else if candidates.isEmpty {
                Text(isRhythmic
                     ? "Analyze more songs to borrow beats."
                     : "No harmonically compatible songs yet — analyze more.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(candidates.prefix(8)) { c in
                candidateRow(c)
            }
        } header: {
            Text(loaded && !candidates.isEmpty ? "Songs" : "")
        }
    }

    private func candidateRow(_ c: BorrowCandidate) -> some View {
        Button {
            // Same mount path as the Remix-sheet Borrow row: renders the
            // donor's loops server-side and activates the resulting
            // SamplePack on the Launchpad. Sheet stays open so the
            // "Rendering…" state is visible; "Applied: …" confirms.
            appState.loadBorrowLoops(donorId: c.entryId, stem: stem)
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(c.name).lineLimit(1)
                    Text(isRhythmic
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
                } else if !isRhythmic, c.harmonic >= 0.9 {
                    Text("harmonizes").font(.caption2)
                        .foregroundStyle(TFTheme.accent)
                } else if !isRhythmic, c.harmonic >= 0.75 {
                    Text("fits").font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .disabled(appState.borrowBusyDonor != nil)
    }

    // MARK: - Data

    private func load() async {
        candidates = await appState.fetchBorrowCandidates(stem: stem)
        loaded = true
    }

    /// Re-fetch when the Session target changes: candidates are ranked against
    /// the target (or the host when off), so the list must re-scope.
    private func reloadCandidates() {
        loaded = false
        Task { await load() }
    }
}

// MARK: - Session key/BPM target controls
//
// The opt-in Session cluster, promoted onto the Launchpad's Borrow surface
// (parity with web kit.js buildSessionControls). OFF by default; when on it
// reveals a Key picker (12 roots × maj/min → "G minor") and a BPM field, and
// on FIRST enable prefills both from the loaded song so opting in changes
// nothing until the user retunes. The target is persisted in AppState
// (UserDefaults) and only ever conforms ADDED (borrowed) parts — the primary
// song is never repitched/retimed. `onChange` re-scopes the candidate list.
private struct SessionTargetControls: View {
    @EnvironmentObject private var appState: AppState
    /// Called after any commit that should re-rank the candidate list.
    var onChange: () -> Void

    @State private var root = "C"
    @State private var quality: SessionKey.Quality = .major
    @State private var bpmText = ""

    var body: some View {
        Section {
            Toggle(isOn: sessionOnBinding) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Session").font(.subheadline.weight(.semibold))
                    Text("Optional: conform ADDED parts to a key/tempo.")
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
            .tint(TFTheme.accent)

            if appState.sessionTargetOn {
                HStack {
                    Text("Key").font(.footnote).foregroundStyle(.secondary)
                        .frame(width: 40, alignment: .leading)
                    Picker("Key root", selection: $root) {
                        ForEach(SessionKey.roots, id: \.self) { Text($0).tag($0) }
                    }
                    .pickerStyle(.menu)
                    Picker("Key quality", selection: $quality) {
                        Text("maj").tag(SessionKey.Quality.major)
                        Text("min").tag(SessionKey.Quality.minor)
                    }
                    .pickerStyle(.segmented)
                    .frame(maxWidth: 130)
                }
                HStack {
                    Text("BPM").font(.footnote).foregroundStyle(.secondary)
                        .frame(width: 40, alignment: .leading)
                    TextField("120", text: $bpmText)
                        .keyboardType(.numberPad)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 90)
                    Spacer()
                }
            }
        } footer: {
            Text(appState.sessionTargetOn
                 ? "Added parts conform to this key/tempo. Your song plays true."
                 : "Off — every song plays true. Turn on to conform added parts to a session key/tempo.")
        }
        .onChange(of: root) { _ in commitKey() }
        .onChange(of: quality) { _ in commitKey() }
        .onChange(of: bpmText) { _ in commitBpm() }
        .onChange(of: appState.sessionTargetOn) { on in
            // First enable: prefill blanks from the loaded song so nothing
            // conforms away from what's playing yet, then mirror into widgets.
            if on { appState.prefillSessionTargetFromSong() }
            seedFromState()
            onChange()
        }
        .onAppear { seedFromState() }
    }

    private var sessionOnBinding: Binding<Bool> {
        Binding(get: { appState.sessionTargetOn },
                set: { appState.sessionTargetOn = $0 })
    }

    /// Mirror the persisted target into the local widget state.
    private func seedFromState() {
        let p = SessionKey.parse(appState.sessionTargetKey)
        root = p.root
        quality = p.quality
        bpmText = appState.sessionTargetBpm > 0
            ? String(appState.sessionTargetBpm) : ""
    }

    private func commitKey() {
        let newKey = SessionKey.format(root: root, quality: quality)
        guard newKey != appState.sessionTargetKey else { return }
        appState.sessionTargetKey = newKey
        if appState.sessionTargetOn { onChange() }
    }

    private func commitBpm() {
        // Persist per keystroke, but don't re-fetch candidates on every digit
        // (chatty). The new BPM is picked up on the next open / on the actual
        // borrow render — matching web, which reads the target at fetch time.
        let v = Int(bpmText.filter(\.isNumber)) ?? 0
        guard v != appState.sessionTargetBpm else { return }
        appState.sessionTargetBpm = v
    }
}
