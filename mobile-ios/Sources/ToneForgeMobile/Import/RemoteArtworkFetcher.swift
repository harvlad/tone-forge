// RemoteArtworkFetcher.swift
//
// Best-effort album-art lookup for songs that have none stored —
// Files-app imports and backend history entries never pass through
// the MPMediaItemArtwork capture path, so they'd otherwise show the
// gradient fallback forever.
//
// Uses the iTunes Search API (public, key-less): search artist+title,
// take the first hit's artworkUrl100, and upscale the URL to 600×600
// (the CDN serves any size by filename convention). Callers cache the
// bytes in ArtworkStore so each song is looked up at most once per
// success; failures are remembered in-memory per app run so an
// artless song doesn't re-query on every appearance.

import Foundation

enum RemoteArtworkFetcher {

    /// Song identities that already failed a lookup this app run.
    @MainActor private static var misses: Set<String> = []

    /// JPEG bytes for the best match, or nil (no match / offline).
    /// Never throws — artwork is cosmetic.
    @MainActor
    static func fetchArtworkData(artist: String?, title: String) async -> Data? {
        var term = title
        if let artist, !artist.isEmpty { term = "\(artist) \(title)" }
        guard !term.isEmpty, !misses.contains(term) else { return nil }

        guard let data = await lookup(term: term) else {
            misses.insert(term)
            return nil
        }
        return data
    }

    private static func lookup(term: String) async -> Data? {
        var comps = URLComponents(string: "https://itunes.apple.com/search")
        comps?.queryItems = [
            URLQueryItem(name: "term", value: term),
            URLQueryItem(name: "media", value: "music"),
            URLQueryItem(name: "entity", value: "song"),
            URLQueryItem(name: "limit", value: "1"),
        ]
        guard let url = comps?.url else { return nil }

        struct SearchResponse: Decodable {
            struct Item: Decodable { let artworkUrl100: String? }
            let results: [Item]
        }

        do {
            let (body, _) = try await URLSession.shared.data(from: url)
            let response = try JSONDecoder().decode(SearchResponse.self, from: body)
            guard let thumb = response.results.first?.artworkUrl100 else {
                return nil
            }
            // CDN filename convention: swap the size component for a
            // higher-resolution render of the same asset.
            let hiRes = thumb.replacingOccurrences(
                of: "100x100bb", with: "600x600bb"
            )
            guard let artURL = URL(string: hiRes) else { return nil }
            let (art, _) = try await URLSession.shared.data(from: artURL)
            return art.isEmpty ? nil : art
        } catch {
            return nil
        }
    }
}
