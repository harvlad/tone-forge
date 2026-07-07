// TempoStrip.swift
//
// Song-less tempo controls on the Play tab (D-016: the synthetic
// quantize grid when no song is loaded): BPM stepper (60–200),
// time-signature picker (3/4, 4/4, 6/8), bar.beat position readout,
// count-in toggle and metronome toggle. Bindings write through to
// `SketchSettingsStore` (which auto-saves). The position label is
// computed by the parent (needs songSeconds + tempo) so this view
// stays state-free — same pattern as QuantizeControls.

import SwiftUI

struct TempoStrip: View {
    @Binding var bpm: Double
    @Binding var timeSigNumerator: Int
    @Binding var metronomeEnabled: Bool
    @Binding var countInEnabled: Bool
    /// "bar.beat" readout ("3.2"), "Count-in" during the lead bar, or
    /// nil to hide (transport stopped).
    var positionLabel: String? = nil

    var body: some View {
        HStack(spacing: 12) {
            // BPM readout + stepper. Stepper auto-repeats on hold so
            // big jumps don't require 60 taps.
            HStack(spacing: 6) {
                Text("\(Int(bpm))")
                    .font(.callout.monospacedDigit().weight(.semibold))
                Text("BPM")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Stepper(
                "BPM",
                value: $bpm,
                in: SketchSettingsStore.bpmRange,
                step: 1
            )
            .labelsHidden()

            Menu {
                Picker("Time signature", selection: $timeSigNumerator) {
                    ForEach(SketchSettingsStore.timeSigOptions, id: \.self) { n in
                        Text(SketchSettingsStore.timeSigLabel(n)).tag(n)
                    }
                }
            } label: {
                HStack(spacing: 4) {
                    Text(SketchSettingsStore.timeSigLabel(timeSigNumerator))
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.primary)
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(Capsule().fill(Color.gray.opacity(0.20)))
            }

            Spacer()

            if let positionLabel {
                Text(positionLabel)
                    .font(.caption.monospacedDigit().weight(.medium))
                    .foregroundStyle(.secondary)
            }

            // 1-bar count-in before sketch recording.
            Button {
                countInEnabled.toggle()
            } label: {
                Image(systemName: "timer")
                    .font(.body)
                    .foregroundStyle(countInEnabled ? Color.accentColor : Color.secondary)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .background(
                        Capsule().fill(
                            countInEnabled
                                ? Color.accentColor.opacity(0.18)
                                : Color.gray.opacity(0.20)
                        )
                    )
            }
            .buttonStyle(.plain)
            .accessibilityLabel(countInEnabled ? "Count-in on" : "Count-in off")

            Button {
                metronomeEnabled.toggle()
            } label: {
                Image(systemName: "metronome.fill")
                    .font(.body)
                    .foregroundStyle(metronomeEnabled ? Color.accentColor : Color.secondary)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .background(
                        Capsule().fill(
                            metronomeEnabled
                                ? Color.accentColor.opacity(0.18)
                                : Color.gray.opacity(0.20)
                        )
                    )
            }
            .buttonStyle(.plain)
            .accessibilityLabel(metronomeEnabled ? "Metronome on" : "Metronome off")
        }
    }
}
