// PerformTabView.swift
//
// Perform tab: the lean live-performance surface. Thin wrapper around
// PerformView inside TabScaffold (shared song header + transport). It
// uses the SAME controllers as Jam, so the instrument configured in
// Jam is exactly what gets performed here.

import SwiftUI

struct PerformTabView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        TabScaffold {
            PerformView(
                coordinator: appState.modeCoordinator,
                jamSettings: appState.jamSettings,
                controller: appState.jamController,
                chordPadController: appState.chordPadController
            )
        }
    }
}
