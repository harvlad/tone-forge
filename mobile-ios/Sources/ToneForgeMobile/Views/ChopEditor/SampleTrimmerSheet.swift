// SampleTrimmerSheet.swift
//
// Waveform trimmer for pack samples, accessed via radial menu "Chop" action.
// Shows the sample waveform with draggable start/end handles to adjust
// playback region. Uses ChopWaveformView for the actual trim UI.

import SwiftUI

/// Target for the sample trimmer sheet.
struct SampleTrimmerTarget: Identifiable, Equatable {
    let id = UUID()
    let packId: String
    let padIdx: Int
    let padName: String
    let gridRow: Int
    let gridCol: Int
    /// Duration of the sample in seconds.
    let durationSec: Double
    /// Peak data for waveform display (if available).
    let peaks: [Float]
    /// Committed trim the sheet opens with (handles start here).
    var initialStart: Double = 0
    var initialEnd: Double = 1

    static func == (lhs: SampleTrimmerTarget, rhs: SampleTrimmerTarget) -> Bool {
        lhs.id == rhs.id
    }
}

struct SampleTrimmerSheet: View {
    let target: SampleTrimmerTarget
    /// Called when the user taps preview or the waveform, with the current trim bounds.
    let onPreview: (Double, Double) -> Void
    /// Commits the trim (scheduler stores it; pads play + draw the
    /// trimmed region). nil hides the Apply button.
    var onApply: ((Double, Double) -> Void)? = nil

    @Environment(\.dismiss) private var dismiss

    @State private var startFraction: Double = 0
    @State private var endFraction: Double = 1
    @State private var seeded = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                // Header
                VStack(spacing: 4) {
                    Text(target.padName)
                        .font(.title2.weight(.bold))
                        .foregroundStyle(.white)

                    Text("Trim sample boundaries")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(.top)

                // Waveform trimmer
                ChopWaveformView(
                    peaks: target.peaks,
                    startFraction: $startFraction,
                    endFraction: $endFraction,
                    onPlay: { _, _ in onPreview(startFraction, endFraction) },
                    durationSec: target.durationSec
                )
                .padding(.horizontal)

                // Time info
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Start")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Text(formatTime(startFraction * target.durationSec))
                            .font(.caption.monospacedDigit().weight(.medium))
                            .foregroundStyle(.white)
                    }

                    Spacer()

                    VStack(spacing: 2) {
                        Text("Duration")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Text(formatTime((endFraction - startFraction) * target.durationSec))
                            .font(.caption.monospacedDigit().weight(.semibold))
                            .foregroundStyle(Color.accentColor)
                    }

                    Spacer()

                    VStack(alignment: .trailing, spacing: 2) {
                        Text("End")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Text(formatTime(endFraction * target.durationSec))
                            .font(.caption.monospacedDigit().weight(.medium))
                            .foregroundStyle(.white)
                    }
                }
                .padding(.horizontal, 24)

                // Action buttons
                HStack(spacing: 16) {
                    Button {
                        onPreview(startFraction, endFraction)
                    } label: {
                        Label("Preview", systemImage: "play.fill")
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                            .background(Color.green.opacity(0.2))
                            .foregroundStyle(.green)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                    }

                    Button {
                        withAnimation {
                            startFraction = 0
                            endFraction = 1
                        }
                    } label: {
                        Label("Reset", systemImage: "arrow.uturn.backward")
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                            .background(Color.secondary.opacity(0.2))
                            .foregroundStyle(.secondary)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                    .disabled(!hasChanges)
                }
                .padding(.horizontal)

                Spacer()
            }
            .background(Color.black)
            .navigationTitle("Trim Sample")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    // Real persistence now exists (scheduler padTrims,
                    // survives relaunch) — Apply is honest again.
                    if let onApply {
                        Button("Apply") {
                            onApply(startFraction, endFraction)
                            dismiss()
                        }
                        .fontWeight(.semibold)
                    }
                }
            }
            .onAppear {
                guard !seeded else { return }
                seeded = true
                startFraction = target.initialStart
                endFraction = target.initialEnd
            }
        }
    }

    private var hasChanges: Bool {
        abs(startFraction) > 0.001 || abs(endFraction - 1) > 0.001
    }

    private func formatTime(_ seconds: Double) -> String {
        let total = max(0, seconds)
        let secs = Int(total)
        let ms = Int((total.truncatingRemainder(dividingBy: 1)) * 100)
        return String(format: "%d.%02ds", secs, ms)
    }
}

// MARK: - Preview

#if DEBUG
struct SampleTrimmerSheet_Previews: PreviewProvider {
    static var previews: some View {
        SampleTrimmerSheet(
            target: SampleTrimmerTarget(
                packId: "lo-fi-hiphop",
                padIdx: 5,
                padName: "Snare",
                gridRow: 7,
                gridCol: 2,
                durationSec: 0.8,
                peaks: (0..<100).map { i in
                    let t = Float(i) / 100
                    return abs(sin(t * .pi * 4)) * (0.3 + 0.7 * exp(-t * 3))
                }
            ),
            onPreview: { start, end in print("Preview \(start) - \(end)") }
        )
        .preferredColorScheme(.dark)
    }
}
#endif
