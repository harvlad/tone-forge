// MixerView.swift
//
// Per-stem mixer: one row per stem (drums / bass / vocals / other),
// each with a gain slider, mute toggle, and solo toggle. Master
// (all-stems) fader on the top. State lives on ``StemPlayer`` and is
// mutated via its published API — this view is a thin observer.
//
// Presented as a sheet from the Perform tab so it can pop up over the
// pad grid without stealing screen real estate.

import SwiftUI
import ToneForgeEngine

struct MixerView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section("Master") {
                    HStack {
                        Image(systemName: "speaker.wave.2.fill")
                        Slider(
                            value: Binding(
                                get: { appState.masterGain },
                                set: { appState.setMasterGain($0) }
                            ),
                            in: 0...1
                        )
                        Text(percent(appState.masterGain))
                            .font(.caption.monospacedDigit())
                            .frame(width: 42, alignment: .trailing)
                    }
                }

                Section("Your Layer") {
                    // Drives the layer bus's outputVolume via
                    // SampleSettingsStore → AppState Combine sink.
                    // Both Samples-panel triggers and Instrument-panel
                    // pad synth output fold into this fader; muting to
                    // -60 dB leaves the stems untouched.
                    HStack {
                        Image(systemName: "person.wave.2")
                        Slider(
                            value: Binding(
                                get: { appState.sampleSettings.layerFaderDb },
                                set: { appState.sampleSettings.layerFaderDb = $0 }
                            ),
                            in: -60...6
                        )
                        Text(String(format: "%+.0f dB", appState.sampleSettings.layerFaderDb))
                            .font(.caption.monospacedDigit())
                            .frame(width: 56, alignment: .trailing)
                    }
                    if let pack = appState.activeSamplePack {
                        Text("Pack: \(pack.pack.name)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                Section("Stems") {
                    if appState.stemPlayer.stems.isEmpty {
                        Text("No stems loaded. Pick a song from the Library tab.")
                            .foregroundStyle(.secondary)
                            .font(.callout)
                    } else {
                        ForEach(appState.stemPlayer.stems) { stem in
                            stemRow(stem)
                        }
                    }
                }
            }
            .navigationTitle("Mixer")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    @ViewBuilder
    private func stemRow(_ stem: StemPlayer.StemState) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(stem.role.capitalized)
                    .font(.body.weight(.medium))
                Spacer()
                Button(stem.isSoloed ? "Solo" : "Solo") {
                    appState.stemPlayer.toggleSolo(role: stem.role)
                }
                .buttonStyle(.bordered)
                .tint(stem.isSoloed ? .yellow : .gray)
                Button(stem.isMuted ? "Muted" : "Mute") {
                    appState.stemPlayer.toggleMute(role: stem.role)
                }
                .buttonStyle(.bordered)
                .tint(stem.isMuted ? .red : .gray)
            }
            HStack {
                Image(systemName: "waveform")
                    .foregroundStyle(.secondary)
                Slider(
                    value: Binding(
                        get: { Double(stem.gain) },
                        set: { appState.stemPlayer.setGain(role: stem.role, gain: Float($0)) }
                    ),
                    in: 0...1
                )
                Text(percent(Double(stem.gain)))
                    .font(.caption.monospacedDigit())
                    .frame(width: 42, alignment: .trailing)
            }
        }
    }

    private func percent(_ v: Double) -> String {
        "\(Int((v * 100).rounded()))%"
    }
}
