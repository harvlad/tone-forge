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
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
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
                        "Octave \(jamSettings.octaveShift >= 0 ? "+" : "")\(jamSettings.octaveShift)",
                        onIncrement: {
                            controller.setOctaveShift(jamSettings.octaveShift + 1)
                        },
                        onDecrement: {
                            controller.setOctaveShift(jamSettings.octaveShift - 1)
                        }
                    )
                }
            }
            .navigationTitle("Jam Settings")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}
