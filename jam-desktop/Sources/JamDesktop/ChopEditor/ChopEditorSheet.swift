// ChopEditorSheet.swift
//
// Chop boundary editor (iOS ChopEditorSheet counterpart): shows the
// chop's stem waveform with draggable start/end handles inside a
// context window (original chop ± half its duration), previews the
// current selection through ChopPlayer's file-segment path, and
// persists a ChopBoundaryEdit via ChopEditStore on save. The store's
// onEditsChanged callback re-resolves the Launchpad grid and
// sequencer adapter (SessionController), so edits are audible
// immediately.
//
// Boundary edits only — splits/merges stay engine-supported but
// un-exposed, matching the iOS editor's current scope. Synthetic
// chops (negative idx) can't reach here: the pad context menu
// disables editing for them.

import SwiftUI
import ToneForgeEngine
import JamDesktopCore
import JamDesktopAudio

/// What the editor is editing. The chop carries its *current*
/// (possibly already-edited) boundaries; the original bundle
/// boundaries come from the saved edit, if any.
struct ChopEditorTarget: Identifiable {
    let id = UUID()
    let analysisId: String
    let presetKey: String
    let chop: Chop
    let stemURL: URL
    let stemDurationSec: Double
}

struct ChopEditorSheet: View {
    let target: ChopEditorTarget

    @EnvironmentObject private var session: SessionController
    @Environment(\.dismiss) private var dismiss

    @State private var startSec: Double = 0
    @State private var endSec: Double = 1
    @State private var peaks: [Float] = []

    /// Bundle-truth boundaries (for reset + the context window).
    private var originalStart: Double { existingEdit?.originalStart ?? target.chop.startSec }
    private var originalEnd: Double { existingEdit?.originalEnd ?? target.chop.endSec }

    private var existingEdit: ChopBoundaryEdit? {
        session.chopEditStore
            .edits(analysisId: target.analysisId, presetKey: target.presetKey)
            .boundaryEdits[target.chop.idx]
    }

    /// Context window: the original chop padded by half its duration
    /// on each side (at least 1s), clamped to the stem.
    private var windowStart: Double {
        max(0, originalStart - max(1, (originalEnd - originalStart) * 0.5))
    }
    private var windowEnd: Double {
        min(
            target.stemDurationSec,
            originalEnd + max(1, (originalEnd - originalStart) * 0.5)
        )
    }

    private var hasChanges: Bool {
        abs(startSec - target.chop.startSec) > 0.001
            || abs(endSec - target.chop.endSec) > 0.001
    }

    private var isEdited: Bool {
        abs(startSec - originalStart) > 0.001
            || abs(endSec - originalEnd) > 0.001
    }

    var body: some View {
        VStack(spacing: 16) {
            header

            ChopWaveformEditor(
                peaks: peaks,
                windowStart: windowStart,
                windowEnd: windowEnd,
                originalStart: originalStart,
                originalEnd: originalEnd,
                startSec: $startSec,
                endSec: $endSec
            )
            .frame(height: 140)

            boundsReadout

            HStack(spacing: 12) {
                Button {
                    session.chopPlayer.trigger(
                        file: target.stemURL,
                        startSec: startSec, endSec: endSec
                    )
                } label: {
                    Label("Play", systemImage: "play.fill")
                }
                .keyboardShortcut(.space, modifiers: [])

                Button {
                    startSec = originalStart
                    endSec = originalEnd
                } label: {
                    Label("Reset", systemImage: "arrow.uturn.backward")
                }
                .disabled(!isEdited)

                Spacer()

                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Save") { save() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!hasChanges)
            }
        }
        .padding(16)
        .frame(minWidth: 480)
        .background(JamTheme.background)
        .preferredColorScheme(.dark)
        .tint(JamTheme.accent)
        .onAppear {
            startSec = target.chop.startSec
            endSec = target.chop.endSec
        }
        .task { await loadPeaks() }
    }

    private var header: some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text(
                    target.chop.chordSymbol
                        ?? target.chop.sectionLabel
                        ?? "Chop \(target.chop.idx + 1)"
                )
                .font(.title3.bold())
                Text("\(target.presetKey.capitalized) · chop \(target.chop.idx + 1)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if existingEdit != nil {
                Text("EDITED")
                    .font(.caption2.bold())
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(JamTheme.accent.opacity(0.2), in: Capsule())
                    .foregroundStyle(JamTheme.accent)
            }
        }
    }

    private var boundsReadout: some View {
        HStack {
            Text(String(format: "%.2fs", startSec))
            Spacer()
            Text(String(format: "%.2fs long", endSec - startSec))
                .foregroundStyle(.secondary)
            Spacer()
            Text(String(format: "%.2fs", endSec))
        }
        .font(.caption.monospacedDigit())
    }

    private func save() {
        var edit = ChopBoundaryEdit(
            chopIndex: target.chop.idx,
            originalStart: originalStart,
            originalEnd: originalEnd,
            editedStart: startSec,
            editedEnd: endSec
        )
        edit.clamp(maxEnd: target.stemDurationSec)
        session.chopEditStore.setBoundary(
            edit,
            analysisId: target.analysisId,
            presetKey: target.presetKey
        )
        dismiss()
    }

    private func loadPeaks() async {
        let url = target.stemURL
        let start = windowStart
        let end = windowEnd
        let extracted = await Task.detached(priority: .userInitiated) {
            (try? WaveformPeakExtractor.peaks(
                file: url, binCount: 400, startSec: start, endSec: end
            )) ?? []
        }.value
        peaks = extracted
    }
}

// MARK: - Waveform + handles

/// Peak strip with a highlighted [startSec, endSec] selection and two
/// draggable boundary handles. Drags grab whichever handle is closer
/// to the touch-down point. Original boundaries render as faint
/// reference ticks.
private struct ChopWaveformEditor: View {
    let peaks: [Float]
    let windowStart: Double
    let windowEnd: Double
    let originalStart: Double
    let originalEnd: Double
    @Binding var startSec: Double
    @Binding var endSec: Double

    private enum Handle { case start, end }
    @State private var activeHandle: Handle?

    private static let minLengthSec = 0.05

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width
            ZStack {
                waveform(size: geo.size)
                selectionOverlay(size: geo.size)
            }
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        let sec = seconds(atX: value.location.x, width: width)
                        if activeHandle == nil {
                            activeHandle =
                                abs(sec - startSec) <= abs(sec - endSec)
                                ? .start : .end
                        }
                        switch activeHandle {
                        case .start:
                            startSec = min(
                                max(windowStart, sec),
                                endSec - Self.minLengthSec
                            )
                        case .end:
                            endSec = max(
                                min(windowEnd, sec),
                                startSec + Self.minLengthSec
                            )
                        case nil:
                            break
                        }
                    }
                    .onEnded { _ in activeHandle = nil }
            )
        }
        .background(Color.black.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
    }

    private func fraction(_ sec: Double) -> CGFloat {
        let span = windowEnd - windowStart
        guard span > 0 else { return 0 }
        return CGFloat((sec - windowStart) / span)
    }

    private func seconds(atX x: CGFloat, width: CGFloat) -> Double {
        guard width > 0 else { return windowStart }
        let f = min(max(0, x / width), 1)
        return windowStart + Double(f) * (windowEnd - windowStart)
    }

    private func waveform(size: CGSize) -> some View {
        Canvas { context, canvasSize in
            guard !peaks.isEmpty else { return }
            let midY = canvasSize.height / 2
            let barWidth = canvasSize.width / CGFloat(peaks.count)
            for (i, peak) in peaks.enumerated() {
                let h = max(1, CGFloat(peak) * (canvasSize.height - 8))
                let rect = CGRect(
                    x: CGFloat(i) * barWidth,
                    y: midY - h / 2,
                    width: max(0.5, barWidth - 0.5),
                    height: h
                )
                context.fill(
                    Path(rect), with: .color(.white.opacity(0.35))
                )
            }
        }
    }

    private func selectionOverlay(size: CGSize) -> some View {
        let startX = fraction(startSec) * size.width
        let endX = fraction(endSec) * size.width
        return ZStack {
            // Selected region
            Rectangle()
                .fill(JamTheme.accent.opacity(0.18))
                .frame(width: max(0, endX - startX))
                .position(x: (startX + endX) / 2, y: size.height / 2)

            // Original-boundary reference ticks
            ForEach([originalStart, originalEnd], id: \.self) { sec in
                Rectangle()
                    .fill(Color.white.opacity(0.25))
                    .frame(width: 1)
                    .position(
                        x: fraction(sec) * size.width, y: size.height / 2
                    )
            }

            // Handles
            handle(atX: startX, height: size.height)
            handle(atX: endX, height: size.height)
        }
    }

    private func handle(atX x: CGFloat, height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: 2)
            .fill(JamTheme.accent)
            .frame(width: 4, height: height)
            .position(x: x, y: height / 2)
    }
}
