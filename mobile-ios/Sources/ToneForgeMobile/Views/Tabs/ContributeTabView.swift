// ContributeTabView.swift
//
// Contribute tab (D-022): the 8×8 sample/hybrid grid surface (the
// old Play tab default). Hosts ContributeSurface inside TabScaffold
// and owns the two contribute-specific sheets (pack browser + help)
// plus the "how does contributing work?" footer.

import SwiftUI
import ToneForgeEngine

struct ContributeTabView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        ContributeTabBody(
            coordinator: appState.modeCoordinator,
            sampleSettings: appState.sampleSettings,
            sketchSettings: appState.sketchSettings
        )
    }
}

/// Indirection so the view observes the coordinator's and the
/// settings stores' @Published state (AppState doesn't republish
/// nested ObservableObjects).
private struct ContributeTabBody: View {
    @ObservedObject var coordinator: ModeCoordinator
    @ObservedObject var sampleSettings: SampleSettingsStore
    @ObservedObject var sketchSettings: SketchSettingsStore

    @State private var showBrowse = false
    @State private var showHelp = false
    /// Family pre-filter for the pack browser (set by CategoryCards,
    /// nil for the plain PackPicker "open browser" path).
    @State private var browseFamily: SampleFamily?

    var body: some View {
        TabScaffold {
            ContributeSurface(
                coordinator: coordinator,
                sampleSettings: sampleSettings,
                sketchSettings: sketchSettings,
                onOpenBrowse: { family in
                    browseFamily = family
                    showBrowse = true
                }
            )

            howItWorksFooter
        }
        .sheet(isPresented: $showBrowse) {
            BrowsePacksSheet(initialFamily: browseFamily)
        }
        .sheet(isPresented: $showHelp) { HelpSheet() }
    }

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
}
