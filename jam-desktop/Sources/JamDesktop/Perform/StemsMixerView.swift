// StemsMixerView.swift
//
// Stems mixer panel: Levels|FX segment picker. Levels = Song master
// fader on top, one strip per stem (gain slider + Mute/Solo), driving
// the Core StemMixModel — semantics identical to the web mixer (mute
// wins, any-solo silences the rest). FX = the D-022 master FX panel
// (FXPanelView) driving FXPanelModel → MusicBus.

import SwiftUI
import JamDesktopCore

struct StemsMixerView: View {
    @EnvironmentObject private var session: SessionController

    private enum Panel: String, CaseIterable {
        case levels = "Levels"
        case fx = "FX"
    }

    @State private var panel: Panel = .levels

    private var mix: StemMixModel { session.mix }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("Mixer")
                    .font(.headline)
                Spacer()
                Picker("", selection: $panel) {
                    ForEach(Panel.allCases, id: \.self) { p in
                        Text(p.rawValue).tag(p)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
            }

            switch panel {
            case .levels:
                songStrip

                Divider()

                ScrollView {
                    VStack(spacing: 14) {
                        ForEach(mix.stems) { stem in
                            stemStrip(stem)
                        }
                    }
                }
            case .fx:
                FXPanelView(model: session.fx)
            }

            Spacer()
        }
        .padding(16)
        .frame(maxHeight: .infinity)
        .background(JamTheme.surface)
    }

    private var songStrip: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Song")
                .font(.subheadline.weight(.semibold))
            HStack {
                Image(systemName: "speaker.wave.2")
                    .foregroundStyle(.secondary)
                Slider(
                    value: Binding(
                        get: { mix.songGain },
                        set: { mix.songGain = $0 }
                    ),
                    in: 0...1
                )
            }
        }
    }

    private func stemStrip(_ stem: StemMixState) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(stem.role.capitalized)
                    .font(.subheadline)
                Spacer()
                muteSoloButtons(stem)
            }
            Slider(
                value: Binding(
                    get: { stem.gain },
                    set: { mix.setGain($0, for: stem.role) }
                ),
                in: 0...1
            )
            .disabled(stem.isMuted)
            .opacity(effectiveOpacity(stem))
        }
    }

    private func muteSoloButtons(_ stem: StemMixState) -> some View {
        HStack(spacing: 4) {
            Button("M") { mix.toggleMute(for: stem.role) }
                .buttonStyle(.bordered)
                .tint(stem.isMuted ? .red : nil)
                .help("Mute")

            Button("S") { mix.toggleSolo(for: stem.role) }
                .buttonStyle(.bordered)
                .tint(stem.isSoloed ? .yellow : nil)
                .help("Solo")
        }
        .controlSize(.small)
    }

    /// Dim strips that are effectively silent (muted, or shadowed by
    /// another stem's solo).
    private func effectiveOpacity(_ stem: StemMixState) -> Double {
        mix.effectiveGain(for: stem.role) == 0 && stem.gain > 0 ? 0.4 : 1.0
    }
}
