// SequencerPanelView.swift
//
// MPC-style step sequencer panel — the desktop port of the iOS
// PatternEditorView: track rows with toggleable step cells, per-track
// mute/solo/preview/remove, pattern settings (steps / swing / BPM
// override), save/load against SequencerPatternStore and a standalone
// play/stop transport (the SequencerClock drives its own timing; the
// song transport is not involved).
//
// Track sources: synth chords (a C-major diatonic palette, playable
// with no song loaded) plus the loaded bundle's chop presets
// (harmonic, sections, …) via the add-track menu. Pack-pad / local
// sample patterns still load fine and route through the adapter.
//
// SequencerPlayer is an ObservableObject (ToneForgeEngine), so the
// editor observes it with @ObservedObject rather than @Observable.

import SwiftUI
import ToneForgeEngine
import JamDesktopCore

private func debugLog(_ msg: String) {
    let path = "/tmp/jamn-sequencer-debug.log"
    let line = "\(Date()): \(msg)\n"
    if let data = line.data(using: .utf8) {
        if FileManager.default.fileExists(atPath: path) {
            if let handle = FileHandle(forWritingAtPath: path) {
                handle.seekToEndOfFile()
                handle.write(data)
                handle.closeFile()
            }
        } else {
            FileManager.default.createFile(atPath: path, contents: data)
        }
    }
}

struct SequencerPanelView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var session: SessionController

    var body: some View {
        VStack(spacing: 0) {
            // Close button row
            HStack {
                Spacer()
                Button { dismiss() } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .keyboardShortcut(.escape, modifiers: [])
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)

            SequencerEditorView(
                player: session.sequencer,
                store: session.patternStore,
                padAssignmentStore: session.padAssignmentStore,
                presets: model.session?.bundle.presets ?? [:]
            )
            .padding(16)
        }
        .frame(minWidth: 1100, minHeight: 700)
        .background(JamTheme.background)
        .preferredColorScheme(.dark)
        .tint(JamTheme.accent)
        // Playback works with no song loaded (synth chords + pack pads
        // route to their own P5 players). player.play() alone doesn't
        // start the engine, so ensure it here.
        .onAppear { session.ensureEngineStarted() }
    }
}

private struct SequencerEditorView: View {
    @ObservedObject var player: SequencerPlayer
    var store: SequencerPatternStore
    var padAssignmentStore: PadAssignmentStore
    let presets: [String: BundlePreset]

    @State private var showPadPicker = false

    /// Force observation by reading patterns at body level.
    private var savedPatterns: [SequencerPattern] { store.all() }

    var body: some View {
        let _ = savedPatterns  // trigger @Observable tracking
        let _ = NSLog("[Sequencer] body rendering, tracks=%d", player.pattern.tracks.count)
        VStack(spacing: 12) {
            header
            Divider()
            if player.pattern.tracks.isEmpty {
                emptyState
            } else {
                trackList
            }
            Divider()
            transportRow
        }
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 12) {
            TextField("Pattern name", text: $player.pattern.name)
                .textFieldStyle(.roundedBorder)
                .frame(width: 180)

            Picker("Steps", selection: stepCountBinding) {
                ForEach(PatternStepCount.allCases, id: \.self) {
                    Text($0.label).tag($0)
                }
            }
            .pickerStyle(.segmented)
            .fixedSize()

            HStack(spacing: 6) {
                Text("Swing")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Slider(value: swingBinding, in: 0...0.5)
                    .frame(width: 90)
                Text("\(Int(player.pattern.swing * 200))%")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .frame(width: 36, alignment: .trailing)
            }

            bpmControl

            Spacer()

            savedPatternsMenu
        }
    }

    /// Song BPM by default; toggleable override with a stepper.
    private var bpmControl: some View {
        HStack(spacing: 6) {
            Toggle("BPM", isOn: bpmOverrideBinding)
                .toggleStyle(.checkbox)
                .font(.caption)
            if player.pattern.bpmOverride != nil {
                Stepper(
                    "\(Int(player.pattern.bpmOverride ?? 120))",
                    value: bpmValueBinding, in: 40...240, step: 1
                )
                .font(.caption.monospacedDigit())
            } else {
                Text("\(Int(player.songBPM)) (song)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var savedPatternsMenu: some View {
        HStack(spacing: 8) {
            Menu {
                Button("New Pattern") {
                    player.stop()
                    player.pattern = SequencerPattern()
                }
                if !savedPatterns.isEmpty {
                    Divider()
                    ForEach(savedPatterns) { saved in
                        Button(saved.name) {
                            player.stop()
                            player.pattern = saved
                        }
                    }
                    Divider()
                    Menu("Delete") {
                        ForEach(savedPatterns) { saved in
                            Button(saved.name, role: .destructive) {
                                store.delete(id: saved.id)
                            }
                        }
                    }
                }
            } label: {
                Label("Patterns", systemImage: "square.stack")
            }
            .fixedSize()

            let tracksEmpty = player.pattern.tracks.isEmpty
            let _ = NSLog("[Sequencer] Save button render, disabled=%d, tracks=%d", tracksEmpty ? 1 : 0, player.pattern.tracks.count)
            Button {
                NSLog("[Sequencer] SAVE TAPPED - pattern '%@' with %d tracks", player.pattern.name, player.pattern.tracks.count)
                store.save(player.pattern)
            } label: {
                Label("Save Pattern", systemImage: "square.and.arrow.down")
            }
            .disabled(tracksEmpty)
            .help("Save this pattern (re-saving updates in place)")

            Button {
                // Save first if needed, then show pad picker
                if !tracksEmpty {
                    store.save(player.pattern)
                }
                showPadPicker = true
            } label: {
                Label("Add to Pad", systemImage: "square.grid.3x3")
            }
            .disabled(tracksEmpty)
            .buttonStyle(.borderedProminent)
            .help("Assign this pattern to a pad on the grid")
        }
        .sheet(isPresented: $showPadPicker) {
            PadPickerSheet(
                patternId: player.pattern.id,
                patternName: player.pattern.name,
                padAssignmentStore: padAssignmentStore,
                onDismiss: { showPadPicker = false }
            )
        }
    }

    // MARK: - Empty state

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "waveform.badge.plus")
                .font(.system(size: 40))
                .foregroundStyle(.secondary)
            Text("No tracks yet")
                .font(.headline)
            Text("Add chords to build a pattern — load a song for its chops too.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            addTrackMenu(label: "Add Track")
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Tracks

    private var trackList: some View {
        ScrollView {
            VStack(spacing: 6) {
                ForEach(
                    Array(player.pattern.tracks.enumerated()),
                    id: \.element.id
                ) { index, track in
                    SequencerTrackRow(
                        track: track,
                        currentStep: player.currentStep,
                        isPlaying: player.isPlaying,
                        onToggleStep: { player.toggleStep(track: index, step: $0) },
                        onToggleMute: { player.toggleMute(track: index) },
                        onToggleSolo: { player.toggleSolo(track: index) },
                        onPreview: { player.previewTrack(index) },
                        onRemove: { player.removeTrack(at: index) }
                    )
                }
            }
        }
        .frame(maxHeight: .infinity)
    }

    // MARK: - Transport

    private var transportRow: some View {
        HStack(spacing: 16) {
            addTrackMenu(label: "Add Track")

            Spacer()

            Button {
                player.isPlaying ? player.stop() : player.play()
            } label: {
                Image(systemName: player.isPlaying ? "stop.fill" : "play.fill")
                    .font(.title2)
                    .foregroundStyle(player.isPlaying ? .red : .green)
                    .frame(width: 52, height: 34)
                    .background(
                        (player.isPlaying ? Color.red : Color.green).opacity(0.15),
                        in: RoundedRectangle(cornerRadius: 8)
                    )
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.space, modifiers: [])

            Spacer()

            HStack(spacing: 4) {
                Text("Step")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text("\(player.currentStep + 1)")
                    .font(.headline.monospacedDigit())
                    .frame(minWidth: 24)
            }
        }
    }

    /// Menu of the bundle's chop presets → chops. chopIndex is the
    /// position in idx-sorted order — the same order
    /// SequencerAudioAdapter resolves against.
    private func addTrackMenu(label: String) -> some View {
        Menu {
            // Synth chords work with no song loaded — route to the
            // DesktopSynthNode. A C-major diatonic palette is a sane
            // default starting point.
            Menu("Chords") {
                ForEach(Self.defaultChordSymbols, id: \.self) { symbol in
                    Button(symbol) {
                        player.addTrack(
                            for: .synthChord(symbol: symbol, octaveShift: 0),
                            name: symbol
                        )
                    }
                }
            }
            if !presets.isEmpty {
                Divider()
            }
            ForEach(presets.keys.sorted(), id: \.self) { key in
                let chops = presets[key]!.chops.sorted { $0.idx < $1.idx }
                Menu("\(key.capitalized) (\(chops.count))") {
                    ForEach(Array(chops.enumerated()), id: \.offset) { index, chop in
                        Button(chopLabel(chop, fallback: "\(key) \(index + 1)")) {
                            player.addTrack(
                                for: .bundleChop(
                                    presetKey: key, chopIndex: index,
                                    resolvedId: nil
                                ),
                                name: chopLabel(chop, fallback: "\(key) \(index + 1)")
                            )
                        }
                    }
                }
            }
        } label: {
            Label(label, systemImage: "plus")
        }
        .fixedSize()
    }

    /// C-major diatonic triads — a usable chord palette when no song is
    /// loaded (matches ChordParser-parseable symbols).
    private static let defaultChordSymbols =
        ["C", "Dm", "Em", "F", "G", "Am", "Bdim"]

    private func chopLabel(_ chop: Chop, fallback: String) -> String {
        chop.chordSymbol ?? chop.sectionLabel ?? fallback
    }

    // MARK: - Bindings

    /// Route through setStepCount so every track resizes with the
    /// pattern (a raw stepCount write would leave short step arrays).
    private var stepCountBinding: Binding<PatternStepCount> {
        Binding(
            get: { player.pattern.stepCount },
            set: { player.pattern.setStepCount($0) }
        )
    }

    private var swingBinding: Binding<Double> {
        Binding(
            get: { Double(player.pattern.swing) },
            set: { player.pattern.swing = Float($0) }
        )
    }

    private var bpmOverrideBinding: Binding<Bool> {
        Binding(
            get: { player.pattern.bpmOverride != nil },
            set: { player.pattern.bpmOverride = $0 ? player.songBPM : nil }
        )
    }

    private var bpmValueBinding: Binding<Double> {
        Binding(
            get: { player.pattern.bpmOverride ?? 120 },
            set: { player.pattern.bpmOverride = $0 }
        )
    }
}

// MARK: - Track row

private struct SequencerTrackRow: View {
    let track: SequencerTrack
    let currentStep: Int
    let isPlaying: Bool
    let onToggleStep: (Int) -> Void
    let onToggleMute: () -> Void
    let onToggleSolo: () -> Void
    let onPreview: () -> Void
    let onRemove: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            // Name + preview
            Button(action: onPreview) {
                HStack(spacing: 4) {
                    Image(systemName: "play.circle")
                        .font(.caption)
                    Text(track.name ?? "Track")
                        .font(.caption)
                        .lineLimit(1)
                }
            }
            .buttonStyle(.plain)
            .foregroundStyle(track.isMuted ? .secondary : .primary)
            .frame(width: 110, alignment: .leading)
            .help("Preview")

            // Mute / solo
            Toggle("M", isOn: .init(get: { track.isMuted }, set: { _ in onToggleMute() }))
                .toggleStyle(.button)
                .font(.caption2.bold())
                .tint(.orange)
            Toggle("S", isOn: .init(get: { track.isSoloed }, set: { _ in onToggleSolo() }))
                .toggleStyle(.button)
                .font(.caption2.bold())
                .tint(.yellow)

            // Step cells
            HStack(spacing: 3) {
                ForEach(track.steps.indices, id: \.self) { step in
                    stepCell(step)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Button(action: onRemove) {
                Image(systemName: "trash")
                    .font(.caption)
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .help("Remove track")
        }
        .padding(.vertical, 2)
    }

    private func stepCell(_ step: Int) -> some View {
        let data = track.steps[step]
        let isPlayhead = isPlaying && step == currentStep
        let isBeat = step % 4 == 0

        return RoundedRectangle(cornerRadius: 3)
            .fill(
                data.isActive
                    ? JamTheme.accent.opacity(0.35 + 0.65 * Double(data.velocity))
                    : Color.white.opacity(isBeat ? 0.12 : 0.06)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 3)
                    .strokeBorder(
                        isPlayhead ? Color.white : Color.white.opacity(0.1),
                        lineWidth: isPlayhead ? 1.5 : 0.5
                    )
            )
            .frame(width: 18, height: 26)
            .contentShape(Rectangle())
            .onTapGesture { onToggleStep(step) }
    }
}

// MARK: - Pad Picker Sheet

private struct PadPickerSheet: View {
    let patternId: UUID
    let patternName: String
    var padAssignmentStore: PadAssignmentStore
    let onDismiss: () -> Void

    @State private var hoveredPad: Int?

    var body: some View {
        VStack(spacing: 16) {
            HStack {
                Text("Add to Pad")
                    .font(.headline)
                Spacer()
                Button("Cancel") { onDismiss() }
                    .keyboardShortcut(.cancelAction)
            }

            Text("Select a pad to assign \"\(patternName)\"")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            // 8x8 grid
            VStack(spacing: 6) {
                ForEach(0..<8, id: \.self) { row in
                    HStack(spacing: 6) {
                        ForEach(0..<8, id: \.self) { col in
                            let padIdx = row * 8 + col
                            padCell(padIdx: padIdx)
                        }
                    }
                }
            }
            .padding(8)
            .background(Color.black.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
        }
        .padding(20)
        .frame(width: 380, height: 440)
        .background(JamTheme.background)
    }

    private func padCell(padIdx: Int) -> some View {
        let isAssigned = padAssignmentStore.slot(padIdx: padIdx) != nil
        let isHovered = hoveredPad == padIdx

        return RoundedRectangle(cornerRadius: 4)
            .fill(isAssigned ? Color.purple.opacity(0.5) : Color.white.opacity(0.08))
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .strokeBorder(
                        isHovered ? Color.white.opacity(0.6) : Color.white.opacity(0.1),
                        lineWidth: 1
                    )
            )
            .overlay {
                if isAssigned {
                    Image(systemName: "waveform")
                        .font(.caption2)
                        .foregroundStyle(.white.opacity(0.6))
                }
            }
            .frame(width: 36, height: 36)
            .shadow(color: isHovered ? JamTheme.accent.opacity(0.4) : .clear, radius: 6)
            .onHover { hoveredPad = $0 ? padIdx : nil }
            .onTapGesture {
                padAssignmentStore.assign(.sequence(patternId: patternId), padIdx: padIdx)
                onDismiss()
            }
    }
}
