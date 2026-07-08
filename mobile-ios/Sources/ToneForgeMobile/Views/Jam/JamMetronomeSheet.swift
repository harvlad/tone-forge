// JamMetronomeSheet.swift
//
// Metronome settings for the Jam in Key surface (redesign Phase 7).
// Jam keeps its own accent/sound/subdivide/enabled in
// JamSettingsStore (independent of the sketch metronome), applied
// through JamInKeyController so AppState.syncMetronome refreshes the
// running click.
//
// Tempo/time-sig rows: with a song loaded the analysed grid wins and
// the rows are read-only; songless they edit the sketch tempo (the
// same values the sketch surface uses — one tempo, two surfaces).

import SwiftUI
import ToneForgeEngine

struct JamMetronomeSheet: View {
    @ObservedObject var controller: JamInKeyController
    @ObservedObject var jamSettings: JamSettingsStore
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Toggle(
                        "Metronome",
                        isOn: Binding(
                            get: { jamSettings.metronomeEnabled },
                            set: { controller.setMetronomeEnabled($0) }
                        )
                    )
                }

                Section("Tempo") {
                    if let bpm = appState.currentBundle?.meta.tempoBpm {
                        LabeledContent(
                            "BPM",
                            value: "\(Int(bpm.rounded())) (from song)"
                        )
                    } else {
                        Stepper(
                            "BPM \(Int(appState.sketchSettings.tempoBpm.rounded()))",
                            value: sketchBpmBinding,
                            in: 40...240,
                            step: 1
                        )
                        Stepper(
                            "Beats per bar \(appState.sketchSettings.timeSigNumerator)",
                            value: sketchTimeSigBinding,
                            in: 2...12
                        )
                    }
                }

                Section("Accent") {
                    Picker("Accent", selection: accentBinding) {
                        ForEach(MetronomeAccent.allCases, id: \.rawValue) { a in
                            Text(Self.label(for: a)).tag(a)
                        }
                    }
                    .pickerStyle(.inline)
                    .labelsHidden()
                }

                Section("Sound") {
                    Picker("Sound", selection: soundBinding) {
                        ForEach(MetronomeSound.allCases, id: \.rawValue) { s in
                            Text(Self.label(for: s)).tag(s)
                        }
                    }
                    .pickerStyle(.inline)
                    .labelsHidden()

                    Toggle(
                        "Subdivide (8th notes)",
                        isOn: Binding(
                            get: { jamSettings.metronomeSubdivide },
                            set: { controller.setMetronomeSubdivide($0) }
                        )
                    )
                }
            }
            .navigationTitle("Metronome")
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

    // MARK: - Bindings

    private var accentBinding: Binding<MetronomeAccent> {
        Binding(
            get: { jamSettings.metronomeAccent },
            set: { controller.setMetronomeAccent($0) }
        )
    }

    private var soundBinding: Binding<MetronomeSound> {
        Binding(
            get: { jamSettings.metronomeSound },
            set: { controller.setMetronomeSound($0) }
        )
    }

    private var sketchBpmBinding: Binding<Double> {
        Binding(
            get: { appState.sketchSettings.tempoBpm },
            set: {
                appState.sketchSettings.tempoBpm = $0
                appState.syncMetronome()
            }
        )
    }

    private var sketchTimeSigBinding: Binding<Int> {
        Binding(
            get: { appState.sketchSettings.timeSigNumerator },
            set: {
                appState.sketchSettings.timeSigNumerator = $0
                appState.syncMetronome()
            }
        )
    }

    // MARK: - Labels

    static func label(for accent: MetronomeAccent) -> String {
        switch accent {
        case .downbeat:    return "Downbeat"
        case .oneAndThree: return "Beats 1 + 3"
        case .everyBeat:   return "Every beat"
        case .none:        return "No accent"
        }
    }

    static func label(for sound: MetronomeSound) -> String {
        switch sound {
        case .sine:      return "Sine"
        case .woodBlock: return "Wood Block"
        case .click:     return "Click"
        case .rim:       return "Rim"
        }
    }
}
