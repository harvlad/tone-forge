// RootView.swift
//
// Top-level view switcher: four views in parity with jam.js
// showView(), plus Studio (studio.html results deep-dive).

import SwiftUI
import JamDesktopCore
import ToneForgeEngine

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var session: SessionController

    @StateObject private var intake = IntakeModel()
    @StateObject private var history = HistoryModel()
    @StateObject private var queue = AnalysisQueueModel()
    @StateObject private var studio = StudioModel()
    @State private var showLaunchpad = false
    @State private var showSequencer = false
    @State private var showRecordings = false
    @State private var showJamPads = false
    @State private var showPacks = false
    @State private var showBeatCapture = false
    @State private var showVocoder = false
    @State private var contributeMode: ContributeMode = .beat

    // Local @State for the picker to avoid @Bindable capture issues.
    // Syncs bidirectionally with model.view via onChange.
    @State private var selectedView: JamView = .intake

    var body: some View {
        HStack(spacing: 0) {
            // Left sidebar (only on Intake view)
            if model.view == .intake {
                SidebarView(
                    selectedMode: $contributeMode,
                    onSongTap: loadSong,
                    onVoiceTap: { showVocoder = true },
                    onBeatTap: { showBeatCapture = true },
                    onSampleTap: { showLaunchpad = true },
                    onLaunchpadTap: { showLaunchpad = true },
                    onSequencerTap: { showSequencer = true },
                    onRecordingsTap: { showRecordings = true },
                    onJamPadsTap: { showJamPads = true },
                    onPacksTap: { showPacks = true },
                    onViewAllSongs: { model.view = .bandRoom }
                )
                .environmentObject(history)
                .environmentObject(model)

                // Separator
                Rectangle()
                    .fill(Color.white.opacity(0.08))
                    .frame(width: 1)
            }

            // Main content
            mainContent
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(JamTheme.background)
        .foregroundStyle(JamTheme.textPrimary)
        .environmentObject(intake)
        .environmentObject(history)
        .environmentObject(queue)
        .environmentObject(studio)
        .environmentObject(session)
        .onChange(of: model.view, initial: true) { _, newValue in
            selectedView = newValue
        }
        .onChange(of: selectedView) { _, newValue in
            model.view = newValue
        }
        .toolbar {
            viewSwitcher
            ToolbarItem(placement: .automatic) {
                if queue.activeCount > 0 {
                    Button {
                        model.view = .bandRoom
                    } label: {
                        HStack(spacing: 6) {
                            ProgressView()
                                .controlSize(.small)
                            Text("\(queue.activeCount)")
                                .font(.caption.monospacedDigit())
                        }
                    }
                    .help("Analyses in progress — open the Band Room")
                }
            }
            ToolbarItem(placement: .automatic) {
                Button {
                    showLaunchpad.toggle()
                } label: {
                    Label("Launchpad", systemImage: "square.grid.3x3.fill")
                }
                .help("Chop pads (Launchpad Pro MK3 mirror)")
            }
            ToolbarItem(placement: .automatic) {
                Button {
                    showSequencer.toggle()
                } label: {
                    Label("Sequencer", systemImage: "squares.below.rectangle")
                }
                .help("Step sequencer (chords, pack pads, and song chops)")
            }
            ToolbarItem(placement: .automatic) {
                Button {
                    if session.sequencer.isPlaying {
                        session.sequencer.stop()
                    } else {
                        session.ensureEngineStarted()
                        session.sequencer.play()
                    }
                } label: {
                    Image(systemName: session.sequencer.isPlaying ? "stop.fill" : "play.fill")
                        .foregroundStyle(session.sequencer.isPlaying ? .red : .green)
                }
                .help(session.sequencer.isPlaying ? "Stop sequencer" : "Play sequencer")
            }
            ToolbarItem(placement: .automatic) {
                Button {
                    showBeatCapture.toggle()
                } label: {
                    Label("Beat", systemImage: "figure.dance")
                }
                .help("Beat Capture — tap a rhythm into a drum pattern")
            }
            ToolbarItem(placement: .automatic) {
                Button {
                    showRecordings.toggle()
                } label: {
                    Label("Recordings", systemImage: "record.circle")
                }
                .help("Saved layer takes (replay / bounce)")
            }
            ToolbarItem(placement: .automatic) {
                Button {
                    showJamPads.toggle()
                } label: {
                    Label("Jam Pads", systemImage: "pianokeys")
                }
                .help("In-key performance pads (wavetable synth)")
            }
            ToolbarItem(placement: .automatic) {
                Button {
                    showPacks.toggle()
                } label: {
                    Label("Packs", systemImage: "square.grid.2x2")
                }
                .help("Curated sample packs (download / play)")
            }
            ToolbarItem(placement: .automatic) {
                ConnectStatusPill(status: session.bridge.status)
            }
        }
        .sheet(isPresented: $showLaunchpad) {
            LaunchpadPanelView()
                .environmentObject(model)
                .environmentObject(session)
        }
        .sheet(isPresented: $showSequencer) {
            SequencerPanelView()
                .environmentObject(model)
                .environmentObject(session)
        }
        .sheet(isPresented: $showRecordings) {
            RecordingsListView()
                .environmentObject(model)
                .environmentObject(session)
        }
        .sheet(isPresented: $showJamPads) {
            JamPadGridView()
                .environmentObject(model)
                .environmentObject(session)
        }
        .sheet(isPresented: $showPacks) {
            PacksBrowserView()
                .environmentObject(model)
                .environmentObject(session)
        }
        .sheet(isPresented: $showBeatCapture) {
            BeatCaptureSheet(onOpenInSequencer: { showSequencer = true })
                .environmentObject(session)
        }
        .sheet(isPresented: $showVocoder) {
            VocoderCaptureSheet(target: VocoderCaptureTarget(padIndex: 0))
                .environmentObject(session)
        }
        .task {
            session.startBridge(
                sessionId: model.bridgeSessionId,
                backendBaseURL: model.backendBaseURL
            )
            // Recover this device's server-side jobs after a relaunch
            // without requiring a visit to the Band Room.
            await queue.refreshFromServer(baseURL: model.backendBaseURL)
        }
    }

    /// Main content area (view switcher).
    @ViewBuilder
    private var mainContent: some View {
        switch model.view {
        case .intake:
            IntakeView()
        case .bandRoom:
            BandRoomView()
        case .rehearsal:
            RehearsalView()
        case .perform:
            PerformView()
        case .studio:
            StudioView()
        }
    }

    /// Load a song from history directly (skip re-download).
    private func loadSong(_ entry: HistoryEntry) {
        Task {
            await model.loadSession(analysisId: entry.id)
        }
    }

    /// Temporary dev switcher until in-app navigation (session load →
    /// band room → perform) drives `model.view` organically.
    private var viewSwitcher: some ToolbarContent {
        ToolbarItem(placement: .principal) {
            Picker("View", selection: $selectedView) {
                Text("Intake").tag(JamView.intake)
                Text("Band Room").tag(JamView.bandRoom)
                Text("Rehearsal").tag(JamView.rehearsal)
                Text("Perform").tag(JamView.perform)
                Text("Studio").tag(JamView.studio)
            }
            .pickerStyle(.segmented)
        }
    }
}
