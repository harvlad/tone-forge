// SequenceBuilderSheet.swift
//
// 4x4 Launchpad-style sequence builder. Opened from a pad's radial
// menu ("Sequence"). Song-aware but not song-dependent: the grid taps
// address one of three sources —
//
//   - Pads       one sample pack's pads
//   - Song Chords the loaded song's harmonic chops (needs a song)
//   - Key Chords  diatonic triads of a musical key (works with no song)
//
// Switch source mid-build without losing recorded tracks. With no song,
// Record runs an internal metronome clock so key-chord loops still build.
// Save persists the pattern and auto-assigns it to the pad that opened
// the builder.
//
// Cell index (0..15) is top-left row-major, matching pack padIdx and
// the harmonic chop ordering: cellIdx = (4 - row)*4 + (col - 1), where
// PadTouchOverlay gives row 1 = bottom.

import SwiftUI
import ToneForgeEngine

struct SequenceBuilderSheet: View {
    /// The grid pad (8×8 PadIndex coords) that opened the builder.
    let gridRow: Int
    let gridCol: Int

    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss
    @StateObject private var recorder = SequenceRecorder()

    @State private var source: SequenceRecorder.PadSource = .keyChords
    @State private var loopLength: SequenceRecorder.LoopLength = .oneBar
    @State private var key = MusicalKey(root: PitchClass(0), scale: .major)
    @State private var name: String = "Sequence"
    @State private var didConfigure = false
    @State private var showKeyPicker = false

    /// Editing a pad's already-saved sequence (loaded + looping) vs
    /// recording a fresh one.
    @State private var isEditingExisting = false
    /// Standalone loop player for in-place editing. Present only in edit
    /// mode; taps overdub into it at the current playhead step.
    @State private var previewPlayer: SequencerPlayer?
    /// Mirrored preview state (SequencerPlayer isn't observed here).
    @State private var previewPlaying = false
    @State private var previewStep = 0

    private static let chordColor = TFTheme.color(hex: 0x30D5C8)

    var body: some View {
        NavigationStack {
            VStack(spacing: 14) {
                controls
                grid
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                transport
            }
            .padding(16)
            .background(TFTheme.background.ignoresSafeArea())
            .navigationTitle("Sequence Builder")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }
                        .disabled(recorder.isEmpty)
                }
            }
        }
        .onAppear(perform: configureIfNeeded)
        .onDisappear { stopPreview() }
        .onReceive(appState.$songSeconds) { t in
            recorder.tick(songSeconds: t)
        }
        .onReceive(Timer.publish(every: 1.0 / 30.0, on: .main, in: .common).autoconnect()) { _ in
            let playing = previewPlayer?.isPlaying ?? false
            if playing { previewStep = previewPlayer?.currentStep ?? 0 }
            if previewPlaying != playing { previewPlaying = playing }
        }
        .sheet(isPresented: $showKeyPicker) {
            SequenceKeyPickerSheet(key: key) { newKey in
                key = newKey
                recorder.setKeyChords(keyChordSymbols)
            }
        }
    }

    // MARK: - Controls

    private var controls: some View {
        VStack(spacing: 10) {
            TextField("Name", text: $name)
                .textFieldStyle(.roundedBorder)
                .onChange(of: name) { _, new in recorder.setName(new) }

            if availableSources.count > 1 {
                Picker("Source", selection: $source) {
                    ForEach(availableSources, id: \.self) { src in
                        Text(sourceLabel(src)).tag(src)
                    }
                }
                .pickerStyle(.segmented)
                .onChange(of: source) { _, new in recorder.setSource(new) }
            }

            if source == .keyChords {
                HStack(spacing: 10) {
                    Button {
                        showKeyPicker = true
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "pianokeys")
                            Text("Key: \(keyLabel)")
                            Image(systemName: "pencil")
                                .font(.caption2)
                        }
                        .font(.subheadline.weight(.semibold))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(Self.chordColor.opacity(0.18), in: Capsule())
                    }
                    .buttonStyle(.plain)

                    octaveStepper
                }
            }

            Picker("Loop", selection: $loopLength) {
                Text("1 Bar").tag(SequenceRecorder.LoopLength.oneBar)
                Text("2 Bars").tag(SequenceRecorder.LoopLength.twoBar)
                Text("Section").tag(SequenceRecorder.LoopLength.section)
            }
            .pickerStyle(.segmented)
            .onChange(of: loopLength) { _, new in
                // Skip the programmatic set from loading an existing pattern
                // (setLoopLength is destructive; it would wipe the load).
                guard new != recorder.loopLength else { return }
                recorder.setLoopLength(new, sectionSteps: sectionSteps())
            }

            Picker("Capture", selection: $recorder.captureMode) {
                Text("Snap").tag(SequenceRecorder.CaptureMode.quantized)
                Text("Free").tag(SequenceRecorder.CaptureMode.free)
            }
            .pickerStyle(.segmented)
        }
    }

    /// Octave transpose for the key-chord synth voice, −3…+3. Re-voices
    /// the whole loop and the live preview when changed.
    private var octaveStepper: some View {
        HStack(spacing: 8) {
            Image(systemName: "arrow.up.arrow.down")
                .font(.caption2)
            Button {
                setOctave(recorder.octaveShift - 1)
            } label: {
                Image(systemName: "minus")
            }
            .disabled(recorder.octaveShift <= -3)

            Text(octaveLabel)
                .font(.subheadline.weight(.semibold).monospacedDigit())
                .frame(minWidth: 28)

            Button {
                setOctave(recorder.octaveShift + 1)
            } label: {
                Image(systemName: "plus")
            }
            .disabled(recorder.octaveShift >= 3)
        }
        .font(.subheadline.weight(.semibold))
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(Self.chordColor.opacity(0.18), in: Capsule())
        .buttonStyle(.plain)
    }

    private var octaveLabel: String {
        let o = recorder.octaveShift
        return o > 0 ? "+\(o)" : "\(o)"
    }

    private func setOctave(_ shift: Int) {
        recorder.setOctaveShift(shift)
        // Push the re-voiced pattern into the live preview loop if editing.
        previewPlayer?.pattern = recorder.pattern
    }

    /// Current playhead step: preview loop while editing, record clock
    /// while recording, -1 when idle. Drives the Launchpad-style flash
    /// that runs the sequence THROUGH the pads.
    private var playheadStep: Int {
        previewPlaying
            ? previewStep
            : (recorder.isRecording ? recorder.currentStep : -1)
    }

    // MARK: - Grid

    private var grid: some View {
        GeometryReader { _ in
            ZStack {
                #if canImport(UIKit)
                PadTouchOverlay(
                    rows: 4,
                    cols: 4,
                    onPadDown: { row, col in handleDown(row: row, col: col) },
                    onPadUp: { _, _ in },
                    onLongPress: { _, _ in }
                )
                #endif
                tiles.allowsHitTesting(false)
            }
        }
    }

    private var tiles: some View {
        VStack(spacing: 6) {
            ForEach(0..<4, id: \.self) { screenRow in
                HStack(spacing: 6) {
                    ForEach(0..<4, id: \.self) { screenCol in
                        let cellIdx = screenRow * 4 + screenCol
                        cellTile(cellIdx: cellIdx)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func cellTile(cellIdx: Int) -> some View {
        let info = cellInfo(cellIdx)
        let recorded = recorder.hasSteps(cell: cellIdx)
        // Launchpad flash: this cell fires on the current playhead step.
        let firing = playheadStep >= 0
            && recorder.fires(cell: cellIdx, step: playheadStep)
        let tint = info?.tint ?? Self.chordColor
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 10)
                .fill(fillStyle(info: info, recorded: recorded, firing: firing))
            if let info {
                Text(info.label)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .padding(8)
            }
            if recorded {
                Circle()
                    .fill(tint)
                    .frame(width: 8, height: 8)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
                    .padding(8)
            }
        }
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(firing ? Color.white : TFTheme.stroke,
                        lineWidth: firing ? 2 : 1)
        )
        .shadow(color: firing ? tint.opacity(0.9) : .clear, radius: firing ? 8 : 0)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .animation(.easeOut(duration: 0.08), value: firing)
    }

    private func fillStyle(
        info: CellInfo?, recorded: Bool, firing: Bool
    ) -> AnyShapeStyle {
        guard let info else { return AnyShapeStyle(TFTheme.chipFill) }
        let opacity = firing ? 0.85 : (recorded ? 0.32 : 0.16)
        return AnyShapeStyle(info.tint.opacity(opacity))
    }

    // MARK: - Transport

    private var transport: some View {
        VStack(spacing: 8) {
            if let hint = transportHint {
                Text(hint)
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
                    .multilineTextAlignment(.center)
            }
            HStack(spacing: 12) {
                Button(action: toggleTransport) {
                    Label(transportLabel, systemImage: transportIcon)
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(transportActive ? .red : Self.chordColor)

                Button(action: clearPattern) {
                    Label("Clear", systemImage: "trash")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(recorder.isEmpty)
            }
        }
    }

    // MARK: - Actions

    private func handleDown(row: Int, col: Int) {
        let cellIdx = (4 - row) * 4 + (col - 1)
        if let ref = recorder.chopRef(forCell: cellIdx) {
            appState.previewChopReference(ref)
        }
        if let player = previewPlayer, player.isPlaying {
            // Edit mode: overdub into the looping pattern at the playhead.
            recorder.captureAtStep(cell: cellIdx, step: player.currentStep)
            player.pattern = recorder.pattern
        } else if recorder.isRecording {
            recorder.capture(cell: cellIdx)
        }
    }

    // MARK: - Transport helpers

    private var transportActive: Bool {
        isEditingExisting ? previewPlaying : recorder.isRecording
    }

    private var transportLabel: String {
        if isEditingExisting { return previewPlaying ? "Stop" : "Play" }
        return recorder.isRecording ? "Stop" : "Record"
    }

    private var transportIcon: String {
        if isEditingExisting { return previewPlaying ? "stop.fill" : "play.fill" }
        return recorder.isRecording ? "stop.fill" : "record.circle"
    }

    private var transportHint: String? {
        if isEditingExisting {
            return "Editing live — tap pads to add hits to the loop."
        }
        if !appState.isPlaying {
            return "No song playing — recording to an internal metronome."
        }
        return nil
    }

    private func toggleTransport() {
        if isEditingExisting {
            togglePreview()
        } else if recorder.isRecording {
            recorder.stopRecording()
        } else {
            recorder.startRecording(
                useSongClock: appState.isPlaying,
                atSongSeconds: appState.songSeconds,
                bpm: songBPM
            )
        }
    }

    private func clearPattern() {
        recorder.clear()
        previewPlayer?.pattern = recorder.pattern
    }

    // MARK: - Preview loop

    /// Build a standalone player over the current pattern and start it.
    private func startPreview() {
        let player = SequencerPlayer(
            pattern: recorder.pattern,
            eventBus: appState.contributionBus
        )
        player.delegate = appState
        player.songBPM = songBPM
        previewPlayer = player
        player.play(sync: false)
        previewPlaying = true
    }

    private func stopPreview() {
        previewPlayer?.stop()
        previewPlaying = false
    }

    private func togglePreview() {
        if previewPlaying { stopPreview() } else { startPreview() }
    }

    private func save() {
        let pattern = recorder.finalize(name: name)
        appState.sequencerPatternStore.save(pattern)
        appState.modeCoordinator.assignSequence(
            targetRow: gridRow,
            targetCol: gridCol,
            patternId: pattern.id
        )
        dismiss()
    }

    // MARK: - Configuration

    private func configureIfNeeded() {
        guard !didConfigure else { return }
        didConfigure = true
        key = MusicalKey.parse(appState.currentBundle?.meta.detectedKey)
            ?? MusicalKey(root: PitchClass(0), scale: .major)
        source = defaultSource
        recorder.configure(
            loopLength: loopLength,
            songBPM: songBPM,
            sectionSteps: sectionSteps(),
            packId: appState.activeSamplePack?.pack.packId,
            padCount: min(16, packPads.count),
            songChordCount: min(16, harmonicChops.count),
            keyChordSymbols: keyChordSymbols,
            initialSource: source
        )

        // If this pad already has a saved sequence, load it for in-place
        // editing and start looping so the editor matches the audio.
        if let existingId = appState.modeCoordinator.assignedSequenceId(
                row: gridRow, col: gridCol),
           let existing = appState.sequencerPatternStore.pattern(id: existingId) {
            recorder.load(existing)
            name = existing.name
            loopLength = recorder.loopLength
            source = recorder.activeSource
            isEditingExisting = true
            startPreview()
        }
    }

    // MARK: - Source data

    private var harmonicChops: [Chop] {
        appState.currentBundle?.presets["harmonic"]?.chops ?? []
    }

    private var packPads: [SamplePad] {
        appState.activeSamplePack?.pack.pads ?? []
    }

    private var hasChords: Bool { !harmonicChops.isEmpty }
    private var hasPack: Bool { appState.activeSamplePack != nil }

    /// Diatonic triad symbols for the current key (7 chords).
    private var keyChordSymbols: [String] {
        DiatonicChords.triads(key: key).map(\.symbol)
    }

    /// Sources offered, availability-gated. Key Chords always present.
    private var availableSources: [SequenceRecorder.PadSource] {
        var out: [SequenceRecorder.PadSource] = []
        if hasPack { out.append(.pads) }
        if hasChords { out.append(.songChords) }
        out.append(.keyChords)
        return out
    }

    private var defaultSource: SequenceRecorder.PadSource {
        if hasChords { return .songChords }
        if hasPack { return .pads }
        return .keyChords
    }

    private func sourceLabel(_ src: SequenceRecorder.PadSource) -> String {
        switch src {
        case .pads: return "Pads"
        case .songChords: return "Song Chords"
        case .keyChords: return "Key Chords"
        }
    }

    private var keyLabel: String {
        let root = NoteNames.name(pitchClass: key.root.rawValue, key: key)
        return "\(root) \(key.scale == .major ? "Major" : "Minor")"
    }

    private var songBPM: Double {
        appState.currentBundle?.meta.tempoBpm ?? 120
    }

    private func sectionSteps() -> Int {
        guard let bundle = appState.currentBundle else { return 16 }
        let t = appState.songSeconds
        let section = bundle.timeline.sections.first { $0.start <= t && t < $0.end }
        let start = section?.start ?? appState.currentChord?.start ?? 0
        let end = section?.end ?? appState.currentChord?.end ?? (start + 4)
        let bars = BarMath.barCount(
            start: start, end: end,
            downbeats: bundle.timeline.downbeats,
            tempoBpm: bundle.meta.tempoBpm,
            beatsPerBar: 4
        )
        return max(16, min(32, max(1, bars) * 16))
    }

    // MARK: - Tile info

    private struct CellInfo {
        let label: String
        let tint: Color
    }

    private func cellInfo(_ cellIdx: Int) -> CellInfo? {
        switch recorder.activeSource {
        case .songChords:
            guard cellIdx < harmonicChops.count else { return nil }
            let chop = harmonicChops[cellIdx]
            return CellInfo(
                label: chop.chordSymbol ?? "#\(cellIdx + 1)",
                tint: Self.chordColor
            )
        case .pads:
            guard let pad = packPads.first(where: { $0.padIdx == cellIdx }) else { return nil }
            return CellInfo(
                label: pad.name,
                tint: TFTheme.familyTint(pad.family)
            )
        case .keyChords:
            let symbols = keyChordSymbols
            guard cellIdx < symbols.count else { return nil }
            return CellInfo(label: symbols[cellIdx], tint: Self.chordColor)
        }
    }
}
