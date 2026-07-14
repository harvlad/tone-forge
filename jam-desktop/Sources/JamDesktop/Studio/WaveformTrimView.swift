// WaveformTrimView.swift
//
// Studio Phase 3: server-rendered waveform preview (peaks + rms from
// /api/preview-waveform) with a draggable trim selection. Mirrors
// studio.html's WaveformTrimmer: left/right handles, range move,
// dimmed out-of-selection areas, min width 0.5s.

import SwiftUI
import JamDesktopCore

struct WaveformTrimView: View {
    let waveform: WaveformPreview
    @Binding var trim: TrimSelection

    /// Which part of the selection the current drag grabbed.
    private enum DragMode {
        case start, end, move(lastFraction: Double)
    }

    @State private var dragMode: DragMode?

    private let handleHitWidth: CGFloat = 10

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            GeometryReader { geo in
                ZStack {
                    waveformCanvas
                    selectionOverlay(width: geo.size.width)
                }
                .contentShape(Rectangle())
                .gesture(dragGesture(width: geo.size.width))
            }
            .frame(height: 96)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color.black.opacity(0.35)))
            .clipShape(RoundedRectangle(cornerRadius: 6))
            timeLabels
        }
    }

    // MARK: - waveform drawing

    private var waveformCanvas: some View {
        Canvas { context, size in
            let positive = waveform.peaksPositive
            let negative = waveform.peaksNegative
            let rms = waveform.rms
            let count = max(positive.count, 1)
            let midY = size.height / 2
            let amp = size.height / 2 - 2

            // Peak envelope: one vertical line per point.
            var peaks = Path()
            for i in 0..<count {
                let x = size.width * CGFloat(i) / CGFloat(count - 1 > 0 ? count - 1 : 1)
                let top = midY - amp * CGFloat(min(1, abs(positive[i])))
                let bottom = midY + amp * CGFloat(
                    min(1, abs(i < negative.count ? negative[i] : 0)))
                peaks.move(to: CGPoint(x: x, y: top))
                peaks.addLine(to: CGPoint(x: x, y: bottom))
            }
            context.stroke(
                peaks, with: .color(JamTheme.accent.opacity(0.45)),
                lineWidth: 1)

            // RMS fill: mirrored filled band around the midline.
            guard !rms.isEmpty else { return }
            var band = Path()
            band.move(to: CGPoint(x: 0, y: midY))
            for i in 0..<rms.count {
                let x = size.width * CGFloat(i) / CGFloat(
                    rms.count - 1 > 0 ? rms.count - 1 : 1)
                band.addLine(to: CGPoint(
                    x: x, y: midY - amp * CGFloat(min(1, abs(rms[i])))))
            }
            for i in stride(from: rms.count - 1, through: 0, by: -1) {
                let x = size.width * CGFloat(i) / CGFloat(
                    rms.count - 1 > 0 ? rms.count - 1 : 1)
                band.addLine(to: CGPoint(
                    x: x, y: midY + amp * CGFloat(min(1, abs(rms[i])))))
            }
            band.closeSubpath()
            context.fill(band, with: .color(JamTheme.accent.opacity(0.55)))
        }
    }

    // MARK: - selection overlay

    private func selectionOverlay(width: CGFloat) -> some View {
        let startX = width * CGFloat(trim.startFraction)
        let endX = width * CGFloat(trim.endFraction)
        return ZStack(alignment: .topLeading) {
            // Dim outside the selection.
            Rectangle()
                .fill(Color.black.opacity(0.55))
                .frame(width: max(0, startX))
                .frame(maxHeight: .infinity)
            Rectangle()
                .fill(Color.black.opacity(0.55))
                .frame(width: max(0, width - endX))
                .frame(maxHeight: .infinity)
                .offset(x: endX)
            // Selection border.
            Rectangle()
                .strokeBorder(Color.white.opacity(0.6), lineWidth: 1)
                .frame(width: max(0, endX - startX))
                .frame(maxHeight: .infinity)
                .offset(x: startX)
            // Handles.
            handle.offset(x: startX - 2)
            handle.offset(x: endX - 2)
        }
        .allowsHitTesting(false)
    }

    private var handle: some View {
        RoundedRectangle(cornerRadius: 2)
            .fill(Color.white.opacity(0.9))
            .frame(width: 4)
            .frame(maxHeight: .infinity)
    }

    // MARK: - drag

    private func dragGesture(width: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                guard width > 0 else { return }
                let fraction = Double(value.location.x / width)
                if dragMode == nil {
                    dragMode = pickMode(
                        atX: value.startLocation.x, width: width)
                }
                switch dragMode {
                case .start:
                    trim.dragStart(to: fraction)
                case .end:
                    trim.dragEnd(to: fraction)
                case let .move(lastFraction):
                    trim.move(by: fraction - lastFraction)
                    dragMode = .move(lastFraction: fraction)
                case nil:
                    break
                }
            }
            .onEnded { _ in dragMode = nil }
    }

    private func pickMode(atX x: CGFloat, width: CGFloat) -> DragMode {
        let startX = width * CGFloat(trim.startFraction)
        let endX = width * CGFloat(trim.endFraction)
        // Handle grabs win; ties go to the nearer handle.
        if abs(x - startX) <= handleHitWidth,
           abs(x - startX) <= abs(x - endX) {
            return .start
        }
        if abs(x - endX) <= handleHitWidth {
            return .end
        }
        if x > startX, x < endX {
            return .move(lastFraction: Double(x / width))
        }
        // Outside: jump the nearer handle to the press point.
        return abs(x - startX) <= abs(x - endX) ? .start : .end
    }

    // MARK: - labels

    private var timeLabels: some View {
        HStack {
            Text(timecode(trim.startSeconds))
            Spacer()
            Text(trim.isFullRange
                ? "Full file · \(timecode(trim.duration))"
                : "Selected \(timecode(trim.selectedSeconds))")
                .foregroundStyle(.primary)
            Spacer()
            Text(timecode(trim.endSeconds))
        }
        .font(.caption.monospacedDigit())
        .foregroundStyle(JamTheme.textSecondary)
    }

    private func timecode(_ seconds: Double) -> String {
        let s = max(0, seconds)
        return String(format: "%d:%04.1f", Int(s) / 60,
                      s.truncatingRemainder(dividingBy: 60))
    }
}
