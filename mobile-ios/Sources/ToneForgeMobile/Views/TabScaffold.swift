// TabScaffold.swift
//
// Shared chrome for the performance tabs (D-022): compact song
// header on top, tab content in the middle, transport row on the
// bottom. Deliberately NO NavigationStack and NO ScrollView — the
// tabs sign the "no scrolling" contract, and every screen must fit
// the height it's given.
//
// The gear in the header presents the Settings sheet, so each tab
// gets Settings without owning a toolbar.

import SwiftUI

struct TabScaffold<Content: View, Accessory: View>: View {
    @EnvironmentObject private var appState: AppState

    var showsTransport: Bool
    var accessory: () -> Accessory
    var content: () -> Content

    @State private var showSettings = false

    init(
        showsTransport: Bool = true,
        @ViewBuilder accessory: @escaping () -> Accessory,
        @ViewBuilder content: @escaping () -> Content
    ) {
        self.showsTransport = showsTransport
        self.accessory = accessory
        self.content = content
    }

    var body: some View {
        let hasSong = appState.currentBundle != nil

        VStack(spacing: 8) {
            NowPlayingHeader(
                title: appState.currentBundle?.meta.title ?? "No song loaded",
                artist: appState.currentBundle?.meta.artist,
                durationSec: appState.currentBundle?.meta.durationSec,
                keyLabel: appState.currentBundle?.meta.detectedKey,
                tempoBpm: appState.currentBundle?.meta.tempoBpm,
                analysisId: appState.currentBundle?.analysisId,
                onEject: hasSong ? { appState.ejectSong() } : nil,
                onSettings: { showSettings = true },
                accessory: accessory
            )

            content()

            if showsTransport {
                TransportRow()
            }
        }
        .padding(.top, 4)
        .padding(.bottom, 8)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(TFTheme.background.ignoresSafeArea())
        .sheet(isPresented: $showSettings) { SettingsView() }
    }
}

// MARK: - Convenience initializer (no accessory)

extension TabScaffold where Accessory == EmptyView {
    init(
        showsTransport: Bool = true,
        @ViewBuilder content: @escaping () -> Content
    ) {
        self.showsTransport = showsTransport
        self.accessory = { EmptyView() }
        self.content = content
    }
}
