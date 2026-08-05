// InstrumentEditorView.swift
//
// The deeper "instrument construction" workspace (progressive
// disclosure Level 3), reached CONTEXTUALLY from Jam — never a
// permanent button. It reuses the existing ContributeSurface
// (advanced 8×8 grid, sequencer, Beat Capture, pack browser,
// arrange, multi-pad management) as full-screen editing
// infrastructure that survives the "Contribute" tab going away.
//
// On present it switches the engine into .sample so the construction
// grid is live; on dismiss it restores .jamInKey so the Jam / Perform
// instrument state the performer built stays intact.

import SwiftUI
import ToneForgeEngine

struct InstrumentEditorView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        InstrumentEditorBody(
            coordinator: appState.modeCoordinator,
            sampleSettings: appState.sampleSettings,
            sketchSettings: appState.sketchSettings,
            onDone: { dismiss() }
        )
    }
}

/// Indirection so the editor observes the coordinator + settings
/// stores' @Published state (AppState doesn't republish nested
/// ObservableObjects). Mirrors ContributeTabBody's composition, plus
/// a title bar with Done and the engine-mode bracket.
private struct InstrumentEditorBody: View {
    @ObservedObject var coordinator: ModeCoordinator
    @ObservedObject var sampleSettings: SampleSettingsStore
    @ObservedObject var sketchSettings: SketchSettingsStore
    let onDone: () -> Void
    @EnvironmentObject private var appState: AppState

    @State private var showBrowse = false
    @State private var showHelp = false
    @State private var browseFamily: SampleFamily?

    var body: some View {
        VStack(spacing: TFTheme.Spacing.sm) {
            titleBar

            ContributeSurface(
                coordinator: coordinator,
                sampleSettings: sampleSettings,
                sketchSettings: sketchSettings,
                onOpenBrowse: { family in
                    browseFamily = family
                    showBrowse = true
                }
            )

            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(TFTheme.background.ignoresSafeArea())
        // Enter the sample-construction engine mode while editing; put
        // the Jam instrument back when the performer leaves.
        .onAppear { coordinator.setMode(.sample) }
        .onDisappear { coordinator.setMode(.jamInKey) }
        .sheet(isPresented: $showBrowse) {
            BrowsePacksSheet(initialFamily: browseFamily)
        }
        .sheet(isPresented: $showHelp) { HelpSheet() }
    }

    private var titleBar: some View {
        HStack {
            VStack(alignment: .leading, spacing: 1) {
                Text("Instrument Editor")
                    .font(TFTheme.screenTitle)
                    .foregroundStyle(TFTheme.textPrimary)
                Text("Build pads, sequences & packs")
                    .font(TFTheme.metadata)
                    .foregroundStyle(TFTheme.textSecondary)
            }
            Spacer()
            Button {
                showHelp = true
            } label: {
                Image(systemName: "questionmark.circle")
                    .font(.title3)
                    .foregroundStyle(TFTheme.textSecondary)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("How construction works")
            Button("Done") { onDone() }
                .font(.body.weight(.semibold))
                .foregroundStyle(TFTheme.accent)
        }
        .padding(.horizontal, TFTheme.Spacing.lg)
        .padding(.top, TFTheme.Spacing.md)
    }
}
