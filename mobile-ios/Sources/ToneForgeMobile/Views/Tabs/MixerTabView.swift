// MixerTabView.swift
//
// Mixer tab (D-022): the per-stem mixer promoted from a Play-tab
// sheet to a first-class tab. Hosts MixerBody directly (the sheet
// wrapper MixerView keeps serving snapshot tests until Phase 8
// restructures this screen into Levels | FX segments).
//
// Passive surface: TabModePolicy leaves the engine mode untouched
// here, so audio keeps behaving while the user mixes.

import SwiftUI

struct MixerTabView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        TabScaffold {
            MixerBody(
                stemPlayer: appState.stemPlayer,
                sampleSettings: appState.sampleSettings,
                fxSettingsStore: appState.fxSettingsStore
            )
        }
    }
}
