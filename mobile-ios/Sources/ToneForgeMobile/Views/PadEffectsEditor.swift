// PadEffectsEditor.swift
//
// Per-pad delay + resonant-lowpass filter sliders. Long-press on a
// ModeGridView pad opens this sheet; every slider drag mutates the
// user override for that (packId, padIdx) via
// SampleSettingsStore.setPadEffectsOverride, so the next tap on the
// pad picks up the new values immediately.
//
// Design notes:
//   * Slider ranges come straight from `SamplePadEffects.clamped()`
//     — the store re-clamps on write so an out-of-bounds drag can't
//     bypass the guard.
//   * "Preview" fires the injected `onPreview` (the grid routes it
//     through the ContributionEventBus) so the user can hear the
//     effect without dismissing the sheet.
//   * "Reset" clears the override so the pad falls back to the
//     manifest baseline (or `.neutral` if none).

import SwiftUI
import ToneForgeEngine

struct PadEffectsEditor: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    let packId: String
    let padIdx: Int
    let padName: String
    /// Manifest baseline for this pad (nil = no pack-defined effects).
    /// Preserved so "Reset" can revert to the baseline instead of
    /// `.neutral` when the pack ships with effect defaults.
    let manifestBaseline: SamplePadEffects?
    /// PadIndex rawValue of the grid pad — the transforms section
    /// (P4) keys chains by grid position, not pack coordinates.
    let gridRaw: Int
    /// Fires the pad through the contribution bus (down + short hold
    /// + up) — the editor never triggers audio directly.
    let onPreview: () -> Void

    /// Local scratch state — mirrors the store's current value on
    /// open so slider drags feel snappy without waiting for Combine
    /// republish round-trips.
    @State private var effects: SamplePadEffects = .neutral

    var body: some View {
        NavigationStack {
            Form {
                Section("Delay") {
                    slider(
                        title: "Time",
                        value: $effects.delayTimeSec,
                        range: 0...2, step: 0.01,
                        format: { String(format: "%.0f ms", $0 * 1000) }
                    )
                    slider(
                        title: "Feedback",
                        value: $effects.delayFeedback,
                        range: 0...95, step: 1,
                        format: { String(format: "%.0f%%", $0) }
                    )
                    slider(
                        title: "Mix",
                        value: $effects.delayMix,
                        range: 0...100, step: 1,
                        format: { String(format: "%.0f%%", $0) }
                    )
                }

                Section("Filter") {
                    slider(
                        title: "Cutoff",
                        value: $effects.filterCutoffHz,
                        range: 100...20_000, step: 10,
                        format: cutoffLabel
                    )
                    slider(
                        title: "Resonance",
                        value: $effects.filterResonanceDb,
                        range: 0...24, step: 0.1,
                        format: { String(format: "%.1f dB", $0) }
                    )
                }

                // P4: transform chain editor (persisted per pad+mode,
                // rendered on edit, bake → new local sample overlaid
                // on this pad).
                PadTransformSection(gridRaw: gridRaw)

                Section {
                    Button("Preview") {
                        onPreview()
                    }
                    Button("Reset to pack default", role: .destructive) {
                        appState.sampleSettings.setPadEffectsOverride(
                            nil, packId: packId, padIdx: padIdx
                        )
                        effects = manifestBaseline ?? .neutral
                    }
                } footer: {
                    Text(overrideState)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle(padName)
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .onAppear {
                effects = appState.sampleSettings.effectivePadEffects(
                    packId: packId, padIdx: padIdx,
                    manifestBaseline: manifestBaseline
                )
            }
            .onChange(of: effects) { _, newValue in
                appState.sampleSettings.setPadEffectsOverride(
                    newValue, packId: packId, padIdx: padIdx
                )
            }
        }
    }

    /// Human-readable text for the current override status. Helps the
    /// user tell "I edited this" from "this is the pack default".
    private var overrideState: String {
        if appState.sampleSettings.padEffectsOverride(
            packId: packId, padIdx: padIdx
        ) != nil {
            return "Custom overrides in effect. Changes persist across launches."
        }
        if manifestBaseline != nil {
            return "Using pack-defined defaults."
        }
        return "Using neutral defaults."
    }

    @ViewBuilder
    private func slider(
        title: String,
        value: Binding<Double>,
        range: ClosedRange<Double>,
        step: Double,
        format: @escaping (Double) -> String
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title)
                Spacer()
                Text(format(value.wrappedValue))
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            Slider(value: value, in: range, step: step)
        }
    }

    /// Cutoff readout — Hz below 1 kHz, kHz above.
    private func cutoffLabel(_ hz: Double) -> String {
        if hz >= 1_000 {
            return String(format: "%.1f kHz", hz / 1_000)
        }
        return String(format: "%.0f Hz", hz)
    }
}
