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

    // Editable STEM WINDOW: the chop padded generously on each side (a full
    // sample's worth, min 8 s) so you can EXTEND past the chop's default
    // length — not just trim inside it — clamped to the stem. Fractions
    // (start/endFraction) run over this window, not the raw chop.
    private var windowPad: Double { max(8, target.chop.endSec - target.chop.startSec) }
    private var windowStart: Double { max(0, target.chop.startSec - windowPad) }
    private var windowEnd: Double {
        min(target.stemDurationSec, target.chop.endSec + windowPad)
    }
    private var windowDuration: Double { max(0.001, windowEnd - windowStart) }
    /// Where the chop's own bounds sit within the window (the reset target).
    private var chopStartFraction: Double {
        (target.chop.startSec - windowStart) / windowDuration
    }
    private var chopEndFraction: Double {
        (target.chop.endSec - windowStart) / windowDuration
    }

    // Peaks over the editable window (subset of the full stem peaks).
    private var chopPeaks: [Float] {
        guard !target.peaks.isEmpty, target.stemDurationSec > 0 else { return [] }
        let n = Double(target.peaks.count)
        let startIdx = Int((windowStart / target.stemDurationSec) * n)
        let endIdx = Int((windowEnd / target.stemDurationSec) * n)
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
                        target.onPreview(windowStart + s * windowDuration,
                                         windowStart + e * windowDuration)
                    },
                    durationSec: windowDuration
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
                    Button("Done") { dismiss() }
                }
                // HONESTY (UX audit fix #2): mobile chop-edit persistence is
                // a stub (saveAndDismiss only printed). Don't offer a Save
                // that discards work — label the surface as preview-only.
                ToolbarItem(placement: .confirmationAction) {
                    Text("Preview only")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .onAppear {
            // Start at the chop's own bounds within the wider window.
            startFraction = chopStartFraction
            endFraction = chopEndFraction
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
                target.onPreview(windowStart + startFraction * windowDuration,
                                 windowStart + endFraction * windowDuration)
            }

            // (Split removed until implemented — a visible button that did
            // nothing was worse than its absence. UX audit fix #2.)

            // Reset to original
            actionButton(
                icon: "arrow.uturn.backward",
                label: "Reset",
                color: .secondary,
                disabled: !hasChanges
            ) {
                withAnimation {
                    startFraction = chopStartFraction
                    endFraction = chopEndFraction
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
        abs(startFraction - chopStartFraction) > 0.001
            || abs(endFraction - chopEndFraction) > 0.001
    }

    // Persistence intentionally absent: desktop persists via ChopEditStore;
    // mobile has no store yet, so this sheet is preview-only (see toolbar).
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
