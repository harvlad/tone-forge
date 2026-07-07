// LayerFader.swift
//
// The "Your Layer" dB slider on the Play tab that drives the shared
// contribution bus. Persisted via `SampleSettingsStore.layerFaderDb`;
// the AppState Combine sink converts dB → linear and applies it to
// the sharedBus (`AudioEngine.setLayerGain`) in real time.

import SwiftUI

struct LayerFader: View {
    @Binding var dbValue: Double

    /// Fader range in dB. Matches the mockup's mixer slider range.
    private let minDb: Double = -60
    private let maxDb: Double = 6

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Your Layer")
                    .font(.caption.weight(.medium))
                Spacer()
                Text(String(format: "%+.0f dB", dbValue))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            Slider(value: $dbValue, in: minDb...maxDb)
        }
    }
}
