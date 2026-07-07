// NowPlayingHeader.swift
//
// Compact now-playing strip for the Play tab. Shows:
//   - Album art placeholder (system icon for now; real art in Phase 5)
//   - Title (single-line, truncating)
//   - Artist + duration + key + BPM on the second line
//
// Purely presentational — every field is a plain value, so this view
// is safe to reuse in the mini-player once we ship it.

import SwiftUI

struct NowPlayingHeader: View {
    let title: String
    let artist: String?
    let durationSec: Double?
    let keyLabel: String?
    let tempoBpm: Double?
    /// When non-nil, shows an eject button on the trailing edge —
    /// unloads the song and returns Play to the sketch surface (D-016).
    var onEject: (() -> Void)? = nil

    var body: some View {
        HStack(spacing: 12) {
            RoundedRectangle(cornerRadius: 6)
                .fill(Color.gray.opacity(0.25))
                .frame(width: 44, height: 44)
                .overlay(
                    Image(systemName: "music.note")
                        .foregroundStyle(.secondary)
                )

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.headline)
                    .lineLimit(1)
                HStack(spacing: 8) {
                    if let artist = artist, !artist.isEmpty {
                        Text(artist).lineLimit(1)
                    }
                    if let dur = durationSec, dur > 0 {
                        Text(formatDuration(dur))
                    }
                    if let key = keyLabel, !key.isEmpty {
                        Text(key)
                    }
                    if let bpm = tempoBpm, bpm > 0 {
                        Text(String(format: "%.0f BPM", bpm))
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }

            Spacer()

            if let onEject {
                Button(action: onEject) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                }
                .accessibilityLabel("Eject song")
            }
        }
        .padding(.horizontal, 12)
    }

    private func formatDuration(_ s: Double) -> String {
        let total = max(0, Int(s.rounded(.down)))
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}
