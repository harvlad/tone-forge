// BorrowPickerView.swift
//
// "Add from another song" — the DJ cross-song sampling picker, promoted
// to a first-class action ON the Launchpad surface (it used to be buried
// in the Remix sheet's Borrow section). Pick a part (Beat / Bass / Chords
// / Melody), pick a donor, and its real loops — tempo-matched, and
// key-compatible for melodic parts — mount straight onto the Launchpad pads.
//
// TWO donor sources behind one part list (web/iOS parity):
//   • My songs — YOUR other analyzed songs (the original borrow pool).
//   • Crate    — the shared, curated CC-BY/CC0 "Vinyl Crate": a legally-clean
//                donor pool everyone digs. Two orthogonal views over it — a
//                "For your session" ranked strip (GET /api/crate/candidates)
//                and a "Browse the crate" faceted grid (GET /api/crate/search,
//                by genre / tempo / key / mood / tags) — both mount through the
//                SAME render + pad-mount as own-song borrow.
//
// Not a second engine: the part list drives the SAME SessionController borrow
// render (loadBorrowLoops / loadCrateLoops → applyBorrowPack), so ranking,
// download and pad-mount are shared; only the DONOR pool and the CC attribution
// differ. Attribution is shown on every crate row it appears on (CC-BY), and a
// CC-BY-SA track is badged "export-locked" (exportEncumbered).

import SwiftUI
import ToneForgeEngine
import JamDesktopCore

/// One selectable Borrow part. `stem` is the server's stem key; the title
/// is the musician-facing name (web parity: Beat/Bass/Chords/Melody).
struct BorrowPart: Identifiable, Equatable, Hashable {
    let title: String
    let stem: String
    var id: String { stem }

    /// Melodic parts get key + harmonic hints; drums are tempo-only.
    var isMelodic: Bool { stem != "drums" }

    static let all: [BorrowPart] = [
        .init(title: "Beat", stem: "drums"),
        .init(title: "Bass", stem: "bass"),
        .init(title: "Chords", stem: "other"),
        .init(title: "Melody", stem: "vocals"),
    ]
}

/// Which donor pool the picker is showing.
enum BorrowSource: String, CaseIterable, Identifiable {
    case mySongs
    case crate
    var id: String { rawValue }
    var title: String { self == .mySongs ? "My songs" : "Crate" }
}

struct BorrowPickerView: View {
    @EnvironmentObject private var session: SessionController
    @Environment(\.dismiss) private var dismiss

    @State private var source: BorrowSource = .mySongs
    @State private var part: BorrowPart = BorrowPart.all[0]

    // My-songs pool (original borrow).
    @State private var candidates: [BorrowCandidate] = []
    @State private var loaded = false

    // Crate pool — ranked strip.
    @State private var crateRanked: [CrateCandidate] = []
    @State private var crateRankedLoaded = false
    @State private var genreMode: CrateGenreMode = .similar

    // Crate pool — faceted browse.
    @State private var browse: CrateSearchResponse?
    @State private var browsing = false
    @State private var searchText = ""
    @State private var selectedGenre: String?
    @State private var selectedMood: String?
    @State private var cleanExportOnly = false
    @State private var nearTempo = false
    @State private var sessionKeyOnly = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            sourcePicker
            partPicker

            Group {
                switch source {
                case .mySongs: mySongsList
                case .crate: crateContent
                }
            }
            .frame(minHeight: 300)

            statusFooter
        }
        .frame(width: 460, height: 580)
        .background(JamTheme.background)
        .preferredColorScheme(.dark)
        .tint(JamTheme.accent)
        .task { await loadForCurrentSource() }
    }

    // MARK: - Chrome

    private var header: some View {
        HStack {
            Label("Add from another song", systemImage: "square.stack.3d.up")
                .font(.title3.weight(.semibold))
            Spacer()
            Button("Done") { dismiss() }
                .keyboardShortcut(.cancelAction)
        }
        .padding(16)
    }

    private var sourcePicker: some View {
        Picker("Source", selection: $source) {
            ForEach(BorrowSource.allCases) { s in Text(s.title).tag(s) }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .padding(.horizontal, 16)
        .padding(.top, 12)
        .onChange(of: source) { _ in
            Task { await loadForCurrentSource() }
        }
    }

    private var partPicker: some View {
        // Part selector — Beat / Bass / Chords / Melody (web parity). Drives
        // both pools; changing it reloads whichever source is showing.
        Picker("Part", selection: $part) {
            ForEach(BorrowPart.all) { p in Text(p.title).tag(p) }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .padding(.horizontal, 16)
        .padding(.top, 8)
        .onChange(of: part) { _ in
            loaded = false
            crateRankedLoaded = false
            Task { await loadForCurrentSource() }
        }
    }

    @ViewBuilder
    private var statusFooter: some View {
        if let msg = session.remixApplied {
            Divider()
            Text(msg)
                .font(.callout)
                .foregroundStyle(JamTheme.accent)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
        }
        if let err = session.remixError {
            Text(err)
                .font(.caption)
                .foregroundStyle(JamTheme.error)
                .padding(.horizontal, 16)
                .padding(.bottom, 8)
        }
    }

    // MARK: - My songs (original borrow)

    @ViewBuilder
    private var mySongsList: some View {
        List {
            Section {
                if !loaded {
                    loadingRow("Finding compatible loops…")
                } else if candidates.isEmpty {
                    Text(part.stem == "drums"
                         ? "Analyze more songs to borrow beats."
                         : "No harmonically compatible songs yet.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                ForEach(candidates) { c in mySongRow(c) }
            } header: {
                Text(part.isMelodic
                     ? "Songs whose \(part.title.lowercased()) fits this song's key"
                     : "Songs whose beat matches this tempo")
            }
        }
        .listStyle(.inset)
    }

    private func mySongRow(_ c: BorrowCandidate) -> some View {
        Button {
            Task {
                // Session ON → conform the borrowed loops to the session
                // key/tempo; OFF → both nil, so this is today's host-conform
                // load unchanged.
                let t = session.sessionTarget
                await session.loadBorrowLoops(
                    donorId: c.entryId, stem: part.stem, donorName: c.name,
                    targetBpm: t.targetBpm, targetKey: t.targetKey)
                if session.remixError == nil { dismiss() }
            }
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(c.name).lineLimit(1)
                    Text(part.isMelodic
                         ? "\(c.key ?? "?") · \(Int(c.tempo)) bpm"
                         : "\(Int(c.tempo)) bpm")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                Spacer()
                busyOrHint(donorId: c.entryId, harmonic: c.harmonic)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(session.borrowBusyDonor != nil)
    }

    // MARK: - Crate

    @ViewBuilder
    private var crateContent: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                rankedStrip
                Divider()
                browsePanel
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
    }

    // "For your session" — the weighted-match ranked strip.
    @ViewBuilder
    private var rankedStrip: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("For your session")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                // Genre affinity — a toggle, not a separate path (SIMILAR vs
                // CONTRAST). Re-ranks in place.
                Picker("Genre", selection: $genreMode) {
                    Text("Similar").tag(CrateGenreMode.similar)
                    Text("Contrast").tag(CrateGenreMode.contrast)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                .onChange(of: genreMode) { _ in
                    Task { await loadCrateRanked() }
                }
            }

            if !crateRankedLoaded {
                loadingRow("Digging the crate for your session…")
            } else if crateRanked.isEmpty {
                Text("No crate tracks match this session yet — try Browse below.")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                ForEach(crateRanked) { c in crateCandidateRow(c) }
            }
        }
    }

    private func crateCandidateRow(_ c: CrateCandidate) -> some View {
        Button {
            loadCrate(trackId: c.trackId, name: c.name,
                      attribution: c.attribution,
                      encumbered: c.exportEncumbered)
        } label: {
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(c.name).lineLimit(1)
                    Spacer()
                    busyOrHint(donorId: c.trackId, harmonic: c.harmonic)
                }
                Text(crateSubtitle(
                    key: c.key, tempo: c.tempo, genre: c.genre, mood: c.mood))
                    .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                attributionLine(c.attribution, encumbered: c.exportEncumbered)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(session.borrowBusyDonor != nil)
    }

    // "Browse the crate" — free text + facet chips + a faceted result list.
    @ViewBuilder
    private var browsePanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Browse the crate")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)

            HStack(spacing: 6) {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(.secondary).font(.caption)
                TextField("Search title, artist, tag, genre…", text: $searchText)
                    .textFieldStyle(.plain)
                    .onSubmit { Task { await loadBrowse() } }
                if !searchText.isEmpty {
                    Button {
                        searchText = ""
                        Task { await loadBrowse() }
                    } label: { Image(systemName: "xmark.circle.fill") }
                        .buttonStyle(.plain).foregroundStyle(.secondary)
                }
            }
            .padding(6)
            .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 6))

            filterToggles
            facetChips(facet: "genre", selected: $selectedGenre)
            facetChips(facet: "mood", selected: $selectedMood)

            if browsing {
                loadingRow("Searching the crate…")
            } else if let browse {
                if browse.tracks.isEmpty {
                    Text("No crate tracks match these filters.")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("\(browse.total) track\(browse.total == 1 ? "" : "s")")
                        .font(.caption2).foregroundStyle(.secondary)
                    ForEach(browse.tracks) { t in crateTrackRow(t) }
                }
            }
        }
    }

    private var filterToggles: some View {
        HStack(spacing: 10) {
            Toggle("CC0/BY only", isOn: $cleanExportOnly)
                .toggleStyle(.checkbox).font(.caption2)
                .help("Exclude ShareAlike (export-locked) tracks so an exported "
                      + "remix stays free of copyleft.")
                .onChange(of: cleanExportOnly) { _ in Task { await reloadCrate() } }
            Toggle("Near tempo", isOn: $nearTempo)
                .toggleStyle(.checkbox).font(.caption2)
                .disabled(sessionBpm == nil)
                .help("Only tracks within ±10% of this song's tempo.")
                .onChange(of: nearTempo) { _ in Task { await reloadCrate() } }
            Toggle("In key", isOn: $sessionKeyOnly)
                .toggleStyle(.checkbox).font(.caption2)
                .disabled(sessionKey == nil)
                .help("Only tracks in this song's key.")
                .onChange(of: sessionKeyOnly) { _ in Task { await reloadCrate() } }
            Spacer(minLength: 0)
        }
    }

    /// Facet chip row driven by the search response's per-facet counts. Single
    /// select (exact facet match); tapping the active chip clears it.
    @ViewBuilder
    private func facetChips(facet: String, selected: Binding<String?>) -> some View {
        let counts = browse?.facetCounts[facet] ?? [:]
        if !counts.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(counts.keys.sorted(), id: \.self) { key in
                        let on = selected.wrappedValue == key
                        Button {
                            selected.wrappedValue = on ? nil : key
                            Task { await reloadCrate() }
                        } label: {
                            Text("\(key) (\(counts[key] ?? 0))")
                                .font(.caption2)
                                .padding(.horizontal, 8).padding(.vertical, 3)
                                .background(
                                    on ? JamTheme.accent.opacity(0.85)
                                       : Color.white.opacity(0.08),
                                    in: Capsule())
                                .foregroundStyle(on ? Color.black : Color.primary)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    private func crateTrackRow(_ t: CrateTrack) -> some View {
        // A browse row can be mounted only if it has the selected part's stem
        // AND a stored graph (GPU-less prod renders 0 pads without it).
        let hasStem = t.availableStems.isEmpty
            || t.availableStems.contains(part.stem)
        let mountable = hasStem && t.graphAvailable
        return Button {
            loadCrate(trackId: t.id, name: t.title,
                      attribution: t.attribution, encumbered: t.exportEncumbered)
        } label: {
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(t.title).lineLimit(1)
                    Spacer()
                    if session.borrowBusyDonor == t.id {
                        ProgressView().controlSize(.small)
                    } else if !mountable {
                        Text(hasStem ? "no pads" : "no \(part.title.lowercased())")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
                Text(crateSubtitle(
                    key: t.key, tempo: t.tempo, genre: t.genre, mood: t.mood))
                    .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                attributionLine(t.attribution, encumbered: t.exportEncumbered)
            }
            .contentShape(Rectangle())
            .opacity(mountable ? 1 : 0.5)
        }
        .buttonStyle(.plain)
        .disabled(!mountable || session.borrowBusyDonor != nil)
    }

    // MARK: - Shared row bits

    @ViewBuilder
    private func busyOrHint(donorId: String, harmonic: Double) -> some View {
        if session.borrowBusyDonor == donorId {
            ProgressView().controlSize(.small)
        } else if part.isMelodic, harmonic >= 0.9 {
            Text("harmonizes").font(.caption2).foregroundStyle(JamTheme.accent)
        } else if part.isMelodic, harmonic >= 0.75 {
            Text("fits").font(.caption2).foregroundStyle(.secondary)
        }
    }

    /// The CC credit line — REQUIRED wherever a crate track shows (CC-BY). A
    /// ShareAlike track also carries the "export-locked" badge so the user knows
    /// exporting a remix with it re-licenses the whole export BY-SA.
    @ViewBuilder
    private func attributionLine(_ attribution: String, encumbered: Bool) -> some View {
        if !attribution.isEmpty || encumbered {
            HStack(spacing: 5) {
                if !attribution.isEmpty {
                    Text(attribution)
                        .font(.system(size: 9))
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
                if encumbered {
                    Text("export-locked")
                        .font(.system(size: 9, weight: .semibold))
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(JamTheme.error.opacity(0.25), in: Capsule())
                        .foregroundStyle(JamTheme.error)
                }
            }
        }
    }

    private func crateSubtitle(
        key: String?, tempo: Double, genre: String, mood: String
    ) -> String {
        var bits: [String] = []
        if part.isMelodic, let k = key, !k.isEmpty { bits.append(k) }
        if tempo > 0 { bits.append("\(Int(tempo)) bpm") }
        if !genre.isEmpty { bits.append(genre) }
        if !mood.isEmpty { bits.append(mood) }
        return bits.joined(separator: " · ")
    }

    // MARK: - Session context

    private var sessionBpm: Double? {
        session.sessionTarget.targetBpm ?? session.currentSongTempoBpm
    }
    private var sessionKey: String? {
        session.sessionTarget.targetKey ?? session.currentSongDetectedKey
    }

    // MARK: - Loading

    private func loadForCurrentSource() async {
        switch source {
        case .mySongs: await loadMySongs()
        case .crate: await reloadCrate()
        }
    }

    private func loadMySongs() async {
        // Session ON → rank candidates against the session key/tempo; OFF →
        // both nil, identical to today's host-ranked candidate fetch.
        let t = session.sessionTarget
        candidates = await session.borrowCandidates(
            stem: part.stem, targetBpm: t.targetBpm, targetKey: t.targetKey)
        loaded = true
    }

    /// Reload BOTH crate views with the current facet filters (the ranked strip
    /// and the browse grid stack on the same facets, per the spec).
    private func reloadCrate() async {
        async let a: Void = loadCrateRanked()
        async let b: Void = loadBrowse()
        _ = await (a, b)
    }

    private func loadCrateRanked() async {
        crateRankedLoaded = false
        crateRanked = await session.crateCandidates(
            stem: part.stem, genreMode: genreMode, facets: rankFacets())
        crateRankedLoaded = true
    }

    private func loadBrowse() async {
        browsing = true
        browse = await session.searchCrate(facets: browseFacets())
        browsing = false
    }

    /// Facets applied to the ranked strip (pre-rank filter). Excludes the free
    /// text — the ranked strip is session-matched, not text-searched.
    private func rankFacets() -> CrateFacetQuery {
        var f = CrateFacetQuery()
        f.genre = selectedGenre
        f.mood = selectedMood
        f.cleanExportOnly = cleanExportOnly
        applyTempoKey(&f)
        return f
    }

    /// Facets for the browse grid = the same filters + the free-text box.
    private func browseFacets() -> CrateFacetQuery {
        var f = rankFacets()
        f.text = searchText
        return f
    }

    private func applyTempoKey(_ f: inout CrateFacetQuery) {
        if nearTempo, let bpm = sessionBpm, bpm > 0 {
            f.tempoMin = bpm * 0.9
            f.tempoMax = bpm * 1.1
        }
        if sessionKeyOnly, let k = sessionKey, !k.isEmpty { f.key = k }
    }

    private func loadCrate(
        trackId: String, name: String, attribution: String, encumbered: Bool
    ) {
        Task {
            let t = session.sessionTarget
            await session.loadCrateLoops(
                trackId: trackId, stem: part.stem, trackName: name,
                attribution: attribution,
                targetBpm: t.targetBpm, targetKey: t.targetKey)
            if session.remixError == nil { dismiss() }
        }
    }

    private func loadingRow(_ text: String) -> some View {
        HStack {
            ProgressView().controlSize(.small)
            Text(text).font(.caption).foregroundStyle(.secondary)
        }
    }
}
