// WaveformScrubber.swift
//
// Playhead-over-track scrubber. With peak data (extracted on-device
// by WaveformPeakExtractor) it draws a mirrored bar strip with the
// played portion tinted; while peaks are nil (extraction pending, or
// a song with no readable stems) it falls back to the plain progress
// bar.
//
// Emits `onSeek(newSongSeconds)` on tap / drag-end so AppState can
// call `seek(to:)` on the transport.

import SwiftUI

struct WaveformScrubber: View {
    let songSeconds: Double
    let durationSec: Double
    /// Normalized 0..1 peak bins (AppState.waveformPeaks). Nil/empty
    /// → plain progress bar.
    var peaks: [Float]? = nil
    let onSeek: (Double) -> Void

    var body: some View {
        GeometryReader { proxy in
            let w = proxy.size.width
            let progress = durationSec > 0
                ? max(0, min(1, songSeconds / durationSec))
                : 0

            ZStack(alignment: .leading) {
                if let peaks, !peaks.isEmpty {
                    waveform(peaks: peaks, progress: progress)
                } else {
                    RoundedRectangle(cornerRadius: 4)
                        .fill(Color.gray.opacity(0.15))
                    RoundedRectangle(cornerRadius: 4)
                        .fill(Color.accentColor.opacity(0.5))
                        .frame(width: w * progress)
                }

                // Time labels sit on top so the coloured fill provides
                // contrast for the current-time label.
                HStack {
                    Text(fmt(songSeconds))
                    Spacer()
                    Text(fmt(durationSec))
                }
                .font(.caption.monospacedDigit())
                .foregroundStyle(TFTheme.textPrimary)
                .padding(.horizontal, 8)
            }
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onEnded { g in
                        guard durationSec > 0 else { return }
                        let t = max(0, min(1, g.location.x / w)) * durationSec
                        onSeek(t)
                    }
            )
        }
        .padding(.horizontal, 12)
    }

    // MARK: - Peak strip

    /// Mirrored bars around the vertical center. The stored strip is
    /// ~600 bins; we group-max down to ~one bar per 3 pt so bars stay
    /// crisp at any width.
    private func waveform(peaks: [Float], progress: Double) -> some View {
        Canvas { context, size in
            let barCount = max(
                10, min(peaks.count, Int(size.width / 3))
            )
            let bars = Self.downsample(peaks, to: barCount)
            let barSlot = size.width / CGFloat(bars.count)
            let barWidth = max(1, barSlot * 0.6)
            let mid = size.height / 2
            let playedX = size.width * CGFloat(progress)

            for (i, level) in bars.enumerated() {
                let x = CGFloat(i) * barSlot + (barSlot - barWidth) / 2
                let half = max(1, CGFloat(level) * (mid - 2))
                let rect = CGRect(
                    x: x, y: mid - half, width: barWidth, height: half * 2
                )
                let played = x < playedX
                context.fill(
                    Path(roundedRect: rect, cornerRadius: barWidth / 2),
                    with: .color(
                        played
                            ? Color.accentColor.opacity(0.85)
                            : Color.white.opacity(0.22)
                    )
                )
            }
        }
    }

    /// Group-max reduction so narrow widths don't alias bars away.
    static func downsample(_ peaks: [Float], to count: Int) -> [Float] {
        guard count > 0 else { return [] }
        guard peaks.count > count else { return peaks }
        var out = [Float](repeating: 0, count: count)
        for (i, v) in peaks.enumerated() {
            let bucket = min(count - 1, i * count / peaks.count)
            if v > out[bucket] { out[bucket] = v }
        }
        return out
    }

    private func fmt(_ s: Double) -> String {
        let total = max(0, Int(s.rounded(.down)))
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}
