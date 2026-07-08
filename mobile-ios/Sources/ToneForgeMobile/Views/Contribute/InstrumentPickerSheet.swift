// InstrumentPickerSheet.swift
//
// Synth preset picker + octave/brightness controls for the Contribute
// surface Instrument mode (D-022 Phase 8). Presented as a sheet from
// the Contribute tab when the user taps a gear icon near the
// Instrument/Samples segment switch.

import SwiftUI

struct InstrumentPickerSheet: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    /// Observe settings directly so nested @Published fields trigger
    /// updates (AppState doesn't re-publish nested ObservableObjects).
    private var settings: SampleSettingsStore { appState.sampleSettings }

    var body: some View {
        NavigationStack {
            Form {
                presetsSection
                controlsSection
            }
            .navigationTitle("Instrument")
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

    // MARK: - Presets

    private var presetsSection: some View {
        Section("Sound") {
            ForEach(SynthPresetCategory.allCases, id: \.self) { category in
                let presets = SynthPresetCatalog.presets(for: category)
                if !presets.isEmpty {
                    DisclosureGroup(category.displayName) {
                        ForEach(presets) { preset in
                            presetRow(preset)
                        }
                    }
                }
            }
        }
    }

    private func presetRow(_ preset: SynthPreset) -> some View {
        Button {
            applyPreset(preset)
        } label: {
            HStack {
                Text(preset.name)
                    .foregroundStyle(TFTheme.textPrimary)
                Spacer()
                if settings.instrumentPresetId == preset.id {
                    Image(systemName: "checkmark")
                        .foregroundStyle(Color.accentColor)
                }
            }
        }
    }

    private func applyPreset(_ preset: SynthPreset) {
        settings.instrumentPresetId = preset.id
        // Apply preset params to the pad synth, incorporating the
        // current brightness override if != 1.0
        var params = preset.params
        let brightnessOverride = settings.instrumentBrightness
        if brightnessOverride != 1.0 {
            params.brightness = preset.params.brightness * brightnessOverride
        }
        appState.padSynth.update(params: params)
    }

    // MARK: - Controls

    private var controlsSection: some View {
        Section("Controls") {
            octaveRow
            brightnessRow
        }
    }

    private var octaveRow: some View {
        Stepper(value: octaveBinding, in: -3...3) {
            HStack {
                Text("Octave")
                Spacer()
                Text(octaveLabel)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var octaveBinding: Binding<Int> {
        Binding(
            get: { settings.instrumentOctaveShift },
            set: { settings.instrumentOctaveShift = $0 }
        )
    }

    private var octaveLabel: String {
        let shift = settings.instrumentOctaveShift
        if shift > 0 { return "+\(shift)" }
        if shift < 0 { return "\(shift)" }
        return "0"
    }

    private var brightnessRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Tone")
                Spacer()
                Text(brightnessLabel)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            Slider(value: brightnessBinding, in: 0.5...2.0)
                .onChange(of: settings.instrumentBrightness) {
                    applyCurrentPreset()
                }
        }
    }

    private var brightnessBinding: Binding<Double> {
        Binding(
            get: { settings.instrumentBrightness },
            set: { settings.instrumentBrightness = $0 }
        )
    }

    private var brightnessLabel: String {
        let b = settings.instrumentBrightness
        if b < 0.8 { return "Dark" }
        if b > 1.2 { return "Bright" }
        return "Neutral"
    }

    /// Re-apply the current preset with the updated brightness.
    private func applyCurrentPreset() {
        let presetId = settings.instrumentPresetId
        let preset = SynthPresetCatalog.preset(id: presetId)
            ?? SynthPresetCatalog.defaultPreset
        var params = preset.params
        params.brightness = preset.params.brightness * settings.instrumentBrightness
        appState.padSynth.update(params: params)
    }
}
