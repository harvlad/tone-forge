// ChopWaveformView.swift
//
// Waveform display with draggable start/end handles for chop boundary
// editing (D-023 Phase 2). Renders peak data as mirrored bars with the
// selected region highlighted. Drag handles snap to a 10ms grid for
// precision without sub-sample chaos.
//
// Usage:
//   ChopWaveformView(
//       peaks: [Float],           // normalized 0-1 peak bins
//       startFraction: $start,    // 0-1 fraction of total
//       endFraction: $end,        // 0-1 fraction of total
//       onPlay: { start, end in } // preview callback
//   )

import SwiftUI

struct ChopWaveformView: View {
    /// Normalized 0-1 peak bins for the full audio range.
    let peaks: [Float]
    /// Start position as fraction 0-1 of total duration.
    @Binding var startFraction: Double
    /// End position as fraction 0-1 of total duration.
    @Binding var endFraction: Double
    /// Called when user taps the play button or waveform.
    var onPlay: ((Double, Double) -> Void)?
    /// Total duration in seconds (for time display).
    var durationSec: Double = 0

    @State private var isDraggingStart = false
    @State private var isDraggingEnd = false

    private let handleWidth: CGFloat = 12
    private let minSelectionFraction: Double = 0.01

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width
            let height = geo.size.height

            ZStack(alignment: .leading) {
                // Background waveform (dimmed)
                waveformCanvas(
                    in: CGSize(width: width, height: height),
                    selectedRange: nil
                )

                // Selected region overlay
                waveformCanvas(
                    in: CGSize(width: width, height: height),
                    selectedRange: startFraction...endFraction
                )
                .mask(selectionMask(width: width, height: height))

                // Selection box
                selectionBox(width: width, height: height)

                // Handles
                startHandle(width: width, height: height)
                endHandle(width: width, height: height)

                // Time labels
                timeLabels(width: width)
            }
            .contentShape(Rectangle())
            .onTapGesture {
                onPlay?(startFraction, endFraction)
            }
        }
        .frame(height: 80)
    }

    // MARK: - Waveform Canvas

    private func waveformCanvas(
        in size: CGSize,
        selectedRange: ClosedRange<Double>?
    ) -> some View {
        Canvas { context, canvasSize in
            guard !peaks.isEmpty else { return }

            let barCount = max(10, min(peaks.count, Int(size.width / 2)))
            let bars = downsample(peaks, to: barCount)
            let barSlot = size.width / CGFloat(bars.count)
            let barWidth = max(1, barSlot * 0.7)
            let mid = size.height / 2
            let maxBarHeight = (size.height / 2) - 4

            for (i, level) in bars.enumerated() {
                let x = CGFloat(i) * barSlot + (barSlot - barWidth) / 2
                let half = max(1, CGFloat(level) * maxBarHeight)
                let rect = CGRect(
                    x: x, y: mid - half, width: barWidth, height: half * 2
                )

                let fraction = Double(i) / Double(bars.count)
                let isSelected = selectedRange?.contains(fraction) ?? false

                context.fill(
                    Path(roundedRect: rect, cornerRadius: barWidth / 3),
                    with: .color(
                        isSelected
                            ? Color.accentColor.opacity(0.9)
                            : Color.white.opacity(0.2)
                    )
                )
            }
        }
    }

    private func selectionMask(width: CGFloat, height: CGFloat) -> some View {
        let startX = width * CGFloat(startFraction)
        let endX = width * CGFloat(endFraction)
        return Rectangle()
            .frame(width: max(0, endX - startX))
            .offset(x: startX)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Selection Box

    private func selectionBox(width: CGFloat, height: CGFloat) -> some View {
        let startX = width * CGFloat(startFraction)
        let endX = width * CGFloat(endFraction)
        let boxWidth = max(0, endX - startX)

        return RoundedRectangle(cornerRadius: 4)
            .stroke(Color.accentColor, lineWidth: 2)
            .frame(width: boxWidth, height: height - 4)
            .offset(x: startX)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Handles

    private func startHandle(width: CGFloat, height: CGFloat) -> some View {
        let x = width * CGFloat(startFraction)
        return handle(color: .accentColor, isDragging: isDraggingStart)
            .position(x: x, y: height / 2)
            .gesture(
                DragGesture()
                    .onChanged { value in
                        isDraggingStart = true
                        let newFraction = max(0, min(
                            endFraction - minSelectionFraction,
                            Double(value.location.x / width)
                        ))
                        startFraction = snap(newFraction)
                    }
                    .onEnded { _ in
                        isDraggingStart = false
                    }
            )
    }

    private func endHandle(width: CGFloat, height: CGFloat) -> some View {
        let x = width * CGFloat(endFraction)
        return handle(color: .accentColor, isDragging: isDraggingEnd)
            .position(x: x, y: height / 2)
            .gesture(
                DragGesture()
                    .onChanged { value in
                        isDraggingEnd = true
                        let newFraction = max(
                            startFraction + minSelectionFraction,
                            min(1, Double(value.location.x / width))
                        )
                        endFraction = snap(newFraction)
                    }
                    .onEnded { _ in
                        isDraggingEnd = false
                    }
            )
    }

    private func handle(color: Color, isDragging: Bool) -> some View {
        ZStack {
            // Handle bar
            RoundedRectangle(cornerRadius: 3)
                .fill(color)
                .frame(width: 4, height: 60)

            // Grab area (larger hit target)
            Rectangle()
                .fill(Color.clear)
                .frame(width: handleWidth * 3, height: 80)
        }
        .contentShape(Rectangle())
        .scaleEffect(isDragging ? 1.1 : 1.0)
        .animation(.easeOut(duration: 0.1), value: isDragging)
    }

    // MARK: - Time Labels

    private func timeLabels(width: CGFloat) -> some View {
        VStack {
            Spacer()
            HStack {
                Text(formatTime(startFraction * durationSec))
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.white.opacity(0.7))
                    .padding(.leading, 4)

                Spacer()

                Text(formatTime((endFraction - startFraction) * durationSec))
                    .font(.caption2.monospacedDigit().weight(.semibold))
                    .foregroundStyle(Color.accentColor)

                Spacer()

                Text(formatTime(endFraction * durationSec))
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.white.opacity(0.7))
                    .padding(.trailing, 4)
            }
        }
    }

    // MARK: - Helpers

    /// Snap to ~10ms grid (assuming standard audio).
    private func snap(_ fraction: Double) -> Double {
        guard durationSec > 0 else { return fraction }
        let gridSec = 0.01 // 10ms
        let gridFraction = gridSec / durationSec
        return (fraction / gridFraction).rounded() * gridFraction
    }

    private func downsample(_ peaks: [Float], to count: Int) -> [Float] {
        guard count > 0 else { return [] }
        guard peaks.count > count else { return peaks }
        var out = [Float](repeating: 0, count: count)
        for (i, v) in peaks.enumerated() {
            let bucket = min(count - 1, i * count / peaks.count)
            if v > out[bucket] { out[bucket] = v }
        }
        return out
    }

    private func formatTime(_ seconds: Double) -> String {
        let total = max(0, seconds)
        let mins = Int(total) / 60
        let secs = Int(total) % 60
        let ms = Int((total.truncatingRemainder(dividingBy: 1)) * 100)
        if mins > 0 {
            return String(format: "%d:%02d.%02d", mins, secs, ms)
        }
        return String(format: "%d.%02d", secs, ms)
    }
}

// MARK: - Preview

#if DEBUG
struct ChopWaveformView_Previews: PreviewProvider {
    struct Wrapper: View {
        @State var start: Double = 0.2
        @State var end: Double = 0.7

        // Generate some fake peaks
        let peaks: [Float] = (0..<200).map { i in
            let t = Float(i) / 200
            return abs(sin(t * .pi * 8)) * (0.3 + 0.7 * sin(t * .pi * 2))
        }

        var body: some View {
            VStack {
                ChopWaveformView(
                    peaks: peaks,
                    startFraction: $start,
                    endFraction: $end,
                    onPlay: { s, e in print("Play \(s) - \(e)") },
                    durationSec: 4.5
                )
                .padding()
                .background(Color.black)

                Text("Start: \(start, specifier: "%.3f"), End: \(end, specifier: "%.3f")")
            }
        }
    }

    static var previews: some View {
        Wrapper()
            .preferredColorScheme(.dark)
    }
}
#endif
