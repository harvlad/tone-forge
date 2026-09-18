// JamSettingsSheet.swift
//
// Gear sheet for the Jam in Key surface (redesign Phase 7): synth
// preset picker (SynthPresetCatalog), strum toggle, current-chord
// highlight toggle, and the octave stepper (duplicated from the
// controls row for discoverability).
//
// Preset + highlight + octave route through JamInKeyController so
// the PadSynth params and the grid layout refresh; strum is a plain
// JamSettingsStore binding (read at trigger time, no layout impact).

import SwiftUI
import ToneForgeEngine

struct JamSettingsSheet: View {
    @ObservedObject var controller: JamInKeyController
    @ObservedObject var jamSettings: JamSettingsStore
    /// Chords mode transposes the chord grid, which keeps its own
    /// (unpersisted) octave. The octave stepper here routes to the
    /// surface the performer is currently on, so it stays reachable
    /// after moving out of the Jam toolbar.
    @ObservedObject var chordPadController: ChordPadController
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    /// Key editing moved here from the Jam toolbar (grid gets the room).
    @State private var showKeySheet = false
    /// "Save workspace as project…" naming alert.
    @State private var showSaveWorkspace = false
    @State private var saveWorkspaceName = ""

    private var isMinorFamilyKey: Bool {
        switch controller.effectiveKey?.scale {
        case .minor, .harmonicMinor, .melodicMinor: return true
        default: return false
        }
    }

    private var octaveShift: Int {
        jamSettings.padMode == .chords
            ? chordPadController.octaveShift
            : jamSettings.octaveShift
    }

    private func setOctaveShift(_ shift: Int) {
        switch jamSettings.padMode {
        case .pads:    controller.setOctaveShift(shift)
        case .chords:  chordPadController.setOctaveShift(shift)
        case .samples: break  // fixed song chops — no transpose
        }
    }

    var body: some View {
        NavigationStack {
            List {
                Section("Key") {
                    Button {
                        showKeySheet = true
                    } label: {
                        HStack {
                            Text("Key")
                                .foregroundStyle(TFTheme.textPrimary)
                            Spacer()
                            Text(controller.keyDisplayName)
                                .foregroundStyle(TFTheme.textSecondary)
                            Image(systemName: "chevron.right")
                                .font(.caption)
                                .foregroundStyle(TFTheme.textSecondary)
                        }
                    }
                    if isMinorFamilyKey {
                        Picker("Scale", selection: Binding(
                            get: { jamSettings.scaleVariant },
                            set: { controller.setScaleVariant($0) }
                        )) {
                            ForEach(JamScaleVariant.allCases, id: \.rawValue) { v in
                                Text(v.displayName).tag(v)
                            }
                        }
                    }
                }

                Section("Sound") {
                    ForEach(SynthPresetCatalog.all) { preset in
                        Button {
                            controller.applyPreset(id: preset.id)
                        } label: {
                            HStack {
                                Text(preset.name)
                                    .foregroundStyle(TFTheme.textPrimary)
                                Spacer()
                                if jamSettings.soundPresetId == preset.id {
                                    Image(systemName: "checkmark")
                                        .foregroundStyle(Color.accentColor)
                                }
                            }
                        }
                    }
                }

                Section("Playing") {
                    Toggle("Strum chords", isOn: $jamSettings.strumEnabled)
                    Toggle(
                        "Highlight current chord",
                        isOn: Binding(
                            get: { jamSettings.highlightCurrentChord },
                            set: { controller.setHighlightCurrentChord($0) }
                        )
                    )
                    Stepper(
                        "Octave \(octaveShift >= 0 ? "+" : "")\(octaveShift)",
                        onIncrement: { setOctaveShift(octaveShift + 1) },
                        onDecrement: { setOctaveShift(octaveShift - 1) }
                    )
                }

                // Projects (v1): name a durable copy of the current
                // pad workspace, or reset the song to its default kit.
                // The in-progress workspace auto-saves regardless.
                if appState.currentBundle != nil {
                    Section {
                        Button {
                            saveWorkspaceName = ""
                            showSaveWorkspace = true
                        } label: {
                            Label("Save workspace as project…",
                                  systemImage: "square.and.arrow.down")
                        }
                        Button(role: .destructive) {
                            appState.projects.resetToSong()
                            dismiss()
                        } label: {
                            Label("Reset workspace to song",
                                  systemImage: "arrow.uturn.backward")
                        }
                    } header: {
                        Text("Workspace")
                    } footer: {
                        Text("Pads, effects, sequences and borrowed "
                            + "loops for this song. Saved projects live "
                            + "in Library \u{2192} Projects; changes "
                            + "auto-save to a per-song working copy.")
                    }
                }
            }
            .navigationTitle("Jam Settings")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .sheet(isPresented: $showKeySheet) {
                ScaleWheelSheet(controller: controller, jamSettings: jamSettings)
            }
            .alert("Save workspace", isPresented: $showSaveWorkspace) {
                TextField("Project name", text: $saveWorkspaceName)
                Button("Save") {
                    appState.projects
                        .saveCurrentAsProject(named: saveWorkspaceName)
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Saves this song's pads, effects, sequences and "
                    + "borrowed loops as a project in Library \u{2192} "
                    + "Projects.")
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}
