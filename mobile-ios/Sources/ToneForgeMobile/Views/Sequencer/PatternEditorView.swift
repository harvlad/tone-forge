// PatternEditorView.swift
//
// MPC-style step sequencer grid (D-023 Phase 4). Displays all tracks
// with their step cells, plus transport controls and pattern settings.
//
// Features:
//   - 8/16/32 step grid per track
//   - Tap steps to toggle on/off
//   - Vertical drag on steps to adjust velocity
//   - Mute/solo per track
//   - Add/remove tracks via ChopPickerSheet
//   - Play/stop with visual playhead
//
// The view observes SequencerPlayer for reactive updates.

import SwiftUI
import ToneForgeEngine

struct PatternEditorView: View {
    @ObservedObject var player: SequencerPlayer
    @EnvironmentObject private var appState: AppState

    @State private var showingChopPicker = false
    @State private var showingPatternSettings = false
    @State private var showingSaveSheet = false
    @State private var saveName = ""
    @State private var didSave = false

    var body: some View {
        VStack(spacing: 0) {
            // Header with pattern info
            patternHeader

            Divider()
                .background(TFTheme.stroke)

            // Track rows
            if player.pattern.tracks.isEmpty {
                emptyState
            } else {
                trackList
            }

            Divider()
                .background(TFTheme.stroke)

            // Transport controls
            transportRow
        }
        .background(TFTheme.background)
        .sheet(isPresented: $showingChopPicker) {
            ChopPickerSheet(
                onSelect: { chopRef, name in
                    player.addTrack(for: chopRef, name: name)
                },
                bundleChops: bundleChopsForPicker,
                samplePacks: samplePacksForPicker,
                localSamples: [], // FUTURE: Pull from local recordings store
                downloadablePacks: downloadablePacksForPicker,
                downloadingPackIds: downloadingPackIds,
                downloadFractions: downloadFractions,
                onDownloadPack: { packId in
                    guard let entry = appState.curatedCatalog.first(where: { $0.packId == packId })
                    else { return }
                    Task { await appState.downloadCuratedPack(entry) }
                },
                onPreview: { ref in
                    appState.previewChopReference(ref)
                },
                onStopPreview: {
                    appState.modeCoordinator.stopPreviewPad()
                },
                previewDurationProvider: { packId, padIdx in
                    appState.previewPadDurationSec(packId: packId, padIdx: padIdx)
                }
            )
            .task { await appState.refreshCuratedCatalog() }
        }
        .sheet(isPresented: $showingPatternSettings) {
            PatternSettingsSheet(pattern: $player.pattern)
        }
        .sheet(isPresented: $showingSaveSheet) {
            saveSheet
        }
    }

    // MARK: - Header

    private var patternHeader: some View {
        HStack {
            // Pattern name
            Button {
                showingPatternSettings = true
            } label: {
                HStack(spacing: 6) {
                    Text(player.pattern.name)
                        .font(.headline)
                        .foregroundStyle(TFTheme.textPrimary)

                    Image(systemName: "chevron.down")
                        .font(.caption)
                        .foregroundStyle(TFTheme.textSecondary)
                }
            }

            Spacer()

            // Save pattern (export for pad assignment)
            Button {
                saveName = player.pattern.name
                showingSaveSheet = true
            } label: {
                Image(systemName: didSave ? "checkmark.circle.fill" : "square.and.arrow.down")
                    .font(.headline)
                    .foregroundStyle(didSave ? .green : Color.accentColor)
            }
            .disabled(player.pattern.tracks.isEmpty)
            .accessibilityLabel("Save pattern")

            // Step count badge — tap to open pattern settings
            Button {
                showingPatternSettings = true
            } label: {
                Text("\(player.pattern.stepCount.rawValue) steps")
                    .tfChip()
            }

            // BPM badge — tap to open pattern settings
            Button {
                showingPatternSettings = true
            } label: {
                if let bpm = player.pattern.bpmOverride {
                    Text("\(Int(bpm)) BPM")
                        .tfChip()
                } else {
                    Text("\(Int(player.songBPM)) BPM")
                        .tfChip()
                }
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
    }

    // MARK: - Empty State

    private var emptyState: some View {
        VStack(spacing: 16) {
            Image(systemName: "waveform.badge.plus")
                .font(.system(size: 48))
                .foregroundStyle(TFTheme.textSecondary)

            Text("No tracks yet")
                .font(.headline)
                .foregroundStyle(TFTheme.textPrimary)

            Text("Add chops or samples to build your pattern")
                .font(.subheadline)
                .foregroundStyle(TFTheme.textSecondary)
                .multilineTextAlignment(.center)

            Button {
                showingChopPicker = true
            } label: {
                Label("Add Track", systemImage: "plus")
                    .font(.headline)
                    .foregroundStyle(.white)
                    .padding(.horizontal, 24)
                    .padding(.vertical, 12)
                    .background(Color.accentColor, in: Capsule())
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }

    // MARK: - Track List

    private var trackList: some View {
        ScrollView {
            VStack(spacing: 0) {
                ForEach(Array(player.pattern.tracks.enumerated()), id: \.element.id) { index, track in
                    TrackRowView(
                        track: track,
                        trackIndex: index,
                        currentStep: player.currentStep,
                        isPlaying: player.isPlaying,
                        onToggleStep: { step in
                            player.toggleStep(track: index, step: step)
                        },
                        onSetVelocity: { step, velocity in
                            player.setStepVelocity(track: index, step: step, velocity: velocity)
                        },
                        onToggleMute: {
                            player.toggleMute(track: index)
                        },
                        onToggleSolo: {
                            player.toggleSolo(track: index)
                        },
                        onPreview: {
                            player.previewTrack(index)
                        },
                        onSetRole: { role in
                            player.setTrackChop(
                                track: index,
                                chopRef: role.chopRef,
                                name: role.displayName
                            )
                        }
                    )

                    if index < player.pattern.tracks.count - 1 {
                        Divider()
                            .background(TFTheme.stroke)
                    }
                }
            }
            .padding(.vertical, 4)
        }
    }

    // MARK: - Transport Row

    private var transportRow: some View {
        HStack(spacing: 16) {
            // Add track button
            Button {
                showingChopPicker = true
            } label: {
                Image(systemName: "plus.circle")
                    .font(.title2)
                    .foregroundStyle(TFTheme.textSecondary)
            }

            Spacer()

            // Transport group: play, stop-all, record
            HStack(spacing: 8) {
                // Play/Stop button
                Button {
                    if player.isPlaying {
                        player.stop()
                    } else {
                        player.play()
                    }
                } label: {
                    Image(systemName: player.isPlaying ? "stop.fill" : "play.fill")
                        .font(.title)
                        .foregroundStyle(player.isPlaying ? .red : .green)
                        .frame(width: 50, height: 44)
                        .background(
                            (player.isPlaying ? Color.red : Color.green).opacity(0.15),
                            in: RoundedRectangle(cornerRadius: 10)
                        )
                }

                // Stop-all (sample panic)
                stopAllButton

                // Record toggle — arms the session recorder and drives
                // the sequencer's own clock (never the song transport).
                RecordToggle(
                    startsTransport: false,
                    compact: true,
                    onArm: { if !player.isPlaying { player.play() } },
                    onStop: { player.stop() }
                )
            }

            Spacer()

            // Step indicator
            HStack(spacing: 4) {
                Text("Step")
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)

                Text("\(player.currentStep + 1)")
                    .font(.headline.monospacedDigit())
                    .foregroundStyle(TFTheme.textPrimary)
                    .frame(minWidth: 24)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 12)
    }

    private var stopAllButton: some View {
        let hasRinging = !appState.ringingPadKeys.isEmpty
        return Button {
            appState.stopAllSamplePads()
        } label: {
            Image(systemName: "stop.circle.fill")
                .font(.title2)
                .foregroundStyle(hasRinging ? Color.accentColor : .secondary)
                .opacity(hasRinging ? 1 : 0.35)
        }
        .disabled(!hasRinging)
        .accessibilityLabel("Stop all pads")
    }

    // MARK: - Save Sheet

    private var saveSheet: some View {
        NavigationStack {
            Form {
                Section("Sequence name") {
                    TextField("Name", text: $saveName)
                }
                Section {
                    Text("Saved sequences appear when you add a sound to a pad. Pressing that pad plays the whole sequence.")
                        .font(.footnote)
                        .foregroundStyle(TFTheme.textSecondary)
                }
            }
            .navigationTitle("Save Sequence")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { showingSaveSheet = false }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { commitSave() }
                        .disabled(saveName.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
    }

    /// Persist the current pattern under `saveName`. Reuses the pattern's
    /// existing id so re-saving updates in place.
    private func commitSave() {
        let name = saveName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        player.pattern.name = name
        appState.sequencerPatternStore.save(player.pattern)
        showingSaveSheet = false
        didSave = true
    }

    // MARK: - ChopPicker Data

    /// Bundle chops grouped by preset key from the current song.
    private var bundleChopsForPicker: [String: [Chop]] {
        guard let bundle = appState.currentBundle else { return [:] }
        var result: [String: [Chop]] = [:]

        // Get chops from presets (harmonic, sections, etc.)
        for (key, preset) in bundle.presets {
            if !preset.chops.isEmpty {
                result[key] = preset.chops
            }
        }

        // Create section chops from timeline if no preset exists
        if result["sections"] == nil && !bundle.timeline.sections.isEmpty {
            result["sections"] = bundle.timeline.sections.enumerated().map { idx, section in
                Chop(
                    idx: idx,
                    startSec: section.start,
                    endSec: section.end,
                    durationSec: section.end - section.start,
                    kind: "section",
                    root: nil,
                    sectionLabel: section.label,
                    chordSymbol: nil,
                    colorHint: nil
                )
            }
        }

        return result
    }

    /// Sample packs available for the picker: the active pack plus every
    /// pack already downloaded to disk. Downloaded-but-inactive packs
    /// must appear here (with their pads) so they aren't mislabelled as
    /// "not downloaded" in the download-rows section.
    private var samplePacksForPicker: [SamplePackInfo] {
        var packs: [SamplePackInfo] = []
        var seen = Set<String>()

        func add(id: String, pads: [SamplePadInfo]) {
            guard !seen.contains(id) else { return }
            seen.insert(id)
            packs.append(SamplePackInfo(
                id: id,
                name: pickerPackName(for: id),
                padCount: pads.count,
                pads: pads
            ))
        }

        if let active = appState.activeSamplePack {
            add(id: active.pack.packId, pads: active.pack.pads.map {
                SamplePadInfo(padIdx: $0.padIdx, name: $0.name, family: $0.family)
            })
        }

        if let bank = appState.sampleBank {
            for packId in appState.cachedPackIds.sorted() {
                guard let resolved = try? bank.loadCached(packId: packId) else { continue }
                add(id: packId, pads: resolved.pack.pads.map {
                    SamplePadInfo(padIdx: $0.padIdx, name: $0.name, family: $0.family)
                })
            }
        }

        return packs
    }

    /// Display name for a pack id — catalog name if known, else the id
    /// prettified.
    private func pickerPackName(for packId: String) -> String {
        if let entry = appState.curatedCatalog.first(where: { $0.packId == packId }) {
            return entry.name
        }
        return packId.replacingOccurrences(of: "-", with: " ").capitalized
    }

    /// Curated catalog packs not yet in the picker — download rows.
    private var downloadablePacksForPicker: [DownloadablePackInfo] {
        let present = Set(samplePacksForPicker.map { $0.id })
        return appState.curatedCatalog
            .filter { !present.contains($0.packId) }
            .map { entry in
                DownloadablePackInfo(
                    id: entry.packId,
                    name: entry.name,
                    family: entry.family,
                    padCount: entry.padCount
                )
            }
    }

    /// packIds with an in-flight (not-complete) curated download.
    private var downloadingPackIds: Set<String> {
        Set(appState.curatedDownloads.values
            .filter { !$0.isComplete }
            .map { $0.packId })
    }

    /// Fractional progress (0–1) per in-flight curated download —
    /// byte-weighted when the server declared sizes, else pad-count.
    private var downloadFractions: [String: Double] {
        appState.curatedDownloads.reduce(into: [:]) { dict, kv in
            let p = kv.value
            guard !p.isComplete else { return }
            if p.bytesTotal > 0 {
                dict[kv.key] = Double(p.bytesDownloaded) / Double(p.bytesTotal)
            } else if p.padsTotal > 0 {
                dict[kv.key] = Double(p.padsCompleted) / Double(p.padsTotal)
            } else {
                dict[kv.key] = 0
            }
        }
    }
}

// MARK: - Pattern Settings Sheet

private struct PatternSettingsSheet: View {
    @Binding var pattern: SequencerPattern
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Pattern") {
                    TextField("Name", text: $pattern.name)

                    Picker("Steps", selection: $pattern.stepCount) {
                        ForEach(PatternStepCount.allCases, id: \.self) { count in
                            Text(count.label).tag(count)
                        }
                    }

                    Toggle("Loop", isOn: $pattern.isLooping)
                }

                Section("Timing") {
                    Toggle("Override BPM", isOn: Binding(
                        get: { pattern.bpmOverride != nil },
                        set: { pattern.bpmOverride = $0 ? 120 : nil }
                    ))

                    if pattern.bpmOverride != nil {
                        Stepper(
                            "BPM: \(Int(pattern.bpmOverride ?? 120))",
                            value: Binding(
                                get: { pattern.bpmOverride ?? 120 },
                                set: { pattern.bpmOverride = $0 }
                            ),
                            in: 40...240,
                            step: 1
                        )
                    }

                    HStack {
                        Text("Swing")
                        Slider(
                            value: Binding(
                                get: { Double(pattern.swing) },
                                set: { pattern.swing = Float($0) }
                            ),
                            in: 0...0.5
                        )
                        Text("\(Int(pattern.swing * 100))%")
                            .frame(width: 40)
                    }
                }
            }
            .navigationTitle("Pattern Settings")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}

// MARK: - Preview

#if DEBUG
struct PatternEditorView_Previews: PreviewProvider {
    struct Wrapper: View {
        @StateObject private var player: SequencerPlayer

        init() {
            // Create a mock event bus
            let bus = ContributionEventBus()

            // Create a pattern with demo tracks
            var pattern = SequencerPattern(name: "Demo Beat", stepCount: .sixteen)

            var kickTrack = SequencerTrack(
                chopRef: .packPad(packId: "demo", padIdx: 51),
                stepCount: 16,
                name: "Kick"
            )
            // Set kick pattern: 1, 5, 9, 13
            kickTrack.steps[0].velocity = 1.0
            kickTrack.steps[4].velocity = 1.0
            kickTrack.steps[8].velocity = 1.0
            kickTrack.steps[12].velocity = 1.0
            pattern.tracks.append(kickTrack)

            var snareTrack = SequencerTrack(
                chopRef: .packPad(packId: "demo", padIdx: 52),
                stepCount: 16,
                name: "Snare"
            )
            // Set snare pattern: 5, 13
            snareTrack.steps[4].velocity = 1.0
            snareTrack.steps[12].velocity = 1.0
            pattern.tracks.append(snareTrack)

            var hhTrack = SequencerTrack(
                chopRef: .packPad(packId: "demo", padIdx: 53),
                stepCount: 16,
                name: "Hi-Hat"
            )
            // Set hi-hat pattern: every other step
            for i in stride(from: 0, to: 16, by: 2) {
                hhTrack.steps[i].velocity = 0.7
            }
            pattern.tracks.append(hhTrack)

            _player = StateObject(wrappedValue: SequencerPlayer(
                pattern: pattern,
                eventBus: bus
            ))
        }

        var body: some View {
            PatternEditorView(player: player)
        }
    }

    static var previews: some View {
        Wrapper()
            .preferredColorScheme(.dark)
    }
}
#endif
