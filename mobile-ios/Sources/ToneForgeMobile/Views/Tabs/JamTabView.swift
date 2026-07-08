// JamTabView.swift
//
// Jam tab (D-022): the jam-in-key performance surface. Thin wrapper
// around JamView; Chord Pads folds in as a pad-mode toggle inside
// JamView (Phase 5), so this tab is the only home for both.

import SwiftUI

struct JamTabView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        TabScaffold {
            JamView(
                coordinator: appState.modeCoordinator,
                jamSettings: appState.jamSettings,
                controller: appState.jamController,
                chordPadController: appState.chordPadController
            )
        }
    }
}
