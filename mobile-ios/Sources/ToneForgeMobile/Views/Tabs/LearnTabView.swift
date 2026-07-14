// LearnTabView.swift
//
// Learn tab (D-022): guided practice for the loaded song. Thin
// wrapper — the surface itself is LearnView; TabScaffold provides
// the song header + transport chrome.

import SwiftUI

struct LearnTabView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        // No song = full-bleed welcome screen: skip TabScaffold so
        // there's no "No song loaded" header or transport chrome.
        if appState.currentBundle == nil {
            JamWelcomeView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(TFTheme.background.ignoresSafeArea())
        } else {
            TabScaffold {
                LearnView(controller: appState.learnController)
            }
        }
    }
}
