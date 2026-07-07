// NowPlayingHeader.swift
//
// Now Playing card for the Play tab (mockup-driven restyle). Shows:
//   - Album art (captured at import via ArtworkStore; deterministic
//     gradient fallback when no art exists)
//   - Title + artist/duration line
//   - Key / BPM chips
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
    /// Keys the ArtworkStore lookup; nil (no song) shows the fallback.
    var analysisId: String? = nil
    /// When non-nil, shows an eject button on the trailing edge —
    /// unloads the song and returns Play to the sketch surface (D-016).
    var onEject: (() -> Void)? = nil

    var body: some View {
        HStack(spacing: 12) {
            ArtworkView(analysisId: analysisId, title: title, size: 56)

            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.headline)
                    .foregroundStyle(TFTheme.textPrimary)
                    .lineLimit(1)

                HStack(spacing: 8) {
                    if let artist = artist, !artist.isEmpty {
                        Text(artist).lineLimit(1)
                    }
                    if let dur = durationSec, dur > 0 {
                        Text(formatDuration(dur))
                    }
                }
                .font(.caption)
                .foregroundStyle(TFTheme.textSecondary)

                if hasChips {
                    HStack(spacing: 6) {
                        if let key = keyLabel, !key.isEmpty {
                            Text("Key: \(key)").tfChip()
                        }
                        if let bpm = tempoBpm, bpm > 0 {
                            Text(String(format: "%.0f BPM", bpm)).tfChip()
                        }
                    }
                }
            }

            Spacer()

            if let onEject {
                Button(action: onEject) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.title3)
                        .foregroundStyle(TFTheme.textSecondary)
                }
                .accessibilityLabel("Eject song")
            }
        }
        .padding(12)
        .tfCard()
        .padding(.horizontal, 12)
    }

    private var hasChips: Bool {
        (keyLabel?.isEmpty == false) || (tempoBpm ?? 0) > 0
    }

    private func formatDuration(_ s: Double) -> String {
        let total = max(0, Int(s.rounded(.down)))
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}

// MARK: - Artwork

/// Album-art tile. Loads the stored JPEG off-main once per song
/// (`.task(id:)`) and caches it in @State — PlayView's body
/// re-evaluates at the 30 Hz transport tick, so reading disk in
/// `body` is off the table. Fallback is a gradient hashed from the
/// song identity, so every art-less song still gets a stable,
/// distinctive tile.
struct ArtworkView: View {
    let analysisId: String?
    let title: String
    var size: CGFloat = 56

    @State private var loaded: Image?

    var body: some View {
        ZStack {
            fallbackGradient
            if let loaded {
                loaded
                    .resizable()
                    .scaledToFill()
            } else {
                Image(systemName: "music.note")
                    .foregroundStyle(.white.opacity(0.55))
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(TFTheme.stroke, lineWidth: 1)
        )
        .task(id: analysisId) {
            loaded = nil
            guard let analysisId else { return }
            #if canImport(UIKit)
            let data = await Task.detached(priority: .utility) {
                ArtworkStore().imageData(for: analysisId)
            }.value
            if let data, let ui = UIImage(data: data) {
                loaded = Image(uiImage: ui)
            }
            #endif
        }
    }

    /// Stable per-song hue: hash the identity, spin the hue wheel.
    private var fallbackGradient: LinearGradient {
        let seedString = analysisId ?? title
        var seed = 0
        for scalar in seedString.unicodeScalars {
            seed = (seed &* 31) &+ Int(scalar.value)
        }
        let hue = Double(abs(seed) % 360) / 360.0
        return LinearGradient(
            colors: [
                Color(hue: hue, saturation: 0.55, brightness: 0.45),
                Color(hue: hue, saturation: 0.65, brightness: 0.20),
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}
