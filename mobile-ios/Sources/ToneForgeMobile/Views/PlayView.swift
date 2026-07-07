// PlayView.swift
//
// The Play tab — the single contribution surface. The old
// Learn/Jam/Contribute segmented picker and the separate Sketch tab
// are gone (D-016): the tab hosts one 8×8 ModeGridView whose behavior
// is chosen by AppMode on the ModeCoordinator (Sample / Hybrid live;
// five more coming), and "sketch" is simply this same surface with no
// song loaded — the song-specific chrome (scrubber, section chips,
// record toggle) hides, and the TempoStrip's synthetic grid appears.
//
// This file is the *shell* — it composes the panels but owns none of
// the audio logic. All input flows grid → ContributionEventBus →
// ModeCoordinator; this view is pure paint plus a few settings
// bindings.

import SwiftUI
import ToneForgeEngine

public struct PlayView: View {
    @EnvironmentObject private var appState: AppState

    public init() {}

    public var body: some View {
        // Indirection so the view observes the coordinator's and the
        // settings stores' @Published state (AppState doesn't
        // republish nested ObservableObjects).
        PlayBody(
            coordinator: appState.modeCoordinator,
            sampleSettings: appState.sampleSettings,
            sketchSettings: appState.sketchSettings
        )
    }
}

private struct PlayBody: View {
    @ObservedObject var coordinator: ModeCoordinator
    @ObservedObject var sampleSettings: SampleSettingsStore
    @ObservedObject var sketchSettings: SketchSettingsStore
    @EnvironmentObject private var appState: AppState

    @State private var showMixer: Bool = false
    @State private var showSettings: Bool = false
    @State private var showBrowse: Bool = false
    @State private var showHelp: Bool = false

    var body: some View {
        let hasSong = appState.currentBundle != nil

        VStack(spacing: 12) {
            NowPlayingHeader(
                title: appState.currentBundle?.meta.title ?? "No song loaded",
                artist: appState.currentBundle?.meta.artist,
                durationSec: appState.currentBundle?.meta.durationSec,
                keyLabel: appState.currentBundle?.meta.detectedKey,
                tempoBpm: appState.currentBundle?.meta.tempoBpm,
                analysisId: appState.currentBundle?.analysisId,
                onEject: hasSong ? { appState.ejectSong() } : nil
            )

            if hasSong {
                WaveformScrubber(
                    songSeconds: appState.songSeconds,
                    durationSec: appState.currentBundle?.meta.durationSec ?? 0,
                    peaks: appState.waveformPeaks,
                    onSeek: { s in appState.seek(to: s) }
                )
                .frame(height: 48)
            }

            ModeTabsRow(surface: surfaceBinding)

            if hasSong {
                CategoryCards { _ in
                    // Family pre-filter wires up in Phase 10; for now
                    // every card opens the pack browser.
                    showBrowse = true
                }
            }

            HStack(spacing: 8) {
                modeMenu
                PackPicker(
                    pages: appState.carouselPages,
                    activePackId: appState.activeSamplePack?.pack.packId,
                    onSelect: { appState.activateCarouselPage(packId: $0) },
                    onOpen: { showBrowse = true }
                )
                stopAllButton
            }
            .padding(.horizontal, 12)

            if hasSong {
                SectionChips(
                    sections: appState.currentBundle?.timeline.sections ?? [],
                    nowSongSeconds: appState.songSeconds,
                    allowedLabels: appState.sampleSettings.sectionGates(
                        for: appState.currentBundle?.analysisId ?? ""
                    ),
                    onSeek: { t in appState.seekAndPlay(to: t) },
                    onGateToggle: { label in toggleGate(label: label) }
                )
                .frame(height: 44)
            }

            // Both contexts: layers with a song, sketches without.
            RecordToggle()

            ModeGridView(coordinator: coordinator)
                .frame(maxWidth: .infinity, maxHeight: .infinity)

            QuantizeControls(
                quantize: quantizeBinding,
                hold: $sampleSettings.holdMode,
                beatBar: $sampleSettings.beatBarMode
            )

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

            transportRow

            masterVolumeRow

            howItWorksFooter
        }
        .padding(.top, 8)
        .padding(.bottom, 40)
        .background(TFTheme.background.ignoresSafeArea())
        .sheet(isPresented: $showMixer) { MixerView() }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .sheet(isPresented: $showBrowse) { BrowsePacksSheet() }
        .sheet(isPresented: $showHelp) { HelpSheet() }
    }

    // MARK: - Surface switcher (D-018)

    /// Persisted PlaySurface selection. Only `.contribute` is live
    /// today, so nothing beyond persistence hangs off this yet —
    /// Jam/Learn/Chord Pads surfaces branch on it as their phases
    /// land.
    private var surfaceBinding: Binding<PlaySurface> {
        Binding(
            get: {
                PlaySurface(rawValue: sampleSettings.playSurfaceRaw)
                    ?? .contribute
            },
            set: { sampleSettings.playSurfaceRaw = $0.rawValue }
        )
    }

    // MARK: - Master volume

    private var masterVolumeRow: some View {
        HStack(spacing: 10) {
            Image(systemName: "speaker.fill")
                .font(.caption)
                .foregroundStyle(TFTheme.textSecondary)
            Slider(value: masterGainBinding, in: 0...1)
            Image(systemName: "speaker.wave.3.fill")
                .font(.caption)
                .foregroundStyle(TFTheme.textSecondary)
        }
        .padding(.horizontal, 20)
        .accessibilityLabel("Master volume")
    }

    private var masterGainBinding: Binding<Double> {
        Binding(
            get: { appState.masterGain },
            set: { appState.setMasterGain($0) }
        )
    }

    // MARK: - How-it-works footer

    private var howItWorksFooter: some View {
        Button {
            showHelp = true
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "questionmark.circle")
                Text("How does contributing work?")
            }
            .font(.caption)
            .foregroundStyle(TFTheme.textSecondary)
        }
        .buttonStyle(.plain)
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

    // MARK: - Mode menu

    /// AppMode selector. Unimplemented modes are visible but disabled
    /// so the seven-mode roadmap is discoverable.
    private var modeMenu: some View {
        Menu {
            ForEach(AppMode.allCases, id: \.rawValue) { mode in
                Button {
                    coordinator.setMode(mode)
                } label: {
                    if mode == coordinator.appMode {
                        Label(mode.displayName, systemImage: "checkmark")
                    } else if mode.isImplemented {
                        Text(mode.displayName)
                    } else {
                        Text("\(mode.displayName) — Coming soon")
                    }
                }
                .disabled(!mode.isImplemented)
            }
        } label: {
            HStack(spacing: 4) {
                Text(coordinator.appMode.displayName)
                    .font(.subheadline.weight(.semibold))
                Image(systemName: "chevron.up.chevron.down")
                    .font(.caption2)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(.thinMaterial, in: Capsule())
        }
        .accessibilityLabel("Mode: \(coordinator.appMode.displayName)")
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

    // MARK: - Transport

    private var transportRow: some View {
        HStack(spacing: 24) {
            Button {
                appState.seek(to: max(0, appState.songSeconds - 5))
            } label: {
                Image(systemName: "gobackward.5").font(.title2)
            }
            Button {
                appState.togglePlayPause()
            } label: {
                Image(systemName: appState.isPlaying ? "pause.circle.fill" : "play.circle.fill")
                    .font(.system(size: 44))
            }
            Button {
                appState.seek(to: appState.songSeconds + 5)
            } label: {
                Image(systemName: "goforward.5").font(.title2)
            }
            Spacer()
            Button {
                showMixer = true
            } label: {
                Image(systemName: "slider.horizontal.3").font(.title2)
            }
            Button {
                showSettings = true
            } label: {
                Image(systemName: "gearshape").font(.title2)
            }
        }
        .padding(.horizontal, 20)
    }

    // MARK: - Gate toggle

    /// Long-press on a section chip toggles it into/out of the
    /// per-song allowlist. `nil` allowed → all allowed; adding first
    /// label switches to explicit allowlist mode.
    private func toggleGate(label: String) {
        guard let bundle = appState.currentBundle else { return }
        var current = appState.sampleSettings.sectionGates(for: bundle.analysisId)
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
