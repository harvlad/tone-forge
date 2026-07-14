// ChopPickerSheet.swift
//
// Sheet for browsing and selecting chops/samples to add to a sequencer
// track or timeline (D-023 Phase 6). Shows:
//
// 1. Bundle chops grouped by preset (harmonic, sections, phrases, etc.)
// 2. Pack pads from the active sample pack
// 3. Local recordings
//
// Each source shows a preview button and tap-to-select action.
// The sheet returns a ChopReference for the selected sound.

import SwiftUI
import ToneForgeEngine

/// Category tabs for the chop picker.
enum ChopPickerCategory: String, CaseIterable {
    case packs = "Sample Packs"
    case bundle = "Song Chops"
    case local = "Recordings"
    case sequences = "Sequences"

    var icon: String {
        switch self {
        case .bundle: return "waveform"
        case .packs: return "square.grid.2x2"
        case .local: return "mic.fill"
        case .sequences: return "square.grid.3x3.fill"
        }
    }
}

struct ChopPickerSheet: View {
    /// Callback when a chop is selected.
    let onSelect: (ChopReference, String?) -> Void
    /// Bundle chops grouped by preset key.
    let bundleChops: [String: [Chop]]
    /// Available sample packs.
    let samplePacks: [SamplePackInfo]
    /// Local recordings.
    let localSamples: [LocalSampleInfo]
    /// Saved sequencer patterns (empty = hide the Sequences tab).
    var sequences: [SequenceInfo] = []
    /// Curated catalog packs not yet downloaded — listed under the
    /// Sample Packs tab as download rows so packs can be pulled without
    /// leaving the pad picker. Once a download completes the parent
    /// re-renders and the pack moves into `samplePacks` with its pads.
    var downloadablePacks: [DownloadablePackInfo] = []
    /// packIds with an in-flight download (row shows a spinner).
    var downloadingPackIds: Set<String> = []
    /// Fractional progress (0–1) per in-flight download, keyed by
    /// packId. Missing key = indeterminate.
    var downloadFractions: [String: Double] = [:]
    /// Kick off a curated-pack download by packId.
    var onDownloadPack: ((String) -> Void)? = nil
    /// Preview callback for auditioning (play).
    let onPreview: (ChopReference) -> Void
    /// Stop preview callback.
    var onStopPreview: (() -> Void)? = nil
    /// One-shot length (seconds) for a pack pad, so a playing pad cell
    /// can revert its icon when the sound finishes. nil = looping /
    /// unknown (cell stays "playing" until tapped again).
    var previewDurationProvider: ((String, Int) -> Double?)? = nil

    @Environment(\.dismiss) private var dismiss
    @State private var category: ChopPickerCategory = .packs
    @State private var searchText = ""
    @State private var selectedPreset: String?

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Category tabs
                categoryTabs

                // Search bar
                searchBar

                Divider()
                    .background(TFTheme.stroke)

                // Content based on category
                switch category {
                case .bundle:
                    bundleContent
                case .packs:
                    packsContent
                case .local:
                    localContent
                case .sequences:
                    sequencesContent
                }
            }
            .background(TFTheme.background)
            .navigationTitle("Add Sound")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }

    // MARK: - Category Tabs

    /// Tabs to show — the Sequences tab only appears when there are
    /// saved sequences to pick (i.e. when browsing from a pad).
    private var availableCategories: [ChopPickerCategory] {
        ChopPickerCategory.allCases.filter {
            $0 != .sequences || !sequences.isEmpty
        }
    }

    private var categoryTabs: some View {
        HStack(spacing: 0) {
            ForEach(availableCategories, id: \.self) { cat in
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        category = cat
                    }
                } label: {
                    VStack(spacing: 4) {
                        Image(systemName: cat.icon)
                            .font(.title3)
                        Text(cat.rawValue)
                            .font(.caption2)
                    }
                    .foregroundStyle(category == cat ? .white : TFTheme.textSecondary)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(
                        category == cat
                            ? Color.accentColor.opacity(0.3)
                            : Color.clear
                    )
                }
            }
        }
        .background(TFTheme.surface)
    }

    // MARK: - Search Bar

    private var searchBar: some View {
        HStack {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(TFTheme.textSecondary)

            TextField("Search", text: $searchText)
                .textFieldStyle(.plain)
                .foregroundStyle(TFTheme.textPrimary)

            if !searchText.isEmpty {
                Button {
                    searchText = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(TFTheme.textSecondary)
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(TFTheme.chipFill)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .padding()
    }

    // MARK: - Bundle Content

    private var bundleContent: some View {
        ScrollView {
            LazyVStack(spacing: 0, pinnedViews: .sectionHeaders) {
                ForEach(sortedPresetKeys, id: \.self) { presetKey in
                    if let chops = bundleChops[presetKey], !chops.isEmpty {
                        Section {
                            ForEach(filteredChops(chops), id: \.idx) { chop in
                                ChopRow(
                                    chop: chop,
                                    presetKey: presetKey,
                                    onSelect: {
                                        let ref = ChopReference.bundleChop(
                                            presetKey: presetKey,
                                            chopIndex: chop.idx,
                                            resolvedId: nil
                                        )
                                        onSelect(ref, chopLabel(chop, preset: presetKey))
                                        dismiss()
                                    },
                                    onPreview: {
                                        let ref = ChopReference.bundleChop(
                                            presetKey: presetKey,
                                            chopIndex: chop.idx,
                                            resolvedId: nil
                                        )
                                        onPreview(ref)
                                    }
                                )
                            }
                        } header: {
                            presetHeader(presetKey)
                        }
                    }
                }

                if filteredBundleCount == 0 {
                    emptySearchState
                }
            }
        }
    }

    private var sortedPresetKeys: [String] {
        bundleChops.keys.sorted()
    }

    private func filteredChops(_ chops: [Chop]) -> [Chop] {
        guard !searchText.isEmpty else { return chops }
        return chops.filter { chop in
            let label = chopLabel(chop, preset: "").lowercased()
            return label.contains(searchText.lowercased())
        }
    }

    private var filteredBundleCount: Int {
        bundleChops.values.reduce(0) { $0 + filteredChops($1).count }
    }

    private func presetHeader(_ key: String) -> some View {
        HStack {
            Text(presetDisplayName(key))
                .font(.caption.weight(.semibold))
                .foregroundStyle(TFTheme.textSecondary)
                .textCase(.uppercase)

            Spacer()

            Text("\(bundleChops[key]?.count ?? 0) chops")
                .font(.caption2)
                .foregroundStyle(TFTheme.textSecondary)
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(TFTheme.surface)
    }

    private func presetDisplayName(_ key: String) -> String {
        switch key {
        case "harmonic": return "Chords"
        case "sections": return "Sections"
        case "phrases": return "Phrases"
        case "onsets": return "Transients"
        case "beats": return "Beats"
        default: return key.capitalized
        }
    }

    private func chopLabel(_ chop: Chop, preset: String) -> String {
        if let label = chop.sectionLabel {
            return label
        }
        if let chord = chop.chordSymbol {
            return chord
        }
        return "\(presetDisplayName(preset)) #\(chop.idx + 1)"
    }

    // MARK: - Packs Content

    private var packsContent: some View {
        ScrollView {
            VStack(spacing: 12) {
                ForEach(filteredPacks, id: \.id) { pack in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(pack.name)
                            .font(.headline)
                            .foregroundStyle(TFTheme.textPrimary)

                        // 4-column grid
                        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 10), count: 4), spacing: 10) {
                            ForEach(pack.pads, id: \.padIdx) { pad in
                                PadPickerCell(
                                    name: pad.name ?? "Pad \(pad.padIdx)",
                                    family: pad.family,
                                    onSelect: {
                                        onStopPreview?()
                                        let ref = ChopReference.packPad(packId: pack.id, padIdx: pad.padIdx)
                                        onSelect(ref, pad.name)
                                        dismiss()
                                    },
                                    onPlay: {
                                        let ref = ChopReference.packPad(packId: pack.id, padIdx: pad.padIdx)
                                        onPreview(ref)
                                        return previewDurationProvider?(pack.id, pad.padIdx)
                                    },
                                    onStop: {
                                        onStopPreview?()
                                    }
                                )
                            }
                        }
                    }
                    .padding()
                    .background(TFTheme.surface, in: RoundedRectangle(cornerRadius: 12))
                }

                // Downloadable curated packs (not yet on disk).
                ForEach(filteredDownloadablePacks, id: \.id) { pack in
                    DownloadablePackRow(
                        pack: pack,
                        isDownloading: downloadingPackIds.contains(pack.id),
                        progress: downloadFractions[pack.id],
                        onDownload: { onDownloadPack?(pack.id) }
                    )
                }

                if filteredPacks.isEmpty && filteredDownloadablePacks.isEmpty {
                    emptySearchState
                }
            }
            .padding()
        }
    }

    private var filteredPacks: [SamplePackInfo] {
        guard !searchText.isEmpty else { return samplePacks }
        return samplePacks.filter {
            $0.name.lowercased().contains(searchText.lowercased())
        }
    }

    private var filteredDownloadablePacks: [DownloadablePackInfo] {
        guard !searchText.isEmpty else { return downloadablePacks }
        return downloadablePacks.filter {
            $0.name.lowercased().contains(searchText.lowercased())
        }
    }

    // MARK: - Local Content

    private var localContent: some View {
        ScrollView {
            LazyVStack(spacing: 8) {
                ForEach(filteredLocalSamples, id: \.id) { sample in
                    LocalSampleRow(
                        sample: sample,
                        onSelect: {
                            let ref = ChopReference.localSample(id: sample.id)
                            onSelect(ref, sample.name)
                            dismiss()
                        },
                        onPreview: {
                            let ref = ChopReference.localSample(id: sample.id)
                            onPreview(ref)
                        }
                    )
                }

                if filteredLocalSamples.isEmpty {
                    emptyLocalState
                }
            }
            .padding()
        }
    }

    private var filteredLocalSamples: [LocalSampleInfo] {
        guard !searchText.isEmpty else { return localSamples }
        return localSamples.filter {
            $0.name.lowercased().contains(searchText.lowercased())
        }
    }

    // MARK: - Sequences Content

    private var sequencesContent: some View {
        ScrollView {
            LazyVStack(spacing: 8) {
                ForEach(filteredSequences, id: \.id) { seq in
                    SequenceRow(
                        sequence: seq,
                        onSelect: {
                            onStopPreview?()
                            let ref = ChopReference.sequence(patternId: seq.id)
                            onSelect(ref, seq.name)
                            dismiss()
                        }
                    )
                }

                if filteredSequences.isEmpty {
                    emptySequencesState
                }
            }
            .padding()
        }
    }

    private var filteredSequences: [SequenceInfo] {
        guard !searchText.isEmpty else { return sequences }
        return sequences.filter {
            $0.name.lowercased().contains(searchText.lowercased())
        }
    }

    private var emptySequencesState: some View {
        VStack(spacing: 12) {
            Image(systemName: "square.grid.3x3")
                .font(.largeTitle)
                .foregroundStyle(TFTheme.textSecondary)

            Text("No saved sequences")
                .font(.headline)
                .foregroundStyle(TFTheme.textPrimary)

            Text("Build a pattern in the sequencer and tap Save")
                .font(.subheadline)
                .foregroundStyle(TFTheme.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 60)
    }

    // MARK: - Empty States

    private var emptySearchState: some View {
        VStack(spacing: 12) {
            Image(systemName: "magnifyingglass")
                .font(.largeTitle)
                .foregroundStyle(TFTheme.textSecondary)

            Text("No results for \"\(searchText)\"")
                .font(.headline)
                .foregroundStyle(TFTheme.textPrimary)

            Text("Try a different search term")
                .font(.subheadline)
                .foregroundStyle(TFTheme.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 60)
    }

    private var emptyLocalState: some View {
        VStack(spacing: 12) {
            Image(systemName: "mic.slash")
                .font(.largeTitle)
                .foregroundStyle(TFTheme.textSecondary)

            Text("No recordings yet")
                .font(.headline)
                .foregroundStyle(TFTheme.textPrimary)

            Text("Record samples using the mic button on pads")
                .font(.subheadline)
                .foregroundStyle(TFTheme.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 60)
    }
}

// MARK: - Chop Row

private struct ChopRow: View {
    let chop: Chop
    let presetKey: String
    let onSelect: () -> Void
    let onPreview: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            // Preview button
            Button(action: onPreview) {
                Image(systemName: "play.circle.fill")
                    .font(.title2)
                    .foregroundStyle(Color.accentColor)
            }

            // Info
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    if let label = chop.sectionLabel {
                        Text(label)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(TFTheme.textPrimary)
                    }
                    if let chord = chop.chordSymbol {
                        Text(chord)
                            .font(.caption.weight(.medium))
                            .foregroundStyle(.orange)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.orange.opacity(0.2), in: Capsule())
                    }
                }

                Text(String(format: "%.1fs - %.1fs", chop.startSec, chop.endSec))
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
            }

            Spacer()

            // Select button
            Button(action: onSelect) {
                Image(systemName: "plus.circle")
                    .font(.title2)
                    .foregroundStyle(TFTheme.textSecondary)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
        .onTapGesture(perform: onSelect)
    }
}

// MARK: - Pad Picker Cell

private struct PadPickerCell: View {
    let name: String
    let family: SampleFamily?
    let onSelect: () -> Void
    /// Starts preview; returns the one-shot length (seconds) so the cell
    /// can auto-revert its icon, or nil for looping/unknown.
    let onPlay: () -> Double?
    let onStop: () -> Void

    @State private var isPlaying = false
    @State private var isPressed = false
    /// Guards the auto-reset timer against a retap starting a new play.
    @State private var playToken = UUID()

    /// Use the same family tint as the main pad grid for visual continuity.
    private var familyColor: Color {
        guard let family = family else { return TFTheme.chipFill }
        return TFTheme.familyTint(family)
    }

    var body: some View {
        VStack(spacing: 4) {
            // Pad tile matching main grid style
            ZStack(alignment: .topLeading) {
                // Solid background matching main pads
                RoundedRectangle(cornerRadius: 8)
                    .fill(familyColor.opacity(0.85))

                // Name label at top-left (like main pads)
                Text(name)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white)
                    .lineLimit(2)
                    .padding(.horizontal, 6)
                    .padding(.top, 6)

                // Play indicator (top-right corner)
                VStack {
                    HStack {
                        Spacer()
                        Image(systemName: isPlaying ? "stop.fill" : "play.fill")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(.white.opacity(0.9))
                            .padding(5)
                            .background(
                                Circle()
                                    .fill(isPlaying ? Color.orange : Color.black.opacity(0.4))
                            )
                            .padding(4)
                    }
                    Spacer()
                }
            }
            .frame(height: 64)
            .scaleEffect(isPressed ? 0.95 : 1.0)
        }
        .contentShape(Rectangle())
        .onTapGesture {
            // Tap to toggle preview
            if isPlaying {
                onStop()
                withAnimation(.easeInOut(duration: 0.1)) { isPlaying = false }
            } else {
                let dur = onPlay()
                withAnimation(.easeInOut(duration: 0.1)) { isPlaying = true }
                // Auto-revert the icon when a one-shot finishes.
                if let dur {
                    let token = UUID()
                    playToken = token
                    Task { @MainActor in
                        try? await Task.sleep(nanoseconds: UInt64(dur * 1_000_000_000))
                        if playToken == token {
                            withAnimation(.easeInOut(duration: 0.1)) { isPlaying = false }
                        }
                    }
                }
            }
        }
        .onLongPressGesture(minimumDuration: 0.3, pressing: { pressing in
            withAnimation(.easeInOut(duration: 0.1)) {
                isPressed = pressing
            }
        }, perform: {
            // Long press to add
            onSelect()
        })
    }
}

// MARK: - Local Sample Row

private struct LocalSampleRow: View {
    let sample: LocalSampleInfo
    let onSelect: () -> Void
    let onPreview: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            // Preview button
            Button(action: onPreview) {
                Image(systemName: "play.circle.fill")
                    .font(.title2)
                    .foregroundStyle(.green)
            }

            // Info
            VStack(alignment: .leading, spacing: 2) {
                Text(sample.name)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)

                Text(String(format: "%.1fs", sample.durationSec))
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
            }

            Spacer()

            // Select button
            Button(action: onSelect) {
                Image(systemName: "plus.circle")
                    .font(.title2)
                    .foregroundStyle(TFTheme.textSecondary)
            }
        }
        .padding()
        .background(TFTheme.surface, in: RoundedRectangle(cornerRadius: 10))
        .contentShape(Rectangle())
        .onTapGesture(perform: onSelect)
    }
}

// MARK: - Sequence Row

private struct SequenceRow: View {
    let sequence: SequenceInfo
    let onSelect: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "square.grid.3x3.fill")
                .font(.title2)
                .foregroundStyle(.teal)

            VStack(alignment: .leading, spacing: 2) {
                Text(sequence.name)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)

                Text("\(sequence.trackCount) tracks · \(sequence.stepCount) steps")
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
            }

            Spacer()

            Button(action: onSelect) {
                Image(systemName: "plus.circle")
                    .font(.title2)
                    .foregroundStyle(TFTheme.textSecondary)
            }
        }
        .padding()
        .background(TFTheme.surface, in: RoundedRectangle(cornerRadius: 10))
        .contentShape(Rectangle())
        .onTapGesture(perform: onSelect)
    }
}

// MARK: - Downloadable Pack Row

private struct DownloadablePackRow: View {
    let pack: DownloadablePackInfo
    let isDownloading: Bool
    var progress: Double? = nil
    let onDownload: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            RoundedRectangle(cornerRadius: 8)
                .fill(TFTheme.familyTint(pack.family).opacity(0.85))
                .frame(width: 44, height: 44)
                .overlay(
                    Image(systemName: "square.grid.2x2.fill")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(.white)
                )

            VStack(alignment: .leading, spacing: 2) {
                Text(pack.name)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
                if isDownloading {
                    ProgressView(value: progress ?? 0)
                        .progressViewStyle(.linear)
                        .padding(.top, 2)
                }
            }

            Spacer()

            if isDownloading {
                ProgressView().controlSize(.small)
            } else {
                Button(action: onDownload) {
                    Image(systemName: "arrow.down.circle.fill")
                        .font(.title2)
                        .foregroundStyle(Color.accentColor)
                }
                .buttonStyle(.borderless)
            }
        }
        .padding()
        .background(TFTheme.surface, in: RoundedRectangle(cornerRadius: 12))
        .contentShape(Rectangle())
        .onTapGesture { if !isDownloading { onDownload() } }
    }

    private var subtitle: String {
        if isDownloading {
            let pct = Int(((progress ?? 0) * 100).rounded())
            return "Downloading… \(pct)%"
        }
        return "\(pack.padCount) pads · not downloaded"
    }
}

// MARK: - Info Types

/// Simplified saved-sequence info for the picker.
struct SequenceInfo: Identifiable {
    let id: UUID
    let name: String
    let trackCount: Int
    let stepCount: Int
}

/// Simplified pack info for the picker.
struct SamplePackInfo: Identifiable {
    let id: String
    let name: String
    let padCount: Int
    let pads: [SamplePadInfo]
}

/// A curated pack the user can download from the catalog but hasn't
/// pulled to disk yet. Shown as a download row in the pad picker.
struct DownloadablePackInfo: Identifiable {
    let id: String
    let name: String
    let family: SampleFamily
    let padCount: Int
}

struct SamplePadInfo {
    let padIdx: Int
    let name: String?
    let family: SampleFamily?
}

struct LocalSampleInfo: Identifiable {
    let id: UUID
    let name: String
    let durationSec: Double
}

// MARK: - Preview

#if DEBUG
struct ChopPickerSheet_Previews: PreviewProvider {
    static var previews: some View {
        ChopPickerSheet(
            onSelect: { ref, name in print("Selected: \(ref), name: \(name ?? "nil")") },
            bundleChops: [
                "harmonic": [
                    Chop(idx: 0, startSec: 0, endSec: 4, durationSec: 4, kind: "chord", root: 0, sectionLabel: "Verse", chordSymbol: "Am", colorHint: nil),
                    Chop(idx: 1, startSec: 4, endSec: 8, durationSec: 4, kind: "chord", root: 7, sectionLabel: nil, chordSymbol: "G", colorHint: nil),
                    Chop(idx: 2, startSec: 8, endSec: 12, durationSec: 4, kind: "chord", root: 5, sectionLabel: nil, chordSymbol: "F", colorHint: nil)
                ],
                "sections": [
                    Chop(idx: 0, startSec: 0, endSec: 30, durationSec: 30, kind: "section", root: nil, sectionLabel: "Intro", chordSymbol: nil, colorHint: nil),
                    Chop(idx: 1, startSec: 30, endSec: 90, durationSec: 60, kind: "section", root: nil, sectionLabel: "Verse 1", chordSymbol: nil, colorHint: nil)
                ]
            ],
            samplePacks: [
                SamplePackInfo(
                    id: "starter",
                    name: "Starter Pack",
                    padCount: 8,
                    pads: [
                        SamplePadInfo(padIdx: 0, name: "Kick", family: .percussion),
                        SamplePadInfo(padIdx: 1, name: "Snare", family: .percussion),
                        SamplePadInfo(padIdx: 2, name: "Hi-Hat", family: .percussion),
                        SamplePadInfo(padIdx: 3, name: "Clap", family: .percussion),
                        SamplePadInfo(padIdx: 4, name: "Warm Pad", family: .pads),
                        SamplePadInfo(padIdx: 5, name: "Bass Hit", family: .bass),
                        SamplePadInfo(padIdx: 6, name: "Riser", family: .fx),
                        SamplePadInfo(padIdx: 7, name: "Vocal Chop", family: .vocals)
                    ]
                )
            ],
            localSamples: [
                LocalSampleInfo(id: UUID(), name: "Voice Loop 1", durationSec: 2.5),
                LocalSampleInfo(id: UUID(), name: "Guitar Riff", durationSec: 4.0)
            ],
            onPreview: { ref in print("Preview: \(ref)") }
        )
        .preferredColorScheme(.dark)
    }
}
#endif
