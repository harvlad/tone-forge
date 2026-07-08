// ContributeSurface.swift
//
// The Contribute surface of the Play tab (redesign Phase 9),
// extracted from PlayView. Composition per the mockup:
//
//   - CategoryCards family chips (song loaded)
//   - one control row: [Instrument | Samples] switch → setMode(
//     .hybrid / .sample) + pack strip + stop-all + 8×8 toggle
//   - sections + record pill row (song) / record pill (sketch)
//   - the pad surface: named 4×4 SamplePadGrid4x4 in sample mode
//     (with a grid-icon toggle back to the advanced 8×8), the 8×8
//     hybrid grid in instrument mode
//   - quantize chips, sketch tempo strip (no song), layer fader
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

    var body: some View {
        let hasSong = appState.currentBundle != nil

        if hasSong {
            CategoryCards { family in
                onOpenBrowse(family)
            }
        }

        // Single control row (was three): mode segments + pack strip
        // + stop-all + the sample-mode grid toggle. Merged so the tab
        // fits a phone screen without scrolling.
        HStack(spacing: 8) {
            contributeModeSegment(title: "Instrument", mode: .hybrid)
            contributeModeSegment(title: "Samples", mode: .sample)
            PackPicker(
                pages: appState.carouselPages,
                activePackId: appState.activeSamplePack?.pack.packId,
                onSelect: { appState.activateCarouselPage(packId: $0) },
                onOpen: { onOpenBrowse(nil) }
            )
            stopAllButton
            if coordinator.appMode == .sample {
                advancedGridToggle
            }
        }
        .padding(.horizontal, 12)

        if hasSong {
            // Sections + record pill share one row (layers context).
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
                RecordToggle()
                    .fixedSize(horizontal: true, vertical: false)
                    .padding(.trailing, 12)
            }
            .frame(height: 44)
        } else {
            // Sketch context: record pill on its own row.
            RecordToggle()
                .padding(.horizontal, 12)
        }

        padSurface

        QuantizeControls(
            quantize: quantizeBinding,
            hold: $sampleSettings.holdMode,
            beatBar: $sampleSettings.beatBarMode
        )
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
        }

        LayerFader(dbValue: $sampleSettings.layerFaderDb)
    }

    // MARK: - Pad surface

    @ViewBuilder
    private var padSurface: some View {
        if coordinator.appMode == .sample && !showAdvancedGrid {
            SamplePadGrid4x4(coordinator: coordinator)
                .padding(.horizontal, 12)
        } else {
            ModeGridView(coordinator: coordinator)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    // MARK: - Instrument | Samples switch

    private func contributeModeSegment(title: String, mode: AppMode) -> some View {
        Button {
            coordinator.setMode(mode)
        } label: {
            Text(title)
                .font(TFTheme.chipFont)
                .tfChip(active: coordinator.appMode == mode)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(title) mode")
    }

    /// Grid-icon toggle between the named 4×4 and the advanced 8×8
    /// quadrant grid in sample mode.
    private var advancedGridToggle: some View {
        Button {
            showAdvancedGrid.toggle()
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

    // MARK: - Stop-all

    /// Panic button: fades out every ringing sample voice across all
    /// packs. Lit while any *looping* voice rings (`ringingPadKeys`
    /// tracks loops only — one-shot tails decay on their own); dimmed
    /// + disabled otherwise, so it doubles as an "anything still
    /// looping?" indicator.
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
