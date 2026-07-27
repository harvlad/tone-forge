// DerivedAudioClient.swift
//
// Fetches a session's DERIVED music — transcribed note events per stem
// and chord-timeline voicings — from /api/debug/derived-audio/{id}.
// This is the data behind Studio's "Derived Audio" section: audible
// evaluation of what the analysis pipeline actually stored, with no
// original audio involved. Admin-guarded endpoint; AdminCredentials
// attaches the token when configured.

import Foundation

public struct DerivedNote: Decodable, Sendable {
    public let pitch: Int
    public let onset: Double
    public let offset: Double
    public let velocity: Int
}

public struct DerivedChord: Decodable, Sendable {
    public let symbol: String
    public let start: Double
    public let end: Double
    public let midiNotes: [Int]

    enum CodingKeys: String, CodingKey {
        case symbol, start, end
        case midiNotes = "midi_notes"
    }
}

public struct DerivedAudio: Decodable, Sendable {
    public let historyId: String
    public let analysisEngine: String
    public let role: String?
    public let availableRoles: [String]
    public let method: String?
    public let tempoBpm: Double?
    public let notes: [DerivedNote]
    public let chords: [DerivedChord]

    enum CodingKeys: String, CodingKey {
        case role, method, notes, chords
        case historyId = "history_id"
        case analysisEngine = "analysis_engine"
        case availableRoles = "available_roles"
        case tempoBpm = "tempo_bpm"
    }
}

public struct DerivedAudioClient: Sendable {
    public init() {}

    public func fetch(
        baseURL: URL, historyId: String, role: String? = nil
    ) async throws -> DerivedAudio {
        var comps = URLComponents(
            url: baseURL.appendingPathComponent("api/debug/derived-audio/\(historyId)"),
            resolvingAgainstBaseURL: false)!
        if let role, !role.isEmpty {
            comps.queryItems = [URLQueryItem(name: "role", value: role)]
        }
        var request = URLRequest(url: comps.url!)
        AdminCredentials.apply(to: &request)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(DerivedAudio.self, from: data)
    }
}
