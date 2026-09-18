// CrateModels.swift
//
// Wire DTOs + the pure facet-query builder for the "Vinyl Crate" — a shared,
// curated CC-BY/CC0 donor pool the borrow picker digs in ADDITION to your own
// songs. The crate is the borrow engine's second source: session-matched
// suggestions (`GET /api/crate/candidates`) plus a faceted browse
// (`GET /api/crate/search`), both feeding the SAME borrow render + pad-mount.
//
// Why these live in JamDesktopCore (not inline in the view/controller): the
// borrow surface already shipped two regressions from decode logic hiding
// inside the non-testable executable target (see BorrowSourcesDecode.swift).
// The crate wire is richer — a superset candidate row, a nested track/license/
// features shape, an either-key id, and a facet→query-item builder — so it is
// decoded + assembled here where CrateModelsDecodeTests can pin it, and the
// view/controller only render + fetch.
//
// Contract source: the Vinyl Crate design spec (crateTrackContract / searchApi
// / apiEndpoints). The backend serves camelCase (borrow already does:
// `entryId`, `matchScore`, `tempoDistance`). Every field except the identity
// is defaulted so a partial analysis / seed fixture still parses — the same
// forgiving-decode discipline the match model uses (a track missing melody or
// energy is ranked on the rest, never dropped).

import Foundation

/// One session-matched crate suggestion — the superset of a borrow candidate
/// (`borrow.py` candidate: entryId/name/tempo/key/harmonic/tempoDistance) plus
/// the crate's own signals: the weighted `matchScore`, the SOURCE metadata
/// (genre/mood/tags) the pipeline can't compute, and the CC provenance
/// (`attribution` + `licenseId` + `exportEncumbered`) that MUST ride wherever a
/// crate track is shown (CC-BY obligation).
///
/// Identity: the crate track id ("crate:jamendo:123456"). Decoded from either
/// `trackId` (the crate-native key the spec renames to) OR `entryId` (the
/// borrow-superset key), so the row parses whichever the backend emits and the
/// render call (`/api/crate/{id}/borrow`) always has the id it needs.
public struct CrateCandidate: Decodable, Sendable, Identifiable, Equatable {
    public let trackId: String
    public let name: String
    public let artist: String
    public let tempo: Double
    public let key: String?
    /// Content-harmony fit (borrow.py `harmonic_compat`, 0…1). Drives the
    /// "harmonizes/fits" hint exactly as the borrow picker does.
    public let harmonic: Double
    /// The crate's weighted, confidence-normalized match score (0…1). This is
    /// the crate's ranking authority; `harmonic` is one of its inputs.
    public let matchScore: Double
    /// Octave-folded tempo distance (0 = exact / half / double time).
    public let tempoDistance: Double
    public let genre: String
    public let mood: String
    public let tags: [String]
    /// Ready-to-display CC credit ("Title by Artist — CC-BY 4.0"). REQUIRED on
    /// screen wherever this track appears.
    public let attribution: String
    public let licenseId: String?
    /// TRUE for CC-BY-SA: exporting a remix that includes this loop forces the
    /// whole export to BY-SA. The picker badges it "export-locked"; the export
    /// path (follow-up) gates on this one boolean, never re-derives it.
    public let exportEncumbered: Bool
    /// Stems this donor supplies — used for the "fills a hole in your session"
    /// hint (e.g. a drum loop for a drum-less session).
    public let availableStems: [String]

    public var id: String { trackId }

    /// Melodic parts get key + harmonic hints; drums are tempo-only. Mirrors
    /// `BorrowPart.isMelodic` so the crate rows read like the borrow rows.
    public var hasKey: Bool { !(key ?? "").isEmpty }

    private enum CodingKeys: String, CodingKey {
        case trackId, entryId, name, artist, tempo, key, harmonic, matchScore
        case tempoDistance, genre, mood, tags, attribution, licenseId
        case exportEncumbered, availableStems
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // Either id key; entryId keeps the row a strict borrow-superset.
        if let tid = try c.decodeIfPresent(String.self, forKey: .trackId) {
            trackId = tid
        } else {
            trackId = try c.decode(String.self, forKey: .entryId)
        }
        name = (try c.decodeIfPresent(String.self, forKey: .name)) ?? trackId
        artist = (try c.decodeIfPresent(String.self, forKey: .artist)) ?? ""
        tempo = (try c.decodeIfPresent(Double.self, forKey: .tempo)) ?? 0
        key = try c.decodeIfPresent(String.self, forKey: .key)
        harmonic = (try c.decodeIfPresent(Double.self, forKey: .harmonic)) ?? 0
        // matchScore is the crate authority, but a plain borrow row won't carry
        // it — fall back to harmonic so a mixed/legacy response still ranks.
        matchScore = (try c.decodeIfPresent(Double.self, forKey: .matchScore))
            ?? harmonic
        tempoDistance =
            (try c.decodeIfPresent(Double.self, forKey: .tempoDistance)) ?? 0
        genre = (try c.decodeIfPresent(String.self, forKey: .genre)) ?? ""
        mood = (try c.decodeIfPresent(String.self, forKey: .mood)) ?? ""
        tags = (try c.decodeIfPresent([String].self, forKey: .tags)) ?? []
        attribution =
            (try c.decodeIfPresent(String.self, forKey: .attribution)) ?? ""
        licenseId = try c.decodeIfPresent(String.self, forKey: .licenseId)
        exportEncumbered =
            (try c.decodeIfPresent(Bool.self, forKey: .exportEncumbered)) ?? false
        availableStems =
            (try c.decodeIfPresent([String].self, forKey: .availableStems)) ?? []
    }

    /// Memberwise init so the view/tests can synthesize rows without a network.
    public init(
        trackId: String, name: String, artist: String = "", tempo: Double,
        key: String?, harmonic: Double, matchScore: Double,
        tempoDistance: Double = 0, genre: String = "", mood: String = "",
        tags: [String] = [], attribution: String = "", licenseId: String? = nil,
        exportEncumbered: Bool = false, availableStems: [String] = []
    ) {
        self.trackId = trackId
        self.name = name
        self.artist = artist
        self.tempo = tempo
        self.key = key
        self.harmonic = harmonic
        self.matchScore = matchScore
        self.tempoDistance = tempoDistance
        self.genre = genre
        self.mood = mood
        self.tags = tags
        self.attribution = attribution
        self.licenseId = licenseId
        self.exportEncumbered = exportEncumbered
        self.availableStems = availableStems
    }
}

/// `GET /api/crate/candidates` envelope — a superset of the borrow-candidates
/// envelope (analysisId→sessionId), so the picker reads the session target the
/// backend actually ranked against.
public struct CrateCandidatesResponse: Decodable, Sendable, Equatable {
    public let sessionId: String?
    public let stem: String
    public let targetTempo: Double?
    public let targetKey: String?
    public let candidates: [CrateCandidate]

    private enum CodingKeys: String, CodingKey {
        case sessionId, stem, targetTempo, targetKey, candidates
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sessionId = try c.decodeIfPresent(String.self, forKey: .sessionId)
        stem = (try c.decodeIfPresent(String.self, forKey: .stem)) ?? ""
        targetTempo = try c.decodeIfPresent(Double.self, forKey: .targetTempo)
        targetKey = try c.decodeIfPresent(String.self, forKey: .targetKey)
        candidates =
            (try c.decodeIfPresent([CrateCandidate].self, forKey: .candidates)) ?? []
    }

    public init(
        sessionId: String?, stem: String, targetTempo: Double?,
        targetKey: String?, candidates: [CrateCandidate]
    ) {
        self.sessionId = sessionId
        self.stem = stem
        self.targetTempo = targetTempo
        self.targetKey = targetKey
        self.candidates = candidates
    }
}

/// The CC provenance sidecar (mirrors `contracts.CrateLicenseRecord`).
/// `exportEncumbered` is read straight off the wire — never re-derived from the
/// id — so the desktop gates on the exact boolean the backend authored.
public struct CrateLicense: Decodable, Sendable, Equatable {
    public let licenseId: String
    public let licenseUrl: String
    public let attribution: String
    public let source: String
    public let sourceUrl: String
    public let exportEncumbered: Bool

    private enum CodingKeys: String, CodingKey {
        case licenseId, licenseUrl, attribution, source, sourceUrl
        case exportEncumbered
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        licenseId = (try c.decodeIfPresent(String.self, forKey: .licenseId)) ?? ""
        licenseUrl = (try c.decodeIfPresent(String.self, forKey: .licenseUrl)) ?? ""
        attribution =
            (try c.decodeIfPresent(String.self, forKey: .attribution)) ?? ""
        source = (try c.decodeIfPresent(String.self, forKey: .source)) ?? ""
        sourceUrl = (try c.decodeIfPresent(String.self, forKey: .sourceUrl)) ?? ""
        exportEncumbered =
            (try c.decodeIfPresent(Bool.self, forKey: .exportEncumbered)) ?? false
    }

    public init(
        licenseId: String, licenseUrl: String = "", attribution: String,
        source: String = "", sourceUrl: String = "",
        exportEncumbered: Bool = false
    ) {
        self.licenseId = licenseId
        self.licenseUrl = licenseUrl
        self.attribution = attribution
        self.source = source
        self.sourceUrl = sourceUrl
        self.exportEncumbered = exportEncumbered
    }
}

/// The JAMN-analyzed half of a crate row (mirrors `contracts.CrateFeatures`).
/// Every field defaulted so a partial analysis parses.
public struct CrateFeatures: Decodable, Sendable, Equatable {
    public let tempoBpm: Double
    public let detectedKey: String?
    public let keyConfidence: Double
    public let durationS: Double
    public let sectionCount: Int
    public let energy: Double
    public let availableStems: [String]
    public let instrumentation: [String]
    public let hasVocals: Bool

    private enum CodingKeys: String, CodingKey {
        case tempoBpm, detectedKey, keyConfidence, durationS, sectionCount
        case energy, availableStems, instrumentation, hasVocals
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tempoBpm = (try c.decodeIfPresent(Double.self, forKey: .tempoBpm)) ?? 0
        detectedKey = try c.decodeIfPresent(String.self, forKey: .detectedKey)
        keyConfidence =
            (try c.decodeIfPresent(Double.self, forKey: .keyConfidence)) ?? 0
        durationS = (try c.decodeIfPresent(Double.self, forKey: .durationS)) ?? 0
        sectionCount =
            (try c.decodeIfPresent(Int.self, forKey: .sectionCount)) ?? 0
        energy = (try c.decodeIfPresent(Double.self, forKey: .energy)) ?? 0
        availableStems =
            (try c.decodeIfPresent([String].self, forKey: .availableStems)) ?? []
        instrumentation =
            (try c.decodeIfPresent([String].self, forKey: .instrumentation)) ?? []
        hasVocals = (try c.decodeIfPresent(Bool.self, forKey: .hasVocals)) ?? false
    }

    public init(
        tempoBpm: Double, detectedKey: String? = nil, keyConfidence: Double = 0,
        durationS: Double = 0, sectionCount: Int = 0, energy: Double = 0,
        availableStems: [String] = [], instrumentation: [String] = [],
        hasVocals: Bool = false
    ) {
        self.tempoBpm = tempoBpm
        self.detectedKey = detectedKey
        self.keyConfidence = keyConfidence
        self.durationS = durationS
        self.sectionCount = sectionCount
        self.energy = energy
        self.availableStems = availableStems
        self.instrumentation = instrumentation
        self.hasVocals = hasVocals
    }
}

/// A searchable crate catalog row (mirrors `contracts.CrateTrack`) — the UNION
/// of source metadata + license + analyzed features. This is the browse view;
/// the render/mount still goes through `/api/crate/{id}/borrow`.
public struct CrateTrack: Decodable, Sendable, Identifiable, Equatable {
    public let id: String
    public let title: String
    public let artist: String
    public let album: String
    public let year: Int?
    public let genre: String
    public let subgenres: [String]
    public let tags: [String]
    public let mood: String
    public let license: CrateLicense
    public let features: CrateFeatures
    public let previewUrl: String?
    /// Whether the stored analysis carries a `performance_graph`. False =
    /// un-renderable on the GPU-less prod box (0 pads). The picker greys these.
    public let graphAvailable: Bool

    /// Convenience passthroughs so the row renders without reaching into the
    /// nested shapes at every call site.
    public var attribution: String { license.attribution }
    public var exportEncumbered: Bool { license.exportEncumbered }
    public var tempo: Double { features.tempoBpm }
    public var key: String? { features.detectedKey }
    public var availableStems: [String] { features.availableStems }
    public var hasVocals: Bool { features.hasVocals }

    private enum CodingKeys: String, CodingKey {
        case id, title, artist, album, year, genre, subgenres, tags, mood
        case license, features, previewUrl, graphAvailable
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = (try c.decodeIfPresent(String.self, forKey: .title)) ?? id
        artist = (try c.decodeIfPresent(String.self, forKey: .artist)) ?? ""
        album = (try c.decodeIfPresent(String.self, forKey: .album)) ?? ""
        year = try c.decodeIfPresent(Int.self, forKey: .year)
        genre = (try c.decodeIfPresent(String.self, forKey: .genre)) ?? ""
        subgenres =
            (try c.decodeIfPresent([String].self, forKey: .subgenres)) ?? []
        tags = (try c.decodeIfPresent([String].self, forKey: .tags)) ?? []
        mood = (try c.decodeIfPresent(String.self, forKey: .mood)) ?? ""
        // License is mandatory on a real crate row (the compliance artifact),
        // but default an empty record so a malformed row degrades instead of
        // dropping the whole search response.
        license = (try c.decodeIfPresent(CrateLicense.self, forKey: .license))
            ?? CrateLicense(licenseId: "", attribution: "")
        features = (try c.decodeIfPresent(CrateFeatures.self, forKey: .features))
            ?? CrateFeatures(tempoBpm: 0)
        previewUrl = try c.decodeIfPresent(String.self, forKey: .previewUrl)
        graphAvailable =
            (try c.decodeIfPresent(Bool.self, forKey: .graphAvailable)) ?? false
    }

    public init(
        id: String, title: String, artist: String = "", album: String = "",
        year: Int? = nil, genre: String = "", subgenres: [String] = [],
        tags: [String] = [], mood: String = "", license: CrateLicense,
        features: CrateFeatures, previewUrl: String? = nil,
        graphAvailable: Bool = false
    ) {
        self.id = id
        self.title = title
        self.artist = artist
        self.album = album
        self.year = year
        self.genre = genre
        self.subgenres = subgenres
        self.tags = tags
        self.mood = mood
        self.license = license
        self.features = features
        self.previewUrl = previewUrl
        self.graphAvailable = graphAvailable
    }
}

/// `GET /api/crate/search` envelope — the catalog rows plus per-facet counts
/// (genre→N, mood→N, key→N …) so the UI can render a faceted sidebar without a
/// second round-trip, and the grand `total` for pagination.
public struct CrateSearchResponse: Decodable, Sendable, Equatable {
    public let tracks: [CrateTrack]
    public let facetCounts: [String: [String: Int]]
    public let total: Int

    private enum CodingKeys: String, CodingKey {
        case tracks, facetCounts, total
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tracks = (try c.decodeIfPresent([CrateTrack].self, forKey: .tracks)) ?? []
        facetCounts =
            (try c.decodeIfPresent([String: [String: Int]].self,
                                   forKey: .facetCounts)) ?? [:]
        total = (try c.decodeIfPresent(Int.self, forKey: .total)) ?? tracks.count
    }

    public init(
        tracks: [CrateTrack], facetCounts: [String: [String: Int]] = [:],
        total: Int? = nil
    ) {
        self.tracks = tracks
        self.facetCounts = facetCounts
        self.total = total ?? tracks.count
    }
}

/// Genre affinity mode — a TOGGLE, not a separate code path (spec: `s_genre`
/// SIMILAR vs CONTRAST). Sent as `genre_mode` to the candidates endpoint.
public enum CrateGenreMode: String, Sendable, CaseIterable {
    case similar
    case contrast
}

/// The user's crate browse filter — a pure value the view binds to and the
/// controller turns into query items. Keeping it here (not in the view) makes
/// the facet→query mapping unit-testable (`CrateModelsDecodeTests`): the exact
/// same facet filter must produce the exact same request on every surface
/// (parity rule 4), and the candidates + search endpoints share this shape so
/// the "apply facets on top of the ranked list" path stacks rather than forks.
public struct CrateFacetQuery: Sendable, Equatable {
    public var text: String
    public var genre: String?
    public var mood: String?
    public var tags: [String]
    public var tempoMin: Double?
    public var tempoMax: Double?
    public var key: String?
    public var camelot: String?
    public var stems: [String]
    public var hasVocals: Bool?
    public var license: String?
    /// "Clean export only" — excludes BY-SA (export_encumbered) rows. Maps to
    /// `clean_export=1`; the backend applies it pre-rank so it stacks with the
    /// session match.
    public var cleanExportOnly: Bool
    public var durationMin: Double?
    public var durationMax: Double?
    public var sort: String?

    public init(
        text: String = "", genre: String? = nil, mood: String? = nil,
        tags: [String] = [], tempoMin: Double? = nil, tempoMax: Double? = nil,
        key: String? = nil, camelot: String? = nil, stems: [String] = [],
        hasVocals: Bool? = nil, license: String? = nil,
        cleanExportOnly: Bool = false, durationMin: Double? = nil,
        durationMax: Double? = nil, sort: String? = nil
    ) {
        self.text = text
        self.genre = genre
        self.mood = mood
        self.tags = tags
        self.tempoMin = tempoMin
        self.tempoMax = tempoMax
        self.key = key
        self.camelot = camelot
        self.stems = stems
        self.hasVocals = hasVocals
        self.license = license
        self.cleanExportOnly = cleanExportOnly
        self.durationMin = durationMin
        self.durationMax = durationMax
        self.sort = sort
    }

    /// True when nothing is constraining the browse — used to fall back to the
    /// curated default order (no `q`, no facets).
    public var isEmpty: Bool {
        text.trimmingCharacters(in: .whitespaces).isEmpty && genre == nil
            && mood == nil && tags.isEmpty && tempoMin == nil && tempoMax == nil
            && key == nil && camelot == nil && stems.isEmpty && hasVocals == nil
            && license == nil && !cleanExportOnly && durationMin == nil
            && durationMax == nil
    }

    /// Facet-only query items shared by BOTH endpoints (search adds `q`/`sort`/
    /// paging on top; candidates adds `session_id`/`stem`/`genre_mode`). Emitted
    /// deterministically so the request is identical for identical filters — the
    /// parity contract the test pins. Multi-valued facets (tags/stems) repeat
    /// the key (OR within a facet, per the searchApi spec).
    public func facetQueryItems() -> [URLQueryItem] {
        var q: [URLQueryItem] = []
        if let genre, !genre.isEmpty {
            q.append(.init(name: "genre", value: genre))
        }
        if let mood, !mood.isEmpty {
            q.append(.init(name: "mood", value: mood))
        }
        for t in tags where !t.isEmpty {
            q.append(.init(name: "tags", value: t))
        }
        if let tempoMin, tempoMin > 0 {
            q.append(.init(name: "tempo_min", value: trimmed(tempoMin)))
        }
        if let tempoMax, tempoMax > 0 {
            q.append(.init(name: "tempo_max", value: trimmed(tempoMax)))
        }
        if let key, !key.isEmpty {
            q.append(.init(name: "key", value: key))
        }
        if let camelot, !camelot.isEmpty {
            q.append(.init(name: "camelot", value: camelot))
        }
        for s in stems where !s.isEmpty {
            q.append(.init(name: "stems", value: s))
        }
        if let hasVocals {
            q.append(.init(name: "has_vocals", value: hasVocals ? "1" : "0"))
        }
        if let license, !license.isEmpty {
            q.append(.init(name: "license", value: license))
        }
        if cleanExportOnly {
            q.append(.init(name: "clean_export", value: "1"))
        }
        if let durationMin, durationMin > 0 {
            q.append(.init(name: "duration_min", value: trimmed(durationMin)))
        }
        if let durationMax, durationMax > 0 {
            q.append(.init(name: "duration_max", value: trimmed(durationMax)))
        }
        return q
    }

    /// Full search query: `q` + facets + sort + paging.
    public func searchQueryItems(limit: Int, offset: Int) -> [URLQueryItem] {
        var q: [URLQueryItem] = []
        let t = text.trimmingCharacters(in: .whitespaces)
        if !t.isEmpty { q.append(.init(name: "q", value: t)) }
        q.append(contentsOf: facetQueryItems())
        if let sort, !sort.isEmpty { q.append(.init(name: "sort", value: sort)) }
        q.append(.init(name: "limit", value: String(limit)))
        q.append(.init(name: "offset", value: String(offset)))
        return q
    }

    /// Whole numbers render without a trailing ".0" so the request URL matches
    /// what a human would type (120, not 120.0).
    private func trimmed(_ v: Double) -> String {
        v == v.rounded() ? String(Int(v)) : String(v)
    }
}
