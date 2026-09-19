// BorrowPickerSheet.swift
//
// "Add from another song" — the DJ cross-song sampling picker, promoted
// to a first-class action ON THE LAUNCHPAD surface (the Jam .samples pad
// view). Two donor SOURCES behind one Part selector:
//
//   • My songs — your analyzed library (AppState.fetchBorrowCandidates →
//     GET /api/song/{id}/borrow-candidates), picked → loadBorrowLoops.
//   • Crate    — the shared Vinyl Crate: a curated, read-only pool of
//     legally-clean CC-BY/CC0 tracks everyone can dig. Two views over one
//     CrateTrack set: "For your session" (fetchCrateCandidates → GET
//     /api/crate/candidates, session-ranked, with a Similar|Contrast genre
//     toggle) and "Browse crate" (searchCrate → GET /api/crate/search,
//     faceted: free-text + genre/mood chips + clean-export/has-vocals). A
//     pick calls loadCrateLoops → GET /api/crate/{id}/borrow, mounted onto
//     the pads through the SAME borrow layout as an owned song.
//
// Mirrors the web kit.js and jam-desktop BorrowPickerView crate pickers
// (platform parity). The required CC attribution (title — artist · CC
// license) rides EVERY crate row, and CC-BY-SA rows carry an "export-locked"
// badge — a CC-BY obligation. Nothing here re-implements pad triggering; it
// only sources a new SamplePack onto the existing Launchpad.
//
//   Beat   → stem "drums"  (tempo-matched)
//   Bass   → stem "bass"   (harmonic-matched)
//   Chords → stem "other"  (harmonic-matched)
//   Melody → stem "vocals" (harmonic-matched toplines)

import SwiftUI
import ToneForgeEngine

struct BorrowPickerSheet: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    private enum Source: String { case songs, crate }
    private enum CrateMode: String { case match, browse }

    /// The stem this Part maps to. "drums" is rhythmic (tempo only); the
    /// rest are melodic (server ranks by chord content, so we show key and
    /// a harmonize/fits hint). "vocals" = Melody, harmonic-matched toplines.
    @State private var stem = "drums"
    @State private var source: Source = .songs

    // Own-song borrow state.
    @State private var candidates: [BorrowCandidate] = []
    @State private var loaded = false

    // Crate state.
    @State private var crateMode: CrateMode = .match
    @State private var genreMode: CrateGenreMode = .similar
    @State private var crateCandidates: [CrateCandidate] = []
    @State private var crateTracks: [CrateTrack] = []
    @State private var crateFacetCounts: [String: [String: Int]] = [:]
    @State private var crateLoaded = false
    @State private var searchText = ""
    @State private var facetGenre: String?
    @State private var facetMood: String?
    @State private var cleanExportOnly = false
    @State private var vocalsOnly = false
    @State private var searchDebounce: Task<Void, Never>?

    private var isRhythmic: Bool { stem == "drums" }

    var body: some View {
        NavigationStack {
            List {
                sourceSection
                SessionTargetControls { reloadForSessionChange() }
                partSection
                if source == .songs {
                    candidateSection
                } else {
                    crateModeSection
                    if crateMode == .match { crateMatchSection }
                    else { crateBrowseControls; crateBrowseSection }
                }
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

    // MARK: - Source selector (My songs / Crate)

    private var sourceSection: some View {
        Section {
            Picker("Source", selection: $source) {
                Text("My songs").tag(Source.songs)
                Text("Crate").tag(Source.crate)
            }
            .pickerStyle(.segmented)
            .onChange(of: source) { _ in
                Task { await load() }
            }
        } footer: {
            Text(source == .songs
                 ? "Borrow from your own analyzed songs."
                 : "Dig the Vinyl Crate — a shared pool of legally-clean CC-BY/CC0 tracks. Every crate track shows its required credit.")
        }
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
                Task { await load() }
            }
        } footer: {
            Text(partFooter)
        }
    }

    private var partFooter: String {
        if source == .crate {
            return "Beat matches by tempo; Bass, Chords and Melody match by chord content. The crate track's loops mount below your song."
        }
        // Blank canvas (no host song): loops arrive donor-only at their own
        // tempo — the host-lock copy would be a lie.
        return appState.currentBundle == nil
            ? "Real loops from your analyzed songs, played at their own tempo. Pick a song to mount its kit on the pads — set a Session key/BPM above to conform everything to one target."
            : "Real loops from your other analyzed songs, locked to this song's tempo. Beat matches by tempo; Bass, Chords and Melody match by chord content (not just key). This song's sections fill the top pads, the borrowed song's the bottom — jump between either."
    }

    // MARK: - Own-song candidate list

    private var candidateSection: some View {
        Section {
            if !loaded {
                loadingRow("Finding compatible loops…")
            } else if candidates.isEmpty {
                Text(isRhythmic
                     ? "Analyze more songs to borrow beats."
                     : "No harmonically compatible songs yet — analyze more.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(candidates.prefix(8)) { c in
                songRow(c)
            }
        } header: {
            Text(loaded && !candidates.isEmpty ? "Songs" : "")
        }
    }

    private func songRow(_ c: BorrowCandidate) -> some View {
        Button {
            appState.loadBorrowLoops(donorId: c.entryId, stem: stem)
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(c.name).lineLimit(1)
                    if c.tempo > 0 {
                        Text(isRhythmic
                             ? "\(Int(c.tempo)) bpm"
                             : "\(c.key ?? "?") · \(Int(c.tempo)) bpm")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                if appState.borrowBusyDonor == c.entryId {
                    renderingBadge
                } else if !isRhythmic, c.harmonic >= 0.9 {
                    Text("harmonizes").font(.caption2)
                        .foregroundStyle(TFTheme.accent)
                } else if !isRhythmic, c.harmonic >= 0.75 {
                    Text("fits").font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
        .disabled(appState.borrowBusyDonor != nil)
    }

    // MARK: - Crate mode selector

    private var crateModeSection: some View {
        Section {
            Picker("Crate view", selection: $crateMode) {
                Text("For your session").tag(CrateMode.match)
                Text("Browse crate").tag(CrateMode.browse)
            }
            .pickerStyle(.segmented)
            .onChange(of: crateMode) { _ in Task { await loadCrate() } }
        }
    }

    // MARK: - Crate: session match

    private var crateMatchSection: some View {
        Group {
            Section {
                Picker("Genre affinity", selection: $genreMode) {
                    Text("Similar").tag(CrateGenreMode.similar)
                    Text("Contrast").tag(CrateGenreMode.contrast)
                }
                .pickerStyle(.segmented)
                .onChange(of: genreMode) { _ in Task { await loadCrate() } }
            } footer: {
                Text(genreMode == .similar
                     ? "Ranked closest to your session's tempo, key and genre."
                     : "Ranked to CONTRAST your session's genre — a curveball that still fits the groove.")
            }
            Section {
                if !crateLoaded {
                    loadingRow("Digging the crate…")
                } else if crateCandidates.isEmpty {
                    Text("The Vinyl Crate isn't available yet — try Browse, or analyze a song first.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                ForEach(crateCandidates.prefix(24)) { c in
                    crateRow(CrateRowData(candidate: c))
                }
            } header: {
                Text(crateLoaded && !crateCandidates.isEmpty ? "For your session" : "")
            }
        }
    }

    // MARK: - Crate: faceted browse

    private var crateBrowseControls: some View {
        Section {
            HStack {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(.secondary)
                TextField("Search title, artist, tags…", text: $searchText)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .onChange(of: searchText) { _ in scheduleSearch() }
            }
            facetChipRow(title: "Genre", facet: "genre", selection: $facetGenre)
            facetChipRow(title: "Mood", facet: "mood", selection: $facetMood)
            Toggle("Clean export only (no CC-BY-SA)", isOn: $cleanExportOnly)
                .font(.footnote)
                .onChange(of: cleanExportOnly) { _ in Task { await loadCrate() } }
            Toggle("Has vocals", isOn: $vocalsOnly)
                .font(.footnote)
                .onChange(of: vocalsOnly) { _ in Task { await loadCrate() } }
        } header: {
            Text("Browse the crate")
        }
    }

    /// A single-select chip row for a facet, populated from the response's
    /// live facet counts (value · N), sorted by count. Tapping toggles the
    /// filter and re-runs the search (facets AND across categories).
    private func facetChipRow(
        title: String, facet: String, selection: Binding<String?>
    ) -> some View {
        let counts = crateFacetCounts[facet] ?? [:]
        let values = counts.sorted { $0.value > $1.value }.prefix(8)
        return Group {
            if !values.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(Array(values), id: \.key) { pair in
                            let on = selection.wrappedValue == pair.key
                            Button {
                                selection.wrappedValue = on ? nil : pair.key
                                Task { await loadCrate() }
                            } label: {
                                Text("\(pair.key) · \(pair.value)")
                                    .font(.caption2)
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 5)
                                    .background(on ? TFTheme.accent.opacity(0.25)
                                                   : Color.secondary.opacity(0.12))
                                    .clipShape(Capsule())
                                    .overlay(Capsule().stroke(
                                        on ? TFTheme.accent : .clear, lineWidth: 1))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.vertical, 2)
                }
            } else {
                EmptyView()
            }
        }
    }

    private var crateBrowseSection: some View {
        Section {
            if !crateLoaded {
                loadingRow("Digging the crate…")
            } else if crateTracks.isEmpty {
                Text("Nothing in the crate matches — clear a filter, or the crate isn't seeded yet.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(crateTracks.prefix(40)) { t in
                crateRow(CrateRowData(track: t))
            }
        } header: {
            Text(crateLoaded && !crateTracks.isEmpty ? "Crate" : "")
        }
    }

    // MARK: - Shared crate row (attribution REQUIRED on every row)

    private func crateRow(_ d: CrateRowData) -> some View {
        Button {
            appState.loadCrateLoops(
                trackId: d.id, stem: stem, trackName: d.title,
                attribution: d.attribution)
        } label: {
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline) {
                    Text(d.title.isEmpty ? d.id : d.title)
                        .lineLimit(1)
                        .foregroundStyle(d.renderable ? .primary : .secondary)
                    Spacer()
                    if appState.borrowBusyDonor == d.id {
                        renderingBadge
                    } else if let hint = d.matchHint(isRhythmic: isRhythmic) {
                        Text(hint).font(.caption2)
                            .foregroundStyle(hint == "harmonizes"
                                             ? TFTheme.accent : .secondary)
                    }
                }
                // Tempo / key / Camelot line.
                if let meta = d.metaLine(isRhythmic: isRhythmic) {
                    Text(meta).font(.caption2).foregroundStyle(.secondary)
                }
                // REQUIRED CC attribution — the on-screen compliance artifact.
                HStack(spacing: 6) {
                    Text(d.attribution)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                    if d.exportEncumbered {
                        Text("export-locked")
                            .font(.system(size: 9, weight: .semibold))
                            .padding(.horizontal, 5).padding(.vertical, 2)
                            .background(Color.orange.opacity(0.2))
                            .foregroundStyle(.orange)
                            .clipShape(Capsule())
                    }
                }
                if !d.renderable {
                    Text("No stored loops yet — can't mount this one.")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
        }
        .disabled(appState.borrowBusyDonor != nil || !d.renderable)
    }

    // MARK: - Shared bits

    private var renderingBadge: some View {
        HStack(spacing: 6) {
            ProgressView().controlSize(.small)
            Text("Rendering…").font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func loadingRow(_ label: String) -> some View {
        HStack {
            ProgressView().controlSize(.small)
            Text(label).font(.footnote).foregroundStyle(.secondary)
        }
    }

    // MARK: - Data

    private func load() async {
        if source == .songs {
            candidates = await appState.fetchBorrowCandidates(stem: stem)
            loaded = true
        } else {
            await loadCrate()
        }
    }

    private func loadCrate() async {
        crateLoaded = false
        let facets = currentFacets()
        if crateMode == .match {
            crateCandidates = await appState.fetchCrateCandidates(
                stem: stem, genreMode: genreMode, facets: facets)
        } else if let resp = await appState.searchCrate(facets: facets) {
            crateTracks = resp.tracks
            crateFacetCounts = resp.facetCounts
        } else {
            crateTracks = []
            crateFacetCounts = [:]
        }
        crateLoaded = true
    }

    /// The current facet filter — shared by candidates (pre-rank) and search.
    private func currentFacets() -> CrateFacetQuery {
        CrateFacetQuery(
            text: crateMode == .browse ? searchText : "",
            genre: facetGenre, mood: facetMood,
            hasVocals: vocalsOnly ? true : nil,
            cleanExportOnly: cleanExportOnly)
    }

    /// Debounce free-text search so we don't fire a request per keystroke.
    private func scheduleSearch() {
        searchDebounce?.cancel()
        searchDebounce = Task {
            try? await Task.sleep(nanoseconds: 350_000_000)
            if Task.isCancelled { return }
            await loadCrate()
        }
    }

    /// Re-fetch when the Session target changes: candidates rank against the
    /// target (or the host when off), so the active list must re-scope.
    private func reloadForSessionChange() {
        Task { await load() }
    }
}

// MARK: - Crate row view-model (candidate OR track → one row shape)

/// Flattens a `CrateCandidate` (session-match) or a `CrateTrack` (browse) into
/// the fields the row renders — the Swift twin of web `normalizeCrateTrack`.
/// A candidate is always renderable (it came out of the ranker); a browse track
/// is renderable only when its stored analysis carries a graph.
private struct CrateRowData: Identifiable {
    let id: String
    let title: String
    let artist: String
    let attribution: String
    let licenseId: String
    let exportEncumbered: Bool
    let tempo: Double
    let key: String?
    let camelot: String
    let matchScore: Double?
    let harmonic: Double?
    let renderable: Bool

    init(candidate c: CrateCandidate) {
        id = c.trackId
        title = c.name
        artist = c.artist
        licenseId = c.licenseId ?? ""
        exportEncumbered = c.exportEncumbered
        tempo = c.tempo
        key = c.key
        camelot = c.camelot
        matchScore = c.matchScore
        harmonic = c.harmonic
        renderable = true
        attribution = Self.credit(
            wire: c.attribution, title: c.name, artist: c.artist,
            licenseId: c.licenseId ?? "")
    }

    init(track t: CrateTrack) {
        id = t.id
        title = t.title
        artist = t.artist
        licenseId = t.license.licenseId
        exportEncumbered = t.exportEncumbered
        tempo = t.tempo
        key = t.key
        camelot = t.camelot
        matchScore = nil
        harmonic = nil
        renderable = t.graphAvailable
        attribution = Self.credit(
            wire: t.attribution, title: t.title, artist: t.artist,
            licenseId: t.license.licenseId)
    }

    /// Prefer the backend's ready-to-display (legally-vetted) attribution;
    /// else compose "Title — Artist · CC-BY 4.0". Twin of web crateAttribution.
    private static func credit(
        wire: String, title: String, artist: String, licenseId: String
    ) -> String {
        if !wire.isEmpty { return wire }
        var base = title.isEmpty ? "Untitled" : title
        if !artist.isEmpty { base += " — \(artist)" }
        let label = CrateLicenseKind(id: licenseId).label(rawId: licenseId)
        return label.isEmpty ? base : "\(base) · \(label)"
    }

    /// The "harmonizes"/"fits" hint (melodic parts only) — parity with the
    /// own-song row's harmonic thresholds.
    func matchHint(isRhythmic: Bool) -> String? {
        guard !isRhythmic, let h = harmonic else { return nil }
        if h >= 0.9 { return "harmonizes" }
        if h >= 0.75 { return "fits" }
        return nil
    }

    /// Tempo / key / Camelot metadata line ("G minor · 8A · 118 bpm").
    func metaLine(isRhythmic: Bool) -> String? {
        var parts: [String] = []
        if !isRhythmic, let k = key, !k.isEmpty {
            parts.append(camelot.isEmpty ? k : "\(k) · \(camelot)")
        }
        if tempo > 0 { parts.append("\(Int(tempo)) bpm") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
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
