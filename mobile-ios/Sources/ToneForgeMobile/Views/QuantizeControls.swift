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

            Menu {
                Picker("Hold", selection: $hold) {
                    Text("Hold").tag(HoldMode.hold)
                    Text("Toggle").tag(HoldMode.toggle)
                }
            } label: {
                labelCapsule("H", value: hold == .hold ? "Hold" : "Toggle")
            }

            Menu {
                Picker("Beat/Bar", selection: $beatBar) {
                    Text("Beat").tag(BeatBarMode.beat)
                    Text("Bar").tag(BeatBarMode.bar)
                }
            } label: {
                labelCapsule("Grid", value: beatBar == .beat ? "Beat" : "Bar")
            }

            Spacer()
        }
        .font(.caption)
    }

    private func labelCapsule(_ tag: String, value: String) -> some View {
        HStack(spacing: 4) {
            Text(tag)
                .font(.caption2)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.caption.weight(.medium))
                .foregroundStyle(.primary)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(
            Capsule().fill(Color.gray.opacity(0.20))
        )
    }
}
