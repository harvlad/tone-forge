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
    /// Family pre-filter for the pack browser (set by CategoryCards,
    /// nil for the plain PackPicker "open browser" path).
    @State private var browseFamily: SampleFamily?

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

            switch currentSurface {
            case .jam:
                JamView(
                    coordinator: coordinator,
                    jamSettings: appState.jamSettings,
                    controller: appState.jamController
                )
            case .learn:
                LearnView(controller: appState.learnController)
            default:
                ContributeSurface(
                    coordinator: coordinator,
                    sampleSettings: sampleSettings,
                    sketchSettings: sketchSettings,
                    onOpenBrowse: { family in
                        browseFamily = family
                        showBrowse = true
                    }
                )
            }

            transportRow

            masterVolumeRow

            if currentSurface == .contribute {
                howItWorksFooter
            }
        }
        .padding(.top, 8)
        .padding(.bottom, 40)
        .background(TFTheme.background.ignoresSafeArea())
        .sheet(isPresented: $showMixer) { MixerView() }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .sheet(isPresented: $showBrowse) {
            BrowsePacksSheet(initialFamily: browseFamily)
        }
        .sheet(isPresented: $showHelp) { HelpSheet() }
        .onAppear { applySurface(currentSurface) }
    }

    // MARK: - Surface switcher (D-018)

    /// The persisted PlaySurface selection, coerced to an implemented
    /// surface (raw values from newer builds fall back to Contribute).
    private var currentSurface: PlaySurface {
        let s = PlaySurface(rawValue: sampleSettings.playSurfaceRaw)
            ?? .contribute
        return s.isImplemented ? s : .contribute
    }

    private var surfaceBinding: Binding<PlaySurface> {
        Binding(
            get: { currentSurface },
            set: { s in
                sampleSettings.playSurfaceRaw = s.rawValue
                applySurface(s)
            }
        )
    }

    /// Map a surface onto the engine AppMode. Jam drives the grid via
    /// .jamInKey; Contribute restores the last sample/hybrid mode the
    /// user was in. setMode no-ops when the mode is already active,
    /// so calling this from onAppear is idempotent.
    private func applySurface(_ s: PlaySurface) {
        // Leaving Learn mid-practice ends the pass (clears the A/B
        // loop, persists the streak) so the loop doesn't keep
        // wrapping under another surface.
        if s != .learn,
           appState.learnController.phase == .practicing {
            appState.learnController.stopPractice()
        }
        switch s {
        case .learn:
            coordinator.setMode(.learnSong)
        case .jam:
            coordinator.setMode(.jamInKey)
        case .contribute:
            if let last = AppMode(rawValue: sampleSettings.lastContributeModeRaw),
               last == .sample || last == .hybrid {
                coordinator.setMode(last)
            } else {
                coordinator.setMode(.sample)
            }
        case .chordPads:
            break // surface lands in Phase 12
        }
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
}
