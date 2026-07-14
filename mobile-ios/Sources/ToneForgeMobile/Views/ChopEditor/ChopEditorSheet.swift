// ChopEditorSheet.swift
//
// Sheet presenting a single chop for boundary editing (D-023 Phase 2).
// Shows the waveform with draggable start/end handles, plus actions:
//   - Play: preview the current selection
//   - Split: divide the chop at the current playhead
//   - Merge: combine with an adjacent chop (if applicable)
//   - Reset: restore original boundaries
//
// Edits are persisted via ChopEditStore and applied through
// resolvedChops() at playback time.

import SwiftUI
import ToneForgeEngine

/// Target for opening the chop editor.
struct ChopEditorTarget: Identifiable, Equatable {
    let id = UUID()
    /// Preset key (e.g., "harmonic", "sections").
    let presetKey: String
    /// Original chop from the bundle.
    let chop: Chop
    /// Peak data for waveform display.
    let peaks: [Float]
    /// Total stem duration (for context).
    let stemDurationSec: Double
    /// Callback for previewing the chop.
    let onPreview: (Double, Double) -> Void

    static func == (lhs: ChopEditorTarget, rhs: ChopEditorTarget) -> Bool {
        lhs.id == rhs.id
    }
}

struct ChopEditorSheet: View {
    let target: ChopEditorTarget
    @Environment(\.dismiss) private var dismiss

    // Editing state (fractions of the chop's stem range, not global)
    @State private var startFraction: Double = 0
    @State private var endFraction: Double = 1

    // Original values for reset
    private var originalStartFraction: Double {
        target.chop.startSec / target.stemDurationSec
    }
    private var originalEndFraction: Double {
        target.chop.endSec / target.stemDurationSec
    }

    // Chop-relative peaks (subset of full stem peaks)
    private var chopPeaks: [Float] {
        guard !target.peaks.isEmpty else { return [] }
        let startIdx = Int(originalStartFraction * Double(target.peaks.count))
        let endIdx = Int(originalEndFraction * Double(target.peaks.count))
        guard startIdx < endIdx, startIdx >= 0, endIdx <= target.peaks.count else {
            return target.peaks
        }
        return Array(target.peaks[startIdx..<endIdx])
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                // Header
                chopHeader

                // Waveform editor
                ChopWaveformView(
                    peaks: chopPeaks,
                    startFraction: $startFraction,
                    endFraction: $endFraction,
                    onPlay: { s, e in
                        let chopDuration = target.chop.endSec - target.chop.startSec
                        let absStart = target.chop.startSec + s * chopDuration
                        let absEnd = target.chop.startSec + e * chopDuration
                        target.onPreview(absStart, absEnd)
                    },
                    durationSec: target.chop.durationSec
                )
                .padding(.horizontal)

                // Action buttons
                actionButtons

                Spacer()
            }
            .padding(.top)
            .background(Color.black)
            .navigationTitle("Edit Chop")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { saveAndDismiss() }
                        .fontWeight(.semibold)
                        .disabled(!hasChanges)
                }
            }
        }
        .onAppear {
            // Initialize to full chop range
            startFraction = 0
            endFraction = 1
        }
    }

    // MARK: - Header

    private var chopHeader: some View {
        VStack(spacing: 4) {
            if let label = target.chop.sectionLabel {
                Text(label.uppercased())
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.accentColor)
            }

            if let symbol = target.chop.chordSymbol {
                Text(symbol)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(.white)
            }

            Text("Chop #\(target.chop.idx + 1)")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - Actions

    private var actionButtons: some View {
        HStack(spacing: 16) {
            // Play preview
            actionButton(
                icon: "play.fill",
                label: "Play",
                color: .green
            ) {
                let chopDuration = target.chop.durationSec
                let absStart = target.chop.startSec + startFraction * chopDuration
                let absEnd = target.chop.startSec + endFraction * chopDuration
                target.onPreview(absStart, absEnd)
            }

            // Split at midpoint
            actionButton(
                icon: "scissors",
                label: "Split",
                color: .orange
            ) {
                // FUTURE: Implement split - adds a ChopSplit at the midpoint
                // For now, this is a placeholder
            }

            // Reset to original
            actionButton(
                icon: "arrow.uturn.backward",
                label: "Reset",
                color: .secondary,
                disabled: !hasChanges
            ) {
                withAnimation {
                    startFraction = 0
                    endFraction = 1
                }
            }
        }
        .padding(.horizontal)
    }

    @ViewBuilder
    private func actionButton(
        icon: String,
        label: String,
        color: Color,
        disabled: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(spacing: 6) {
                Image(systemName: icon)
                    .font(.title2)
                Text(label)
                    .font(.caption2)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .background(color.opacity(disabled ? 0.1 : 0.2))
            .foregroundStyle(disabled ? .secondary : color)
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .disabled(disabled)
    }

    // MARK: - Helpers

    private var hasChanges: Bool {
        abs(startFraction) > 0.001 || abs(endFraction - 1) > 0.001
    }

    private func saveAndDismiss() {
        // Calculate new absolute boundaries
        let chopDuration = target.chop.durationSec
        let newStart = target.chop.startSec + startFraction * chopDuration
        let newEnd = target.chop.startSec + endFraction * chopDuration

        // FUTURE: Persist via ChopEditStore
        // For now, just print and dismiss
        print("[ChopEditor] Saving: \(newStart) - \(newEnd)")

        dismiss()
    }
}

// MARK: - Preview

#if DEBUG
struct ChopEditorSheet_Previews: PreviewProvider {
    static var previews: some View {
        ChopEditorSheet(
            target: ChopEditorTarget(
                presetKey: "harmonic",
                chop: Chop(
                    idx: 3,
                    startSec: 12.5,
                    endSec: 16.2,
                    durationSec: 3.7,
                    kind: "chord",
                    root: 2,
                    sectionLabel: "Verse",
                    chordSymbol: "Dm7",
                    colorHint: nil
                ),
                peaks: (0..<200).map { i in
                    let t = Float(i) / 200
                    return abs(sin(t * .pi * 6)) * (0.4 + 0.6 * sin(t * .pi * 1.5))
                },
                stemDurationSec: 180.0,
                onPreview: { s, e in print("Preview: \(s) - \(e)") }
            )
        )
        .preferredColorScheme(.dark)
    }
}
#endif
