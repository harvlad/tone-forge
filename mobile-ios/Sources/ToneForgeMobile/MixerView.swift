// MixerView.swift
//
// Per-stem mixer, matching the mockup: a vertical list of channel
// ROWS — one per stem (drums / bass / vocals / other) with a tinted
// role icon, name, S/M buttons, a horizontal slider, and a dB
// readout — plus a Your Layer row (layerFaderDb) and a Master row.
// (The first cut used horizontal strips with vertical faders; the
// mockup is rows, and rows also sidestep the fader-vs-scroll gesture
// fight entirely.) State lives on ``StemPlayer`` /
// SampleSettingsStore / AppState and is mutated via their published
// APIs — this view is a thin observer.
//
// D-022 Phase 8: segmented [Levels | FX] control. The FX segment
// hosts FXPanelBody inline so the master FX panel is accessible
// from the Mixer tab without a separate sheet.
//
// Presented as a sheet from the Play tab so it can pop up over the
// pad grid without stealing screen real estate.

import SwiftUI
import ToneForgeEngine

/// Mixer segment picker (D-022 Phase 8).
enum MixerSegment: String, CaseIterable, Identifiable {
    case levels = "Levels"
    case fx = "FX"

    var id: String { rawValue }
}

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
                sampleSettings: appState.sampleSettings,
                fxSettingsStore: appState.fxSettingsStore
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

/// Internal (not private) so MixerSnapshotTests can render the body
/// directly — ImageRenderer can't flatten NavigationStack (UIKit-
/// backed) and would produce a full-frame placeholder otherwise.
struct MixerBody: View {
    @ObservedObject var stemPlayer: StemPlayer
    @ObservedObject var sampleSettings: SampleSettingsStore
    @ObservedObject var fxSettingsStore: FXSettingsStore
    @EnvironmentObject private var appState: AppState
    /// Snapshot hook: ImageRenderer also leaves ScrollView content
    /// blank, so MixerSnapshotTests renders the rows without the
    /// scroller. Production call sites leave this false.
    var renderForSnapshot = false
    /// D-022 Phase 8: initial segment selection (snapshot override).
    private let initialSegment: MixerSegment
    /// D-022 Phase 8: segment selection.
    @State private var segment: MixerSegment

    init(
        stemPlayer: StemPlayer,
        sampleSettings: SampleSettingsStore,
        fxSettingsStore: FXSettingsStore,
        renderForSnapshot: Bool = false,
        initialSegment: MixerSegment = .levels
    ) {
        self.stemPlayer = stemPlayer
        self.sampleSettings = sampleSettings
        self.fxSettingsStore = fxSettingsStore
        self.renderForSnapshot = renderForSnapshot
        self.initialSegment = initialSegment
        _segment = State(initialValue: initialSegment)
    }

    var body: some View {
        VStack(spacing: 0) {
            segmentPicker
            if renderForSnapshot {
                segmentContent
            } else {
                ScrollView { segmentContent }
            }
        }
    }

    // MARK: - Segment picker

    private var segmentPicker: some View {
        HStack(spacing: 8) {
            ForEach(MixerSegment.allCases) { seg in
                Button {
                    segment = seg
                } label: {
                    Text(seg.rawValue)
                        .font(TFTheme.chipFont)
                        .tfChip(active: segment == seg)
                }
                .buttonStyle(.plain)
            }
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    // MARK: - Segment content

    @ViewBuilder
    private var segmentContent: some View {
        switch segment {
        case .levels:
            levelsRows
        case .fx:
            FXPanelBody(store: fxSettingsStore)
                .padding(.horizontal, 16)
        }
    }

    private var levelsRows: some View {
        VStack(spacing: 8) {
            ForEach(stemPlayer.stems) { stem in
                stemRow(stem)
            }

            if stemPlayer.stems.isEmpty {
                Text("No stems loaded. Pick a song from the Library tab.")
                    .font(.callout)
                    .foregroundStyle(TFTheme.textSecondary)
                    .padding(.vertical, 12)
                    .multilineTextAlignment(.center)
            }

            yourLayerRow
            masterRow

            if let pack = appState.activeSamplePack {
                Text("Pack: \(pack.pack.name)")
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
                    .padding(.top, 4)
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 12)
    }

    // MARK: - Stem rows

    private func stemRow(_ stem: StemPlayer.StemState) -> some View {
        channelRow(
            icon: Self.icon(forRole: stem.role),
            tint: Self.tint(forRole: stem.role),
            name: stem.role.capitalized,
            value: Binding(
                get: { Double(stem.gain) },
                set: { appState.stemPlayer.setGain(role: stem.role, gain: Float($0)) }
            ),
            range: 0...1,
            readout: MixerReadout.dbString(gainLinear: Double(stem.gain)),
            dimmed: stem.isMuted
        ) {
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

    // MARK: - Your Layer row

    /// Drives the layer bus's outputVolume via SampleSettingsStore →
    /// AppState Combine sink. Both Samples-panel triggers and
    /// Instrument-panel pad synth output fold into this fader; pulling
    /// to -60 dB effectively mutes the layer while leaving the stems
    /// untouched.
    private var yourLayerRow: some View {
        channelRow(
            icon: "person.wave.2.fill",
            tint: Color.accentColor,
            name: "Your Layer",
            value: $sampleSettings.layerFaderDb,
            range: -60...6,
            readout: String(format: "%+.0f dB", sampleSettings.layerFaderDb),
            dimmed: false
        ) {
            // No S/M for the layer — hidden placeholders keep the
            // slider column aligned with the stem rows.
            soloMuteButton(label: "S", active: false, activeTint: .yellow,
                           accessibility: "") {}.hidden()
            soloMuteButton(label: "M", active: false, activeTint: .red,
                           accessibility: "") {}.hidden()
        }
    }

    // MARK: - Master row

    private var masterRow: some View {
        channelRow(
            icon: "speaker.wave.2.fill",
            tint: TFTheme.textPrimary,
            name: "Master",
            value: Binding(
                get: { appState.masterGain },
                set: { appState.setMasterGain($0) }
            ),
            range: 0...1,
            readout: MixerReadout.dbString(gainLinear: appState.masterGain),
            dimmed: false
        ) {
            soloMuteButton(label: "S", active: false, activeTint: .yellow,
                           accessibility: "") {}.hidden()
            soloMuteButton(label: "M", active: false, activeTint: .red,
                           accessibility: "") {}.hidden()
        }
    }

    // MARK: - Row scaffold

    private func channelRow<Buttons: View>(
        icon: String,
        tint: Color,
        name: String,
        value: Binding<Double>,
        range: ClosedRange<Double>,
        readout: String,
        dimmed: Bool,
        @ViewBuilder buttons: () -> Buttons
    ) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icon)
                .font(.caption)
                .foregroundStyle(dimmed ? TFTheme.textSecondary : tint)
                .frame(width: 30, height: 30)
                .overlay(Circle().stroke(
                    (dimmed ? TFTheme.textSecondary : tint).opacity(0.5),
                    lineWidth: 1
                ))

            Text(name)
                .font(TFTheme.chipFont)
                .foregroundStyle(dimmed ? TFTheme.textSecondary : TFTheme.textPrimary)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
                .frame(width: 62, alignment: .leading)

            buttons()

            Slider(value: value, in: range)
                .opacity(dimmed ? 0.45 : 1)
                .accessibilityLabel("\(name) level")

            Text(readout)
                .font(TFTheme.readout)
                .foregroundStyle(dimmed ? TFTheme.textSecondary : TFTheme.textPrimary)
                .frame(width: 56, alignment: .trailing)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
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

    /// Per-channel icon tint from the mockup's colored channel dots.
    static func tint(forRole role: String) -> Color {
        switch role.lowercased() {
        case "drums":  return .orange
        case "bass":   return .blue
        case "vocals": return .purple
        case "other":  return .green
        default:        return .gray
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
