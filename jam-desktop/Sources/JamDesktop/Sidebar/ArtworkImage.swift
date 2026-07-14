// ArtworkImage.swift
//
// Album art view: loads from ArtworkStore cache, falls back to
// RemoteArtworkFetcher (iTunes API), shows gradient placeholder
// while loading or when no art found.

import SwiftUI
import JamDesktopCore

struct ArtworkImage: View {
    let analysisId: String
    let artist: String?
    let title: String?
    let size: CGFloat

    @State private var image: NSImage?
    @State private var loading = true

    private static let store = ArtworkStore()

    var body: some View {
        Group {
            if let image {
                Image(nsImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } else {
                gradientPlaceholder
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: 4))
        .task(id: analysisId) {
            await loadArtwork()
        }
    }

    private var gradientPlaceholder: some View {
        RoundedRectangle(cornerRadius: 4)
            .fill(
                LinearGradient(
                    colors: [Color(white: 0.2), Color(white: 0.15)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
            .overlay {
                if loading {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Image(systemName: "music.note")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.4))
                }
            }
    }

    private func loadArtwork() async {
        loading = true

        // Check cache first
        if let data = Self.store.imageData(for: analysisId),
           let nsImage = NSImage(data: data) {
            image = nsImage
            loading = false
            return
        }

        // Try remote fetch
        guard let title, !title.isEmpty else {
            loading = false
            return
        }

        if let data = await RemoteArtworkFetcher.fetchArtworkData(
            artist: artist,
            title: title
        ) {
            _ = try? Self.store.save(data, analysisId: analysisId)
            if let nsImage = NSImage(data: data) {
                image = nsImage
            }
        }
        loading = false
    }
}
