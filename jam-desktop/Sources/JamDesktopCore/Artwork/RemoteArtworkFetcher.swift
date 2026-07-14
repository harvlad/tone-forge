// RemoteArtworkFetcher.swift
//
// Best-effort album-art lookup via iTunes Search API.
// Port of mobile RemoteArtworkFetcher.

import Foundation

public enum RemoteArtworkFetcher {

    @MainActor private static var misses: Set<String> = []

    @MainActor
    public static func fetchArtworkData(artist: String?, title: String) async -> Data? {
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
