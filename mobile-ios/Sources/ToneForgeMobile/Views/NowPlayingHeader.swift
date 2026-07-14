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

struct NowPlayingHeader<Accessory: View>: View {
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
    /// When non-nil, shows a gear on the trailing edge that opens the
    /// Settings sheet (D-022: settings live in the header, not a tab).
    var onSettings: (() -> Void)? = nil
    /// Attribution credit (D-024) — shown as a third caption line only
    /// for licensed songs (curated CC tracks). Nil keeps the card at
    /// its two-line shape, so snapshot goldens don't shift.
    var creditLine: String? = nil
    /// Optional link target for the credit line (source page).
    var creditURL: URL? = nil
    /// Optional accessory view (e.g., pack picker) displayed before
    /// the settings button.
    @ViewBuilder var accessory: () -> Accessory

    var body: some View {
        // Compact two-line card: real devices give the Play tab far
        // less height than the safe-area-less snapshot renders, so
        // Key/BPM fold into the metadata line instead of a chip row.
        HStack(spacing: 10) {
            ArtworkView(
                analysisId: analysisId, title: title, artist: artist, size: 40
            )

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)
                    .lineLimit(1)

                Text(metaLine)
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
                    .lineLimit(1)

                if let creditLine, !creditLine.isEmpty {
                    Group {
                        if let creditURL {
                            Link(creditLine, destination: creditURL)
                        } else {
                            Text(creditLine)
                        }
                    }
                    .font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
                    .lineLimit(1)
                    .accessibilityIdentifier("nowplaying.credit")
                }
            }

            Spacer()

            accessory()

            if let onEject {
                Button(action: onEject) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.title3)
                        .foregroundStyle(TFTheme.textSecondary)
                }
                .accessibilityLabel("Eject song")
            }

            if let onSettings {
                Button(action: onSettings) {
                    Image(systemName: "gearshape")
                        .font(.title3)
                        .foregroundStyle(TFTheme.textSecondary)
                }
                .accessibilityLabel("Settings")
            }
        }
        .padding(10)
        .tfCard()
        .padding(.horizontal, 12)
    }

    /// "Artist · 3:42 · Key: Dm · 120 BPM" — whichever parts exist.
    private var metaLine: String {
        var parts: [String] = []
        if let artist, !artist.isEmpty { parts.append(artist) }
        if let dur = durationSec, dur > 0 { parts.append(formatDuration(dur)) }
        if let key = keyLabel, !key.isEmpty { parts.append("Key: \(key)") }
        if let bpm = tempoBpm, bpm > 0 {
            parts.append(String(format: "%.0f BPM", bpm))
        }
        return parts.joined(separator: " · ")
    }

    private func formatDuration(_ s: Double) -> String {
        let total = max(0, Int(s.rounded(.down)))
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}

// MARK: - Convenience initializer (no accessory)

extension NowPlayingHeader where Accessory == EmptyView {
    init(
        title: String,
        artist: String?,
        durationSec: Double?,
        keyLabel: String?,
        tempoBpm: Double?,
        analysisId: String? = nil,
        onEject: (() -> Void)? = nil,
        onSettings: (() -> Void)? = nil
    ) {
        self.title = title
        self.artist = artist
        self.durationSec = durationSec
        self.keyLabel = keyLabel
        self.tempoBpm = tempoBpm
        self.analysisId = analysisId
        self.onEject = onEject
        self.onSettings = onSettings
        self.accessory = { EmptyView() }
    }
}

// MARK: - Artwork

/// Album-art tile. Loads the stored JPEG off-main once per song
/// (`.task(id:)`) and caches it in @State — PlayView's body
/// re-evaluates at the 30 Hz transport tick, so reading disk in
/// `body` is off the table. Songs with nothing stored (Files-app
/// imports, history entries) fall back to a remote iTunes Search
/// lookup, cached into the ArtworkStore on success. Until/unless art
/// arrives, a gradient hashed from the song identity gives every
/// art-less song a stable, distinctive tile.
struct ArtworkView: View {
    let analysisId: String?
    let title: String
    var artist: String? = nil
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
                return
            }
            // Nothing stored — try a remote lookup and cache the hit
            // so the next appearance is a plain disk read.
            guard
                let remote = await RemoteArtworkFetcher.fetchArtworkData(
                    artist: artist, title: title
                ),
                let ui = UIImage(data: remote)
            else { return }
            try? ArtworkStore().save(remote, analysisId: analysisId)
            loaded = Image(uiImage: ui)
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
