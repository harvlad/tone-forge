// WaveformScrubber.swift
//
// Simple playhead-over-track scrubber. Phase 1 draws a plain progress
// bar with time labels — no peak strip yet. When we wire the real
// stem peak data (already emitted by the backend) this view swaps
// its background rectangle for a peak-strip drawing.
//
// Emits `onSeek(newSongSeconds)` on tap / drag-end so AppState can
// call `seek(to:)` on the transport.

import SwiftUI

struct WaveformScrubber: View {
    let songSeconds: Double
    let durationSec: Double
    let onSeek: (Double) -> Void

    var body: some View {
        GeometryReader { proxy in
            let w = proxy.size.width
            let progress = durationSec > 0
                ? max(0, min(1, songSeconds / durationSec))
                : 0

            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.gray.opacity(0.15))
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.accentColor.opacity(0.5))
                    .frame(width: w * progress)

                // Time labels sit on top so the coloured fill provides
                // contrast for the current-time label.
                HStack {
                    Text(fmt(songSeconds))
                    Spacer()
                    Text(fmt(durationSec))
                }
                .font(.caption.monospacedDigit())
                .foregroundStyle(.primary)
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

    private func fmt(_ s: Double) -> String {
        let total = max(0, Int(s.rounded(.down)))
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}
