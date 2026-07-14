// FXPanelView.swift
//
// Master FX panel (D-022): preset picker, 3-band EQ, compressor,
// reverb send, delay send and FX return level, driving the Core
// FXPanelModel (which persists and pushes to the MusicBus). Shown as
// the FX segment of the mixer panel; slider ranges mirror the mobile
// FXPanelView.

import SwiftUI
import ToneForgeEngine
import JamDesktopCore

struct FXPanelView: View {
    @Bindable var model: FXPanelModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                presetSection
                section("EQ", neutral: model.eq.isNeutral) {
                    slider("Low", $model.eq.lowGainDb, -24...24, "%.0f dB")
                    slider("Mid", $model.eq.midGainDb, -24...24, "%.0f dB")
                    slider("High", $model.eq.highGainDb, -24...24, "%.0f dB")
                }
                section("Compressor", neutral: model.comp.isNeutral) {
                    slider("Threshold", $model.comp.thresholdDb, -60...0, "%.0f dB")
                    slider("Amount", $model.comp.amountDb, 0...40, "%.0f dB")
                    slider("Makeup", $model.comp.makeupDb, 0...40, "%.0f dB")
                }
                section("Reverb", neutral: model.reverb.isNeutral) {
                    slider("Mix", $model.reverb.mix, 0...100, "%.0f%%")
                    slider("Size", $model.reverb.sizeSeconds, 0.3...6, "%.1f s")
                }
                section("Delay", neutral: model.delay.isNeutral) {
                    slider("Time", $model.delay.timeSec, 0...2, "%.2f s")
                    slider("Feedback", $model.delay.feedback, 0...95, "%.0f%%")
                    slider("Mix", $model.delay.mix, 0...100, "%.0f%%")
                }
                section("FX Return", neutral: false) {
                    slider("Level", $model.fxReturnDb, -40...6, "%.0f dB")
                }
            }
            .padding(.vertical, 2)
        }
    }

    // MARK: - Presets

    private var presetSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Preset")
                    .font(.subheadline.weight(.semibold))
                Spacer()
                if model.presetId == nil, !model.settings.isNeutral {
                    Text("Custom")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(FXPresetCatalog.all) { preset in
                        presetChip(preset)
                    }
                }
            }
        }
    }

    private func presetChip(_ preset: FXPreset) -> some View {
        let isActive = model.presetId == preset.id
        return Button {
            model.applyPreset(preset)
        } label: {
            Text(preset.name)
                .font(.caption.weight(.medium))
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(
                    RoundedRectangle(cornerRadius: 6)
                        .fill(isActive ? Color.accentColor : Color.secondary.opacity(0.15))
                )
                .foregroundStyle(isActive ? Color.white : Color.primary)
        }
        .buttonStyle(.plain)
        .help("\(preset.name) effects preset")
    }

    // MARK: - Helpers

    private func section(
        _ title: String, neutral: Bool,
        @ViewBuilder content: () -> some View
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                Spacer()
                if neutral {
                    Text("OFF")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.secondary)
                }
            }
            content()
        }
    }

    private func slider(
        _ label: String,
        _ value: Binding<Double>,
        _ range: ClosedRange<Double>,
        _ format: String
    ) -> some View {
        HStack(spacing: 8) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(width: 62, alignment: .leading)
            Slider(value: value, in: range)
                .controlSize(.small)
                .accessibilityLabel(label)
            Text(String(format: format, value.wrappedValue))
                .font(.caption.monospacedDigit())
                .frame(width: 52, alignment: .trailing)
        }
    }
}
