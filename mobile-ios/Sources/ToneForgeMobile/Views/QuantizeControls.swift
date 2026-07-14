// QuantizeControls.swift
//
// The three settings that shape sample-trigger timing:
//   - Quantize:  Off | 1/8 | 1/4 | 1/2 | 1 bar | phrase
//   - Hold/Toggle: whether taps latch on second press
//   - Beat/Bar:  interpretation of the metronome grid at "1"
//
// Bindings write through to `SampleSettingsStore` (which auto-saves)
// via the AppState wiring in `wireSampleSettings`. No local state.

import SwiftUI
import ToneForgeEngine

struct QuantizeControls: View {
    @Binding var quantize: QuantizeMode
    @Binding var hold: HoldMode
    @Binding var beatBar: BeatBarMode

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                Menu {
                    Picker("Quantize", selection: $quantize) {
                        ForEach(QuantizeMode.allCases, id: \.self) { m in
                            Text(m.rawValue).tag(m)
                        }
                    }
                } label: {
                    labelCapsule("Q", value: quantize.rawValue)
                }
                .accessibilityLabel("Quantize")
                .accessibilityValue(quantize.rawValue)

                // Simple toggle - tap cycles between Hold/Toggle
                Button {
                    hold = (hold == .hold) ? .toggle : .hold
                } label: {
                    labelCapsule("H", value: hold == .hold ? "Hold" : "Toggle")
                }
                .accessibilityLabel("Pad hold mode")
                .accessibilityValue(hold == .hold ? "Hold" : "Toggle")

                // Simple toggle - tap cycles between Beat/Bar
                Button {
                    beatBar = (beatBar == .beat) ? .bar : .beat
                } label: {
                    labelCapsule("Grid", value: beatBar == .beat ? "Beat" : "Bar")
                }
                .accessibilityLabel("Quantize grid")
                .accessibilityValue(beatBar == .beat ? "Beat" : "Bar")
            }
        }
        .font(.caption)
    }

    private func labelCapsule(_ tag: String, value: String) -> some View {
        HStack(spacing: 4) {
            Text(tag)
                .font(.caption2)
                .foregroundStyle(TFTheme.textSecondary)
            Text(value)
                .font(TFTheme.chipFont)
                .foregroundStyle(TFTheme.textPrimary)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(Capsule().fill(TFTheme.chipFill))
        .overlay(Capsule().stroke(TFTheme.stroke, lineWidth: 1))
        .fixedSize()
    }
}
