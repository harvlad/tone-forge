// SessionSidecar.swift
//
// The /api/session/{id} payload carries legacy sidecar fields next to
// the SongBundle contract: the tone recommendation (`legacy_tone`,
// guitar_catalog.to_wire_dict shape), decoded per-stem MIDI notes
// (`legacy_midi_stems`, for the lead tab lane) and attribution
// metadata (`legacy_attribution`, D-024 credit line). ToneForgeEngine's
// BundleLoader ignores them, so this client re-reads the same route
// and decodes only the sidecars.
//
// LeadNotePicker is a pure port of jam.js `_pickLeadMidiNotes` +
// `_melodyOnly` (jam.js:3381-3480): preference-ordered stems, melody
// role filter, note-density threshold with densest-fallback.

import Foundation

// MARK: - Tone recommendation wire model

/// Mirror of `guitar_catalog.to_wire_dict` — the dict jam.js renders
/// as the tone card. Only the fields the card consumes are decoded.
public struct ToneRecommendation: Codable, Equatable, Sendable {

    public struct Apply: Codable, Equatable, Sendable {
        public var chainId: String?
        enum CodingKeys: String, CodingKey { case chainId = "chain_id" }
        public init(chainId: String? = nil) { self.chainId = chainId }
    }

    public struct Match: Codable, Equatable, Sendable {
        public var chainId: String?
        public var displayName: String?
        public var distance: Double?
        public var confidence: Double?

        enum CodingKeys: String, CodingKey {
            case chainId = "chain_id"
            case displayName = "display_name"
            case distance, confidence
        }

        public init(
            chainId: String? = nil, displayName: String? = nil,
            distance: Double? = nil, confidence: Double? = nil
        ) {
            self.chainId = chainId
            self.displayName = displayName
            self.distance = distance
            self.confidence = confidence
        }
    }

    public struct Alternate: Codable, Equatable, Sendable {
        public var chainId: String?
        public var displayName: String?
        public var distance: Double?

        enum CodingKeys: String, CodingKey {
            case chainId = "chain_id"
            case displayName = "display_name"
            case distance
        }

        public init(
            chainId: String? = nil, displayName: String? = nil,
            distance: Double? = nil
        ) {
            self.chainId = chainId
            self.displayName = displayName
            self.distance = distance
        }
    }

    public var tier: String?
    public var rationale: String?
    public var apply: Apply?
    public var match: Match?
    public var alternates: [Alternate]?

    public init(
        tier: String? = nil, rationale: String? = nil,
        apply: Apply? = nil, match: Match? = nil,
        alternates: [Alternate]? = nil
    ) {
        self.tier = tier
        self.rationale = rationale
        self.apply = apply
        self.match = match
        self.alternates = alternates
    }

    /// Human title for the card, mirroring jam.js renderToneCard:
    /// match name first (friendlier), then the apply chain, else nil.
    public var cardTitle: String? {
        if let name = match?.displayName, !name.isEmpty { return name }
        if let id = match?.chainId { return Self.displayName(forChainId: id) }
        if let id = apply?.chainId { return Self.displayName(forChainId: id) }
        return nil
    }

    /// Port of jam.js toneChainDisplayName: strip the `tfc.` prefix
    /// and title-case the slug words.
    public static func displayName(forChainId chainId: String?) -> String {
        guard let chainId, !chainId.isEmpty else { return "" }
        var slug = chainId
        if slug.hasPrefix("tfc.") { slug.removeFirst(4) }
        return slug
            .split(whereSeparator: { $0 == "_" || $0 == "-" })
            .map { $0.prefix(1).uppercased() + $0.dropFirst() }
            .joined(separator: " ")
    }
}

// MARK: - Attribution (D-024)

public struct SessionAttribution: Codable, Equatable, Sendable {
    public var title: String?
    public var artist: String?
    public var license: String?
    public var licenseUrl: String?
    public var sourceUrl: String?
    public var attribution: String?

    enum CodingKeys: String, CodingKey {
        case title, artist, license, attribution
        case licenseUrl = "license_url"
        case sourceUrl = "source_url"
    }

    public init(
        title: String? = nil, artist: String? = nil, license: String? = nil,
        licenseUrl: String? = nil, sourceUrl: String? = nil,
        attribution: String? = nil
    ) {
        self.title = title
        self.artist = artist
        self.license = license
        self.licenseUrl = licenseUrl
        self.sourceUrl = sourceUrl
        self.attribution = attribution
    }
}

// MARK: - MIDI notes

/// One extracted note. `role` is 'melody' | 'harmony' when the
/// engine's melody-split tagged the stem; older bundles carry none.
public struct MidiNote: Codable, Equatable, Sendable {
    public var start: Double
    public var end: Double?
    public var pitch: Int
    public var velocity: Double?
    public var role: String?

    public init(
        start: Double, end: Double? = nil, pitch: Int,
        velocity: Double? = nil, role: String? = nil
    ) {
        self.start = start
        self.end = end
        self.pitch = pitch
        self.velocity = velocity
        self.role = role
    }
}

/// Per-stem decoded MIDI as served in `legacy_midi_stems`.
public struct MidiStem: Codable, Equatable, Sendable {
    public var notes: [MidiNote]?
    public var noteCount: Int?

    enum CodingKeys: String, CodingKey {
        case notes
        case noteCount = "note_count"
    }

    public init(notes: [MidiNote]? = nil, noteCount: Int? = nil) {
        self.notes = notes
        self.noteCount = noteCount
    }
}

// MARK: - Sidecar payload + client

public struct SessionSidecar: Codable, Equatable, Sendable {
    public var tone: ToneRecommendation?
    public var midiStems: [String: MidiStem]?
    public var attribution: SessionAttribution?

    enum CodingKeys: String, CodingKey {
        case tone = "legacy_tone"
        case midiStems = "legacy_midi_stems"
        case attribution = "legacy_attribution"
    }

    public init(
        tone: ToneRecommendation? = nil,
        midiStems: [String: MidiStem]? = nil,
        attribution: SessionAttribution? = nil
    ) {
        self.tone = tone
        self.midiStems = midiStems
        self.attribution = attribution
    }
}

/// Seam for tests.
public protocol SessionSidecarFetching: Sendable {
    func fetch(analysisId: String, backend: URL) async throws -> SessionSidecar
}

public struct SessionSidecarClient: SessionSidecarFetching {

    public init() {}

    public func fetch(analysisId: String, backend: URL) async throws -> SessionSidecar {
        let url = backend
            .appendingPathComponent("api")
            .appendingPathComponent("session")
            .appendingPathComponent(analysisId)
        let (data, response) = try await URLSession.shared.data(from: url)
        if let http = response as? HTTPURLResponse,
           !(200...299).contains(http.statusCode) {
            throw URLError(.badServerResponse)
        }
        return try Self.decode(data)
    }

    /// Static + pure so tests decode fixtures without a server.
    public static func decode(_ data: Data) throws -> SessionSidecar {
        try JSONDecoder().decode(SessionSidecar.self, from: data)
    }
}

// MARK: - Tone-card dismissal telemetry

/// jam.js dismissToneCard parity: fire-and-forget POST to
/// /api/tone/ignored so the catalog learns which recommendations get
/// waved off. Failures are swallowed — telemetry never surfaces.
public enum ToneIgnoredReporter {

    public static func post(
        chainId: String?,
        reason: String,
        analysisId: String?,
        sourceUrl: String?,
        backend: URL
    ) async {
        var request = URLRequest(
            url: backend
                .appendingPathComponent("api")
                .appendingPathComponent("tone")
                .appendingPathComponent("ignored")
        )
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: Any] = ["reason": reason]
        if let chainId { body["chain_id"] = chainId }
        if let analysisId { body["session_id"] = analysisId }
        if let sourceUrl { body["source_url"] = sourceUrl }
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        _ = try? await URLSession.shared.data(for: request)
    }
}

// MARK: - Lead note picker (jam.js `_pickLeadMidiNotes` port)

public enum LeadNotePicker {

    /// jam.js `_LEAD_PICKER_MIN_DENSITY`: minimum notes/second for a
    /// stem to win on preference order alone.
    public static let minDensity = 0.5

    /// jam.js `STEM_PREF`: highest-priority lead source first.
    public static let stemPreference = ["guitar", "other", "piano", "bass", "vocals"]

    /// Melody/accompaniment role filter (`_melodyOnly`): untagged
    /// stems pass through; tagged stems keep melody-role notes unless
    /// that would empty the lane entirely.
    public static func melodyOnly(_ notes: [MidiNote]) -> [MidiNote] {
        guard !notes.isEmpty else { return notes }
        let tagged = notes.contains { $0.role != nil }
        guard tagged else { return notes }
        let melody = notes.filter { $0.role == nil || $0.role == "melody" }
        return melody.isEmpty ? notes : melody
    }

    /// Walk stems in preference order; first candidate clearing the
    /// density floor wins, else the densest candidate. Duration clamps
    /// to a 1s floor so a missing duration can't divide-by-zero.
    public static func pick(
        stems: [String: MidiStem]?, durationSec: Double
    ) -> [MidiNote] {
        guard let stems else { return [] }
        let duration = max(1, durationSec)

        struct Candidate { let notes: [MidiNote]; let density: Double }
        var candidates: [Candidate] = []
        for key in stemPreference {
            guard let stem = stems[key],
                  let raw = stem.notes, !raw.isEmpty else { continue }
            let notes = melodyOnly(raw)
            candidates.append(
                Candidate(notes: notes, density: Double(notes.count) / duration)
            )
        }
        if let winner = candidates.first(where: { $0.density >= minDensity }) {
            return winner.notes
        }
        return candidates.max(by: { $0.density < $1.density })?.notes ?? []
    }
}
