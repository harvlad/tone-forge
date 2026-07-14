// FXPanelView.swift
//
// Master FX panel (D-022 Phase 6): 3-band EQ, dynamics compressor,
// reverb send, delay send, FX return level, and preset picker. Shown
// as a sheet from the Mixer tab (full integration in Phase 8 as a
// Levels|FX segment).
//
// All knob edits clear the preset to "Custom"; applying a preset
// restores all params in one shot.

import SwiftUI
import ToneForgeEngine

struct FXPanelView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            FXPanelBody(store: appState.fxSettingsStore)
                .background(TFTheme.background.ignoresSafeArea())
                .navigationTitle("Master FX")
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

/// Body extracted for snapshot testing (NavigationStack can't flatten).
struct FXPanelBody: View {
    @ObservedObject var store: FXSettingsStore

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                presetSection
                eqSection
                compSection
                reverbSection
                delaySection
                returnSection
            }
            .padding()
        }
    }

    // MARK: - Preset Picker

    private var presetSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Preset")
                .font(.headline)
                .foregroundStyle(TFTheme.textPrimary)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(FXPresetCatalog.all) { preset in
                        presetChip(preset)
                    }
                }
            }
        }
    }

    private func presetChip(_ preset: FXPreset) -> some View {
        let isActive = store.presetId == preset.id
        return Button {
            store.applyPreset(preset)
        } label: {
            Text(preset.name)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(isActive ? TFTheme.background : TFTheme.textPrimary)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(
                    RoundedRectangle(cornerRadius: 8)
                        .fill(isActive ? Color.accentColor : TFTheme.chipFill)
                )
        }
        .buttonStyle(.plain)
    }

    // MARK: - EQ Section

    private var eqSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionHeader("EQ", neutral: store.eq.isNeutral)

            fxSlider(
                label: "Low",
                value: Binding(
                    get: { store.eq.lowGainDb },
                    set: { store.eq = FXEQParams(
                        lowFreq: store.eq.lowFreq,
                        lowGainDb: $0,
                        midFreq: store.eq.midFreq,
                        midGainDb: store.eq.midGainDb,
                        highFreq: store.eq.highFreq,
                        highGainDb: store.eq.highGainDb
                    )}
                ),
                range: -24...24,
                format: "%.0f dB"
            )

            fxSlider(
                label: "Mid",
                value: Binding(
                    get: { store.eq.midGainDb },
                    set: { store.eq = FXEQParams(
                        lowFreq: store.eq.lowFreq,
                        lowGainDb: store.eq.lowGainDb,
                        midFreq: store.eq.midFreq,
                        midGainDb: $0,
                        highFreq: store.eq.highFreq,
                        highGainDb: store.eq.highGainDb
                    )}
                ),
                range: -24...24,
                format: "%.0f dB"
            )

            fxSlider(
                label: "High",
                value: Binding(
                    get: { store.eq.highGainDb },
                    set: { store.eq = FXEQParams(
                        lowFreq: store.eq.lowFreq,
                        lowGainDb: store.eq.lowGainDb,
                        midFreq: store.eq.midFreq,
                        midGainDb: store.eq.midGainDb,
                        highFreq: store.eq.highFreq,
                        highGainDb: $0
                    )}
                ),
                range: -24...24,
                format: "%.0f dB"
            )
        }
    }

    // MARK: - Compressor Section

    private var compSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionHeader("Compressor", neutral: store.comp.isNeutral)

            fxSlider(
                label: "Threshold",
                value: Binding(
                    get: { store.comp.thresholdDb },
                    set: { store.comp = FXCompParams(
                        thresholdDb: $0,
                        amountDb: store.comp.amountDb,
                        attackMs: store.comp.attackMs,
                        releaseMs: store.comp.releaseMs,
                        makeupDb: store.comp.makeupDb
                    )}
                ),
                range: -60...0,
                format: "%.0f dB"
            )

            fxSlider(
                label: "Amount",
                value: Binding(
                    get: { store.comp.amountDb },
                    set: { store.comp = FXCompParams(
                        thresholdDb: store.comp.thresholdDb,
                        amountDb: $0,
                        attackMs: store.comp.attackMs,
                        releaseMs: store.comp.releaseMs,
                        makeupDb: store.comp.makeupDb
                    )}
                ),
                range: 0...40,
                format: "%.0f dB"
            )

            fxSlider(
                label: "Makeup",
                value: Binding(
                    get: { store.comp.makeupDb },
                    set: { store.comp = FXCompParams(
                        thresholdDb: store.comp.thresholdDb,
                        amountDb: store.comp.amountDb,
                        attackMs: store.comp.attackMs,
                        releaseMs: store.comp.releaseMs,
                        makeupDb: $0
                    )}
                ),
                range: 0...40,
                format: "%.0f dB"
            )
        }
    }

    // MARK: - Reverb Section

    private var reverbSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionHeader("Reverb", neutral: store.reverb.isNeutral)

            fxSlider(
                label: "Mix",
                value: Binding(
                    get: { store.reverb.mix },
                    set: { store.reverb = FXReverbParams(
                        mix: $0,
                        sizeSeconds: store.reverb.sizeSeconds,
                        dampPercent: store.reverb.dampPercent
                    )}
                ),
                range: 0...100,
                format: "%.0f%%"
            )

            fxSlider(
                label: "Size",
                value: Binding(
                    get: { store.reverb.sizeSeconds },
                    set: { store.reverb = FXReverbParams(
                        mix: store.reverb.mix,
                        sizeSeconds: $0,
                        dampPercent: store.reverb.dampPercent
                    )}
                ),
                range: 0.3...6,
                format: "%.1f s"
            )
        }
    }

    // MARK: - Delay Section

    private var delaySection: some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionHeader("Delay", neutral: store.delay.isNeutral)

            fxSlider(
                label: "Time",
                value: Binding(
                    get: { store.delay.timeSec },
                    set: { store.delay = FXDelayParams(
                        timeSec: $0,
                        feedback: store.delay.feedback,
                        mix: store.delay.mix
                    )}
                ),
                range: 0...2,
                format: "%.2f s"
            )

            fxSlider(
                label: "Feedback",
                value: Binding(
                    get: { store.delay.feedback },
                    set: { store.delay = FXDelayParams(
                        timeSec: store.delay.timeSec,
                        feedback: $0,
                        mix: store.delay.mix
                    )}
                ),
                range: 0...95,
                format: "%.0f%%"
            )

            fxSlider(
                label: "Mix",
                value: Binding(
                    get: { store.delay.mix },
                    set: { store.delay = FXDelayParams(
                        timeSec: store.delay.timeSec,
                        feedback: store.delay.feedback,
                        mix: $0
                    )}
                ),
                range: 0...100,
                format: "%.0f%%"
            )
        }
    }

    // MARK: - Return Section

    private var returnSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("FX Return")
                .font(.headline)
                .foregroundStyle(TFTheme.textPrimary)

            fxSlider(
                label: "Level",
                value: Binding(
                    get: { store.fxReturnDb },
                    set: { store.fxReturnDb = $0 }
                ),
                range: -40...6,
                format: "%.0f dB"
            )
        }
    }

    // MARK: - Helpers

    private func sectionHeader(_ title: String, neutral: Bool) -> some View {
        HStack {
            Text(title)
                .font(.headline)
                .foregroundStyle(TFTheme.textPrimary)
            Spacer()
            if neutral {
                Text("OFF")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(TFTheme.textSecondary)
            }
        }
    }

    private func fxSlider(
        label: String,
        value: Binding<Double>,
        range: ClosedRange<Double>,
        format: String
    ) -> some View {
        HStack(spacing: 12) {
            Text(label)
                .font(.subheadline)
                .foregroundStyle(TFTheme.textSecondary)
                .frame(width: 70, alignment: .leading)

            Slider(value: value, in: range)
                .tint(TFTheme.faderTint)

            Text(String(format: format, value.wrappedValue))
                .font(.subheadline.monospacedDigit())
                .foregroundStyle(TFTheme.textPrimary)
                .frame(width: 60, alignment: .trailing)
        }
    }
}
