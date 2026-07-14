// SequencerTabView.swift
//
// Container view for the sequencer (D-023 Phase 4). Provides toggle
// between Pattern and Timeline modes, plus integration with the
// Contribute surface's transport and audio systems.
//
// When in Pattern mode: shows PatternEditorView (MPC-style grid).
// When in Timeline mode: shows TimelineView (DAW-style arrangement).
//
// The view creates and manages the SequencerPlayer, syncing it with
// the app's TransportClock for song-locked playback.

import SwiftUI
import ToneForgeEngine

/// Mode toggle for the sequencer view.
enum SequencerMode: String, CaseIterable {
    case pattern = "Pattern"
    case timeline = "Timeline"
}

struct SequencerTabView: View {
    let eventBus: ContributionEventBus
    let songBPM: Double
    let currentBeat: Double
    let isPlaying: Bool
    /// When set, seed the player from this saved pattern on first
    /// appear (Beat Capture "Open in Sequencer").
    let initialPatternId: UUID?

    @EnvironmentObject private var appState: AppState
    @State private var mode: SequencerMode = .pattern
    @StateObject private var player: SequencerPlayer
    @State private var arrangement: TimelineArrangement
    @State private var didSeedPattern = false

    init(
        eventBus: ContributionEventBus,
        songBPM: Double = 120,
        currentBeat: Double = 0,
        isPlaying: Bool = false,
        analysisId: String = "",
        initialPatternId: UUID? = nil
    ) {
        self.eventBus = eventBus
        self.songBPM = songBPM
        self.currentBeat = currentBeat
        self.isPlaying = isPlaying
        self.initialPatternId = initialPatternId

        // Initialize player with empty pattern
        _player = StateObject(wrappedValue: SequencerPlayer(
            pattern: SequencerPattern(),
            eventBus: eventBus
        ))

        // Initialize arrangement
        _arrangement = State(initialValue: TimelineArrangement(analysisId: analysisId))
    }

    var body: some View {
        VStack(spacing: 0) {
            // Mode picker
            modePicker

            // Content based on mode
            switch mode {
            case .pattern:
                PatternEditorView(player: player)
            case .timeline:
                TimelineView(
                    arrangement: $arrangement,
                    bpm: songBPM,
                    currentBeat: currentBeat,
                    isPlaying: isPlaying,
                    onPreviewClip: { clip in
                        player.previewChop(clip.chopRef, velocity: clip.velocity)
                    }
                )
            }
        }
        .onAppear {
            player.songBPM = songBPM
            // Sound bundleChop/localSample/customURL tracks (non-bus).
            player.delegate = appState
            seedInitialPatternIfNeeded()
        }
        .onChange(of: songBPM) { _, newBPM in
            player.songBPM = newBPM
        }
        .onChange(of: isPlaying) { _, playing in
            if playing {
                // Song transport started: (re)start the sequencer song-synced
                // so the app's beat clock drives it via tick(songSeconds:).
                if player.isPlaying { player.stop() }
                let songSeconds = currentBeat * 60 / songBPM
                player.play(at: songSeconds, sync: true)
            } else {
                player.stop()
            }
        }
        .onChange(of: currentBeat) { _, newBeat in
            // Drive the sequencer clock from the app's transport
            guard isPlaying else { return }
            let songSeconds = newBeat * 60 / songBPM
            player.tick(songSeconds: songSeconds)
        }
    }

    // MARK: - Pattern seeding

    /// Load the Beat Capture pattern into the player once, then clear
    /// the pending id so re-renders don't reseed over user edits.
    private func seedInitialPatternIfNeeded() {
        guard !didSeedPattern, let id = initialPatternId,
              let pattern = appState.sequencerPatternStore.pattern(id: id)
        else { return }
        didSeedPattern = true
        player.pattern = pattern
        mode = .pattern
        if appState.pendingSequencerPatternId == id {
            appState.pendingSequencerPatternId = nil
        }
    }

    // MARK: - Mode Picker

    private var modePicker: some View {
        HStack(spacing: 0) {
            ForEach(SequencerMode.allCases, id: \.self) { segmentMode in
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        mode = segmentMode
                    }
                } label: {
                    Text(segmentMode.rawValue)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(mode == segmentMode ? .white : TFTheme.textSecondary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(
                            mode == segmentMode
                                ? Color.accentColor.opacity(0.3)
                                : Color.clear
                        )
                }
            }
        }
        .background(TFTheme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(TFTheme.stroke, lineWidth: 1)
        )
        .padding(.horizontal)
        .padding(.vertical, 8)
    }
}

// MARK: - Preview

#if DEBUG
struct SequencerTabView_Previews: PreviewProvider {
    static var previews: some View {
        SequencerTabView(
            eventBus: ContributionEventBus(),
            songBPM: 120
        )
        .preferredColorScheme(.dark)
    }
}
#endif
