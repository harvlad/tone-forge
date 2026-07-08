// LearnTabView.swift
//
// Learn tab (D-022): guided practice for the loaded song. Thin
// wrapper — the surface itself is LearnView; TabScaffold provides
// the song header + transport chrome.

import SwiftUI

struct LearnTabView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        TabScaffold {
            LearnView(controller: appState.learnController)
        }
    }
}
