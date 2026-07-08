// LayerFader.swift
//
// The "Your Layer" dB slider on the Play tab that drives the shared
// contribution bus. Persisted via `SampleSettingsStore.layerFaderDb`;
// the AppState Combine sink converts dB → linear and applies it to
// the sharedBus (`AudioEngine.setLayerGain`) in real time.
//
// Styled per the mockup: a card row — speaker icon, slider, and a
// stacked "Layer / X.X dB" readout on the trailing edge. Owns its
// horizontal margins so call sites (Contribute, Chord Pads) can drop
// it in bare.

import SwiftUI

struct LayerFader: View {
    @Binding var dbValue: Double

    /// Fader range in dB. Matches the mockup's mixer slider range.
    private let minDb: Double = -60
    private let maxDb: Double = 6

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "speaker.wave.2.fill")
                .font(.caption)
                .foregroundStyle(TFTheme.textSecondary)
            Slider(value: $dbValue, in: minDb...maxDb)
                .accessibilityLabel("Your layer level")
            VStack(alignment: .trailing, spacing: 0) {
                Text("Layer")
                    .font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
                Text(String(format: "%+.1f dB", dbValue))
                    .font(TFTheme.readout)
                    .foregroundStyle(TFTheme.textPrimary)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .tfCard()
        .padding(.horizontal, 12)
    }
}
