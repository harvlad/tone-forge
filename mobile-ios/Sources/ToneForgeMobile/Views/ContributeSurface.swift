// ContributeSurface.swift
//
// The Contribute surface of the Play tab (redesign Phase 9),
// extracted from PlayView. Composition per the mockup:
//
//   - CategoryCards family chips (song loaded)
//   - one control row: [Instrument | Samples] switch → setMode(
//     .hybrid / .sample) + pack strip + stop-all + 8×8 toggle
//   - sections + record pill row (song loaded)
//   - the pad surface: named 4×4 SamplePadGrid4x4 in sample mode
//     (with a grid-icon toggle back to the advanced 8×8), the 8×8
//     hybrid grid in instrument mode
//   - quantize chips (+ record pill in the sketch context), sketch
//     tempo strip (no song), layer fader
//
// Pure composition — all audio still flows grid → bus → ModeRouter →
// ModeCoordinator; this view owns no engine logic.

import SwiftUI
import ToneForgeEngine

struct ContributeSurface: View {
    @ObservedObject var coordinator: ModeCoordinator
    @ObservedObject var sampleSettings: SampleSettingsStore
    @ObservedObject var sketchSettings: SketchSettingsStore
    /// Open the pack browser, optionally pre-filtered to a family
    /// (from a CategoryCards card).
    let onOpenBrowse: (SampleFamily?) -> Void
    @EnvironmentObject private var appState: AppState

    /// Sample mode's escape hatch to the advanced 8×8 quadrant grid
    /// (local assignments outside the pack quadrant live there).
    @State private var showAdvancedGrid = false
    /// Toggle between Pads and Sequencer in sample mode.
    @State private var showSequencer = false
    /// Arrange mode on the advanced 8×8 grid: drag pads to swap cells.
    @State private var arranging = false
    /// Beat Capture sheet (mic rhythm → drum pattern).
    @State private var showBeatCapture = false

    var body: some View {
        let hasSong = appState.currentBundle != nil

        // Control row: pad/sequencer + advanced-grid toggles.
        HStack(spacing: 8) {
            Spacer()
            if showAdvancedGrid {
                arrangeToggle
            }
            beatCaptureButton
            sequencerToggle
            advancedGridToggle
            stopAllButton
        }
        .padding(.horizontal, 12)
        .sheet(isPresented: $showBeatCapture) {
            BeatCaptureSheet(
                coordinator: coordinator,
                onOpenInSequencer: { id in
                    coordinator.setMode(.sample)
                    appState.pendingSequencerPatternId = id
                    showAdvancedGrid = false
                    showSequencer = true
                }
            )
        }

        if hasSong {
            // Sections + layer slot share one row (layers context).
            // Transport (play/stop/record) moved to quantize row.
            HStack(spacing: 0) {
                SectionChips(
                    sections: appState.currentBundle?.timeline.sections ?? [],
                    nowSongSeconds: appState.songSeconds,
                    allowedLabels: sampleSettings.sectionGates(
                        for: appState.currentBundle?.analysisId ?? ""
                    ),
                    onSeek: { t in appState.seekAndPlay(to: t) },
                    onGateToggle: { label in toggleGate(label: label) }
                )
                Spacer()
                LayerSlotToggle()
                    .padding(.trailing, 12)
            }
            .frame(height: 44)
        }

        padSurface

        // Quantize chips row.
        HStack(spacing: 8) {
            QuantizeControls(
                quantize: quantizeBinding,
                hold: $sampleSettings.holdMode,
                beatBar: $sampleSettings.beatBarMode
            )
            Spacer()
            if !hasSong {
                LayerSlotToggle()
            }
        }
        .padding(.horizontal, 12)

        if !hasSong {
            // Song-less synthetic grid (D-016): tempo/meter/click
            // for the quantize clock.
            TempoStrip(
                bpm: $sketchSettings.tempoBpm,
                timeSigNumerator: $sketchSettings.timeSigNumerator,
                metronomeEnabled: $sketchSettings.metronomeEnabled,
                countInEnabled: $sketchSettings.countInEnabled,
                positionLabel: sketchPositionLabel
            )
            .padding(.horizontal, 12)
        }

        LayerFader(dbValue: $sampleSettings.layerFaderDb)
    }

    // MARK: - Pad surface

    @ViewBuilder
    private var padSurface: some View {
        if coordinator.appMode == .sample && showSequencer {
            let bpm = appState.currentBundle?.meta.tempoBpm ?? sketchSettings.tempoBpm
            SequencerTabView(
                eventBus: appState.contributionBus,
                songBPM: bpm,
                currentBeat: appState.songSeconds * bpm / 60,
                isPlaying: appState.isPlaying,
                analysisId: appState.currentBundle?.analysisId ?? "",
                initialPatternId: appState.pendingSequencerPatternId
            )
            .padding(.horizontal, 12)
        } else if showAdvancedGrid {
            // 8x8 advanced grid for power users
            ModeGridView(coordinator: coordinator, arranging: arranging)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            // 4x4 grid for both Instrument and Sample modes
            SamplePadGrid4x4(coordinator: coordinator)
                .padding(.horizontal, 12)
        }
    }

    /// Entry point for Beat Capture (D-024): mic rhythm → drum pattern.
    private var beatCaptureButton: some View {
        Button {
            showBeatCapture = true
        } label: {
            Image(systemName: "figure.dance")
                .font(TFTheme.chipFont)
                .tfChip(active: false)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Beat Capture")
    }

    /// Toggle between Pads and Sequencer views in sample mode.
    private var sequencerToggle: some View {
        Button {
            showSequencer.toggle()
            if showSequencer { showAdvancedGrid = false }
        } label: {
            Image(systemName: showSequencer
                ? "pianokeys" : "slider.vertical.3")
                .font(TFTheme.chipFont)
                .tfChip(active: showSequencer)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(showSequencer
            ? "Switch to pad grid"
            : "Switch to sequencer")
    }

    /// Grid-icon toggle between the named 4×4 and the advanced 8×8
    /// quadrant grid in sample mode.
    private var advancedGridToggle: some View {
        Button {
            showAdvancedGrid.toggle()
            if showAdvancedGrid { showSequencer = false }
            // Arrange only lives on the 8×8; leaving it exits arrange.
            if !showAdvancedGrid { arranging = false }
        } label: {
            Image(systemName: showAdvancedGrid
                ? "square.grid.2x2" : "square.grid.4x3.fill")
                .font(TFTheme.chipFont)
                .tfChip(active: showAdvancedGrid)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(showAdvancedGrid
            ? "Switch to 4 by 4 pad grid"
            : "Switch to advanced 8 by 8 grid")
    }

    /// Arrange-mode toggle (advanced 8×8 only): drag pads to swap cells
    /// instead of playing them.
    private var arrangeToggle: some View {
        Button {
            arranging.toggle()
        } label: {
            Image(systemName: "arrow.up.and.down.and.arrow.left.and.right")
                .font(TFTheme.chipFont)
                .tfChip(active: arranging)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(arranging
            ? "Exit arrange mode"
            : "Arrange pads")
    }

    // MARK: - Stop-all

    /// Panic button: fades out every ringing sample voice across all
    /// packs. Lit while any *looping* voice rings (`ringingPadKeys`
    /// tracks loops only). Always tappable — one-shot tails aren't
    /// tracked (their slots stay active after the buffer ends), so the
    /// button must stay enabled to panic-stop them too.
    private var stopAllButton: some View {
        let hasRinging = !appState.ringingPadKeys.isEmpty
        return Button {
            appState.stopAllSamplePads()
        } label: {
            Image(systemName: "stop.circle.fill")
                .font(.title2)
                .foregroundStyle(hasRinging ? Color.accentColor : .secondary)
                .opacity(hasRinging ? 1 : 0.55)
        }
        .accessibilityLabel("Stop all pads")
    }

    // MARK: - Quantize source switch (D-016)

    /// Bundle loaded → song quantize setting; no bundle → the sketch
    /// (synthetic-grid) quantize setting. The stores' own sinks push
    /// the change into the scheduler for whichever context is live.
    private var quantizeBinding: Binding<QuantizeMode> {
        Binding(
            get: {
                appState.currentBundle != nil
                    ? sampleSettings.quantizeMode
                    : sketchSettings.quantizeMode
            },
            set: { newValue in
                if appState.currentBundle != nil {
                    sampleSettings.quantizeMode = newValue
                } else {
                    sketchSettings.quantizeMode = newValue
                }
            }
        )
    }

    // MARK: - Sketch position readout

    /// "bar.beat" (1-based) for the TempoStrip while the sketch
    /// transport is running; "Count-in" through the negative lead
    /// bar; nil (hidden) when the transport is parked at 0.
    private var sketchPositionLabel: String? {
        let s = appState.songSeconds
        if s < 0 { return "Count-in" }
        guard appState.isPlaying || s > 0 else { return nil }
        let beatDur = 60.0 / sketchSettings.tempoBpm
        let beats = Int(s / beatDur)
        let bar = beats / sketchSettings.timeSigNumerator + 1
        let beat = beats % sketchSettings.timeSigNumerator + 1
        return "\(bar).\(beat)"
    }

    // MARK: - Gate toggle

    /// Long-press on a section chip toggles it into/out of the
    /// per-song allowlist. `nil` allowed → all allowed; adding first
    /// label switches to explicit allowlist mode.
    private func toggleGate(label: String) {
        guard let bundle = appState.currentBundle else { return }
        var current = sampleSettings.sectionGates(for: bundle.analysisId)
            ?? Set(uniqueSectionLabels())
        if current.contains(label) {
            current.remove(label)
        } else {
            current.insert(label)
        }
        // If the resulting set equals all labels, treat as "allow all"
        // and clear the entry to keep persistence compact.
        let all = Set(uniqueSectionLabels())
        if current == all {
            appState.setSectionGates(nil)
        } else {
            appState.setSectionGates(current)
        }
    }

    private func uniqueSectionLabels() -> [String] {
        SectionResolver.uniqueLabels(in:
            appState.currentBundle?.timeline.sections ?? []
        )
    }
}
