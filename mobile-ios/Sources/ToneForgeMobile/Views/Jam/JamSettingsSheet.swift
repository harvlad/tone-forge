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
    @Environment(\.dismiss) private var dismiss

    /// Key editing moved here from the Jam toolbar (grid gets the room).
    @State private var showKeySheet = false

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
            }
            .navigationTitle("Jam Settings")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .sheet(isPresented: $showKeySheet) {
                ScaleWheelSheet(controller: controller, jamSettings: jamSettings)
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
