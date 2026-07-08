// MixerView.swift
//
// Per-stem mixer, restyled per the mockup (Phase 11): horizontal
// channel strips — one per stem (drums / bass / vocals / other) with
// a role icon, vertical fader, dB readout, and S/M buttons — plus a
// Your Layer strip (layerFaderDb) and a Master strip. State lives on
// ``StemPlayer`` / SampleSettingsStore / AppState and is mutated via
// their published APIs — this view is a thin observer.
//
// Presented as a sheet from the Play tab so it can pop up over the
// pad grid without stealing screen real estate.

import SwiftUI
import ToneForgeEngine

struct MixerView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            // Indirection so the strips observe the nested stores'
            // @Published state (AppState doesn't republish nested
            // ObservableObjects).
            MixerBody(
                stemPlayer: appState.stemPlayer,
                sampleSettings: appState.sampleSettings
            )
            .background(TFTheme.background.ignoresSafeArea())
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
        .preferredColorScheme(.dark)
    }
}

private struct MixerBody: View {
    @ObservedObject var stemPlayer: StemPlayer
    @ObservedObject var sampleSettings: SampleSettingsStore
    @EnvironmentObject private var appState: AppState

    private let faderHeight: CGFloat = 170
    private let stripWidth: CGFloat = 78

    var body: some View {
        VStack(spacing: 16) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: .top, spacing: 10) {
                    ForEach(stemPlayer.stems) { stem in
                        stemStrip(stem)
                    }
                    yourLayerStrip
                    masterStrip
                }
                .padding(.horizontal, 16)
                .padding(.top, 12)
            }

            if stemPlayer.stems.isEmpty {
                Text("No stems loaded. Pick a song from the Library tab.")
                    .font(.callout)
                    .foregroundStyle(TFTheme.textSecondary)
                    .padding(.horizontal, 24)
                    .multilineTextAlignment(.center)
            }

            if let pack = appState.activeSamplePack {
                Text("Pack: \(pack.pack.name)")
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
            }

            Spacer(minLength: 0)
        }
    }

    // MARK: - Stem strips

    private func stemStrip(_ stem: StemPlayer.StemState) -> some View {
        channelStrip(
            icon: Self.icon(forRole: stem.role),
            name: stem.role.capitalized,
            fader: VerticalFader(
                value: Binding(
                    get: { Double(stem.gain) },
                    set: { appState.stemPlayer.setGain(role: stem.role, gain: Float($0)) }
                ),
                range: 0...1
            ),
            readout: MixerReadout.dbString(gainLinear: Double(stem.gain)),
            dimmed: stem.isMuted
        ) {
            HStack(spacing: 6) {
                soloMuteButton(
                    label: "S",
                    active: stem.isSoloed,
                    activeTint: .yellow,
                    accessibility: "Solo \(stem.role)"
                ) {
                    appState.stemPlayer.toggleSolo(role: stem.role)
                }
                soloMuteButton(
                    label: "M",
                    active: stem.isMuted,
                    activeTint: .red,
                    accessibility: "Mute \(stem.role)"
                ) {
                    appState.stemPlayer.toggleMute(role: stem.role)
                }
            }
        }
    }

    // MARK: - Your Layer strip

    /// Drives the layer bus's outputVolume via SampleSettingsStore →
    /// AppState Combine sink. Both Samples-panel triggers and
    /// Instrument-panel pad synth output fold into this fader; pulling
    /// to -60 dB effectively mutes the layer while leaving the stems
    /// untouched.
    private var yourLayerStrip: some View {
        channelStrip(
            icon: "person.wave.2.fill",
            name: "Your Layer",
            fader: VerticalFader(
                value: $sampleSettings.layerFaderDb,
                range: -60...6
            ),
            readout: String(format: "%+.0f dB", sampleSettings.layerFaderDb),
            dimmed: false
        ) {
            // No S/M — spacer keeps fader heights aligned.
            Color.clear.frame(height: 26)
        }
    }

    // MARK: - Master strip

    private var masterStrip: some View {
        channelStrip(
            icon: "speaker.wave.2.fill",
            name: "Master",
            fader: VerticalFader(
                value: Binding(
                    get: { appState.masterGain },
                    set: { appState.setMasterGain($0) }
                ),
                range: 0...1
            ),
            readout: MixerReadout.dbString(gainLinear: appState.masterGain),
            dimmed: false
        ) {
            Color.clear.frame(height: 26)
        }
    }

    // MARK: - Strip scaffold

    private func channelStrip<Fader: View, Buttons: View>(
        icon: String,
        name: String,
        fader: Fader,
        readout: String,
        dimmed: Bool,
        @ViewBuilder buttons: () -> Buttons
    ) -> some View {
        VStack(spacing: 8) {
            Image(systemName: icon)
                .font(.body)
                .foregroundStyle(dimmed ? TFTheme.textSecondary : TFTheme.textPrimary)
            Text(name)
                .font(TFTheme.chipFont)
                .foregroundStyle(TFTheme.textSecondary)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            fader
                .frame(height: faderHeight)
                .opacity(dimmed ? 0.45 : 1)
            Text(readout)
                .font(TFTheme.readout)
                .foregroundStyle(dimmed ? TFTheme.textSecondary : TFTheme.textPrimary)
            buttons()
        }
        .padding(.vertical, 12)
        .padding(.horizontal, 6)
        .frame(width: stripWidth)
        .tfCard()
    }

    private func soloMuteButton(
        label: String,
        active: Bool,
        activeTint: Color,
        accessibility: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(label)
                .font(TFTheme.chipFont)
                .foregroundStyle(active ? Color.black : TFTheme.textSecondary)
                .frame(width: 26, height: 26)
                .background(
                    active ? activeTint : TFTheme.chipFill,
                    in: Circle()
                )
                .overlay(Circle().stroke(TFTheme.stroke, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(accessibility)
        .accessibilityAddTraits(active ? .isSelected : [])
    }

    // MARK: - Role icons

    static func icon(forRole role: String) -> String {
        switch role.lowercased() {
        case "drums":  return "metronome.fill"
        case "bass":   return "waveform.path"
        case "vocals": return "music.mic"
        case "other":  return "guitars.fill"
        default:        return "waveform"
        }
    }
}

// MARK: - Readout formatting

/// dB readout for linear-gain faders (stems, master). Internal (not
/// view-private) so the formatting contract is unit-testable.
enum MixerReadout {
    /// `20*log10(gain)` formatted "+0.0 dB" style; 0 gain → "-∞ dB".
    static func dbString(gainLinear: Double) -> String {
        guard gainLinear > 0 else { return "-∞ dB" }
        let db = 20 * log10(gainLinear)
        return String(format: "%+.1f dB", db)
    }
}
