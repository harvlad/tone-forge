// SourceTrack.swift
//
// The unified "Songs" page contract — Swift twin of the backend
// `tone_forge/contracts.py` SourceTrack / SearchPage / FacetBucket /
// SourceId / TrackStatus DTOs (GET /api/library/search). ONE row shape
// every source returns, so the Vinyl Crate + external CC catalogs can
// drop in behind the same table shell later without touching this view
// layer.
//
// Wire shape is snake_case (Python backend convention); every provenance
// and lifecycle field is optional so a lean LibrarySource row and a
// rich CrateSource row both decode against the same struct. Unknown enum
// values decode to `.unknown` rather than throwing — a newer backend
// adding a source/status must never break an older client's decode.

import Foundation

/// Which pluggable catalog a row came from. MVP implements only
/// `.library`; the rest are declared so the table's source tabs and the
/// decode are forward-compatible when those sources land.
public enum SourceId: String, Codable, Sendable, CaseIterable, Equatable {
    case library
    case crate
    case jamendo
    case ccmixter
    case device
    /// Forward-compat: a source this build doesn't know yet.
    case unknown

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = SourceId(rawValue: raw) ?? .unknown
    }
}

/// Lifecycle of a track as the union (history ∪ jobs) reports it. A
/// completing job COLLAPSES into its history row: the same row flips
/// `queued → running → done` and gains a `history_id`.
public enum TrackStatus: String, Codable, Sendable, Equatable {
    case queued
    case running
    case done
    case error
    /// Forward-compat sentinel for an unrecognized server status.
    case unknown

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = TrackStatus(rawValue: raw) ?? .unknown
    }

    /// Still working — the "Processing (N)" filter counts these.
    public var isActive: Bool { self == .queued || self == .running }
}

/// One row of the Songs table. Every field beyond `source`/`source_ref`/
/// `status` is optional so a bare in-flight job row (no metadata yet) and
/// a fully-analyzed track share the type.
public struct SourceTrack: Codable, Sendable, Identifiable, Equatable {
    public let source: SourceId
    /// Opaque handle WITHIN the source (a history id for library, a
    /// catalog id for a CC source). Passed back to `POST /api/library/
    /// ingest` to bring the track into the user's library.
    public let sourceRef: String

    public var title: String?
    public var artist: String?
    public var key: String?
    public var tempoBpm: Double?
    public var durationS: Double?
    public var genre: String?
    public var mood: String?
    public var tags: [String]

    // Provenance (CC sources fill these; library uploads leave them nil).
    public var license: String?
    public var licenseUrl: String?
    public var attribution: String?
    public var sourceUrl: String?

    // Lifecycle.
    public var status: TrackStatus
    /// [0, 1] fraction while running. The server may send a 0–100 percent
    /// instead; ``progressFraction`` normalizes either convention.
    public var progress: Double?
    /// Present once the analysis has landed — the id `/api/history/{id}`
    /// (deep open) and stem load use. nil while a job is still cooking.
    public var historyId: String?
    public var artworkRef: String?

    enum CodingKeys: String, CodingKey {
        case source
        case sourceRef = "source_ref"
        case title
        case artist
        case key
        case tempoBpm = "tempo_bpm"
        case durationS = "duration_s"
        case genre
        case mood
        case tags
        case license
        case licenseUrl = "license_url"
        case attribution
        case sourceUrl = "source_url"
        case status
        case progress
        case historyId = "history_id"
        case artworkRef = "artwork_ref"
    }

    public init(
        source: SourceId,
        sourceRef: String,
        title: String? = nil,
        artist: String? = nil,
        key: String? = nil,
        tempoBpm: Double? = nil,
        durationS: Double? = nil,
        genre: String? = nil,
        mood: String? = nil,
        tags: [String] = [],
        license: String? = nil,
        licenseUrl: String? = nil,
        attribution: String? = nil,
        sourceUrl: String? = nil,
        status: TrackStatus = .done,
        progress: Double? = nil,
        historyId: String? = nil,
        artworkRef: String? = nil
    ) {
        self.source = source
        self.sourceRef = sourceRef
        self.title = title
        self.artist = artist
        self.key = key
        self.tempoBpm = tempoBpm
        self.durationS = durationS
        self.genre = genre
        self.mood = mood
        self.tags = tags
        self.license = license
        self.licenseUrl = licenseUrl
        self.attribution = attribution
        self.sourceUrl = sourceUrl
        self.status = status
        self.progress = progress
        self.historyId = historyId
        self.artworkRef = artworkRef
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = try c.decodeIfPresent(SourceId.self, forKey: .source) ?? .unknown
        sourceRef = try c.decodeIfPresent(String.self, forKey: .sourceRef) ?? ""
        title = try c.decodeIfPresent(String.self, forKey: .title)
        artist = try c.decodeIfPresent(String.self, forKey: .artist)
        key = try c.decodeIfPresent(String.self, forKey: .key)
        tempoBpm = try c.decodeIfPresent(Double.self, forKey: .tempoBpm)
        durationS = try c.decodeIfPresent(Double.self, forKey: .durationS)
        genre = try c.decodeIfPresent(String.self, forKey: .genre)
        mood = try c.decodeIfPresent(String.self, forKey: .mood)
        tags = try c.decodeIfPresent([String].self, forKey: .tags) ?? []
        license = try c.decodeIfPresent(String.self, forKey: .license)
        licenseUrl = try c.decodeIfPresent(String.self, forKey: .licenseUrl)
        attribution = try c.decodeIfPresent(String.self, forKey: .attribution)
        sourceUrl = try c.decodeIfPresent(String.self, forKey: .sourceUrl)
        status = try c.decodeIfPresent(TrackStatus.self, forKey: .status) ?? .done
        progress = try c.decodeIfPresent(Double.self, forKey: .progress)
        historyId = try c.decodeIfPresent(String.self, forKey: .historyId)
        artworkRef = try c.decodeIfPresent(String.self, forKey: .artworkRef)
    }

    /// Stable SwiftUI identity. A row keeps its identity across the
    /// queued→done flip because the merge keys on ``mergeKey`` (history
    /// id when present, else the source ref), not on status.
    public var id: String { "\(source.rawValue):\(mergeKey)" }

    /// The identity used to collapse a live job row into its finished
    /// history row: prefer the history id (both sides converge on it),
    /// else the source ref.
    public var mergeKey: String { historyId ?? sourceRef }

    /// Progress as a [0, 1] fraction regardless of whether the server
    /// sent a fraction or a 0–100 percent (the union projects a job's
    /// integer percent, but a CC source might send a fraction).
    public var progressFraction: Double? {
        guard let p = progress else { return nil }
        if p > 1.0 { return min(1.0, p / 100.0) }
        return max(0.0, p)
    }
}

/// One selectable value in a facet column, with how many rows carry it.
/// The rail renders these as toggles; `count` lets the UI grey out empty
/// buckets and show tallies.
public struct FacetBucket: Codable, Sendable, Identifiable, Equatable {
    public let value: String
    public let count: Int
    /// Optional pretty label (e.g. "A minor" for value "am"). Falls back
    /// to `value` when absent.
    public let label: String?

    public var id: String { value }

    enum CodingKeys: String, CodingKey {
        case value, count, label
    }

    public init(value: String, count: Int, label: String? = nil) {
        self.value = value
        self.count = count
        self.label = label
    }

    public var displayLabel: String { label ?? value }
}

/// One page of search results. `facets` is keyed by field name
/// ("genre", "key", "mood", "tags", "status"); `next_cursor` is OPAQUE —
/// a background ingest inserting a row must not shift the window, so the
/// client never computes offsets, it just echoes the cursor back.
public struct SearchPage: Codable, Sendable, Equatable {
    public let source: SourceId?
    public let tracks: [SourceTrack]
    public let facets: [String: [FacetBucket]]
    public let nextCursor: String?
    public let total: Int?

    enum CodingKeys: String, CodingKey {
        case source
        case tracks
        case facets
        case nextCursor = "next_cursor"
        case total
    }

    public init(
        source: SourceId? = nil,
        tracks: [SourceTrack],
        facets: [String: [FacetBucket]] = [:],
        nextCursor: String? = nil,
        total: Int? = nil
    ) {
        self.source = source
        self.tracks = tracks
        self.facets = facets
        self.nextCursor = nextCursor
        self.total = total
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = try c.decodeIfPresent(SourceId.self, forKey: .source)
        tracks = try c.decodeIfPresent([SourceTrack].self, forKey: .tracks) ?? []
        facets = try c.decodeIfPresent([String: [FacetBucket]].self, forKey: .facets) ?? [:]
        nextCursor = try c.decodeIfPresent(String.self, forKey: .nextCursor)
        total = try c.decodeIfPresent(Int.self, forKey: .total)
    }
}

/// Result of `POST /api/library/ingest`. Exactly one of the two is set:
/// a `history_id` when the track already existed (dedupe hit — the MVP
/// library case, a no-op reuse), a `job_id` when a fresh analysis was
/// kicked off (the CC-source case, later).
public struct IngestResult: Codable, Sendable, Equatable {
    public let jobId: String?
    public let historyId: String?

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id"
        case historyId = "history_id"
    }

    public init(jobId: String? = nil, historyId: String? = nil) {
        self.jobId = jobId
        self.historyId = historyId
    }
}
