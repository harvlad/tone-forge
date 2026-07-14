// NowPlayingHeaderView.swift
//
// Desktop now-playing card: gradient art tile (no ArtworkStore on
// desktop yet), title, metadata line and the D-024 attribution
// credit for licensed (curated CC) tracks. Layout ported from the
// mobile NowPlayingHeader.

import SwiftUI
import ToneForgeEngine

struct NowPlayingHeaderView: View {
    let meta: BundleMeta

    var body: some View {
        HStack(spacing: 10) {
            artTile

            VStack(alignment: .leading, spacing: 2) {
                Text(meta.title)
                    .font(.headline)
                    .lineLimit(1)

                Text(metaLine)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)

                if let credit = creditLine {
                    Group {
                        if let url = creditURL {
                            Link(credit, destination: url)
                        } else {
                            Text(credit)
                        }
                    }
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                }
            }

            Spacer()
        }
        .padding(10)
        .jamCard()
    }

    /// "Artist · 3:42 · Key: Dm · 120 BPM" — whichever parts exist.
    private var metaLine: String {
        var parts: [String] = []
        if !meta.artist.isEmpty { parts.append(meta.artist) }
        if meta.durationSec > 0 { parts.append(formatDuration(meta.durationSec)) }
        if let key = meta.detectedKey, !key.isEmpty { parts.append("Key: \(key)") }
        if let bpm = meta.tempoBpm, bpm > 0 {
            parts.append(String(format: "%.0f BPM", bpm))
        }
        return parts.joined(separator: " · ")
    }

    /// D-024: attribution + license, only for licensed tracks.
    private var creditLine: String? {
        var parts: [String] = []
        if let attribution = meta.attribution, !attribution.isEmpty {
            parts.append(attribution)
        }
        if let license = meta.license, !license.isEmpty {
            parts.append(license)
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private var creditURL: URL? {
        if let raw = meta.licenseUrl, let url = URL(string: raw) { return url }
        return URL(string: meta.sourceUrl)
    }

    private func formatDuration(_ s: Double) -> String {
        let total = max(0, Int(s.rounded(.down)))
        return String(format: "%d:%02d", total / 60, total % 60)
    }

    /// Stable per-song hue, same hash the mobile fallback uses.
    private var artTile: some View {
        let seedString = meta.title
        var seed = 0
        for scalar in seedString.unicodeScalars {
            seed = (seed &* 31) &+ Int(scalar.value)
        }
        let hue = Double(abs(seed) % 360) / 360.0
        return ZStack {
            LinearGradient(
                colors: [
                    Color(hue: hue, saturation: 0.55, brightness: 0.45),
                    Color(hue: hue, saturation: 0.65, brightness: 0.20),
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            Image(systemName: "music.note")
                .foregroundStyle(.white.opacity(0.55))
        }
        .frame(width: 44, height: 44)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
