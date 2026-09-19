// CrateModels.swift  (ToneForgeEngine)
//
// Wire DTOs + the pure facet-query builder for the "Vinyl Crate" — a shared,
// curated CC-BY/CC0 donor pool the borrow picker digs in ADDITION to your own
// songs. The crate is the borrow engine's SECOND source: session-matched
// suggestions (`GET /api/crate/candidates`) plus a faceted browse
// (`GET /api/crate/search`), both feeding the SAME borrow render + pad-mount
// as an owned-song borrow (`loadCrateLoops` -> the shared borrow mount tail).
//
// Swift twin of jam-desktop's JamDesktopCore/Launchpad/CrateModels.swift and
// the web normalizeCrateTrack (backend/static/kit.js) — the port-parity rule
// (CLAUDE.md) wants ONE decode + ONE facet->query mapping per surface so an
// identical filter produces an identical request everywhere (parity rule 4),
// and the CC provenance survives to screen on every surface (a CC-BY duty).
//
// Wire shape (authored by tone_forge/crate/registry.track_to_dict +
// tone_forge_api.get_crate_candidates): the catalog metadata is top-level; the
// analyzed half is a NESTED `features` object and the license a NESTED
// `license` object, BOTH serialized snake_case (features_to_dict /
// license_to_dict). A candidate row is a strict SUPERSET of that track row —
// it adds camelCase borrow fields (`entryId`, `matchScore`, `tempoDistance`,
// `harmonic`) plus camelCase convenience mirrors (`attribution`, `licenseId`,
// `exportEncumbered`, `tempo`, `key`). Decode therefore reads snake_case for
// the nested objects and accepts the camelCase mirrors at top level, tolerant
// of either casing so a mixed/legacy/seed row still parses. Every optional is
// defaulted (a partial analysis is ranked on the rest, never dropped) — the
// same forgiving discipline the match model uses.
//
// NOT decoded with `.convertFromSnakeCase`: that strategy also rewrites
// dictionary KEYS, which would mangle `facetCounts` values that contain an
// underscore (a "hip_hop" genre bucket would become "hipHop"). Explicit keys
// keep the facet buckets byte-exact.

import Foundation

// MARK: - Flexible keyed decode

private extension KeyedDecodingContainer {
    /// First present (non-null) value among the given wire keys — the Swift
    /// twin of web `crateField`. Lets one property accept BOTH the snake_case
    /// the backend emits and the camelCase a legacy/seed payload might carry.
    func flex<T: Decodable>(_ type: T.Type, _ keys: [K]) -> T? {
        for k in keys {
            // `try?` on a `T?`-returning call flattens to `T?`: a present-but-
            // null value, an absent key, or a wrong-typed value all yield nil,
            // so we fall through to the next candidate key (forgiving decode).
            if let v = try? decodeIfPresent(T.self, forKey: k) { return v }
        }
        return nil
    }
}

// MARK: - License

/// CC license identity as a forward-compatible enum: an unrecognized id (a new
/// CC flavor the backend adds later) decodes to `.unknown` instead of throwing,
/// so a crate row is never dropped over an unfamiliar license string. The raw
/// id string is preserved on `CrateLicense.licenseId` for display.
public enum CrateLicenseKind: Sendable, Equatable {
    case cc0
    case ccBy
    case ccBySa
    case unknown

    /// Classify a raw license id ("CC0" / "CC-BY-4.0" / "CC-BY-SA-4.0" / …).
    /// Mirrors web crateLicenseIsShareAlike / crateLicenseLabel: BY-SA is
    /// checked FIRST (it also contains "BY"), then plain BY, then CC0.
    public init(id: String) {
        let u = id.uppercased().replacingOccurrences(of: " ", with: "")
        if u.range(of: "BY-?SA", options: .regularExpression) != nil {
            self = .ccBySa
        } else if u.contains("BY") {
            self = .ccBy
        } else if u.hasPrefix("CC0") || u == "CC0" {
            self = .cc0
        } else {
            self = .unknown
        }
    }

    /// Short human label for the attribution line ("CC0" / "CC-BY 4.0" /
    /// "CC-BY-SA 4.0"). Twin of web crateLicenseLabel; falls back to the raw id
    /// for `.unknown`.
    public func label(rawId: String) -> String {
        let id = rawId.trimmingCharacters(in: .whitespaces)
        let four = id.range(of: "4\\.0", options: .regularExpression) != nil
        switch self {
        case .cc0: return "CC0"
        case .ccBy: return four ? "CC-BY 4.0" : "CC-BY"
        case .ccBySa: return four ? "CC-BY-SA 4.0" : "CC-BY-SA"
        case .unknown: return id
        }
    }
}

/// The CC provenance sidecar (mirrors `contracts.CrateLicenseRecord`).
/// `exportEncumbered` is read STRAIGHT off the wire — never re-derived from the
/// id — so the client gates on the exact boolean the backend authored (True for
/// copyleft CC-BY-SA, which would force a user's remix to re-license).
public struct CrateLicense: Decodable, Sendable, Equatable {
    public let licenseId: String
    public let licenseUrl: String
    public let attribution: String
    public let source: String
    public let sourceUrl: String
    public let exportEncumbered: Bool

    /// Forward-compatible classification of `licenseId`.
    public var kind: CrateLicenseKind { CrateLicenseKind(id: licenseId) }
    /// Short label for the credit line ("CC-BY 4.0"), never throwing.
    public var label: String { kind.label(rawId: licenseId) }
    /// Copyleft badge: TRUE when the backend flagged it encumbered, OR (belt &
    /// suspenders, matching web) when the id classifies as BY-SA.
    public var isShareAlike: Bool { exportEncumbered || kind == .ccBySa }

    private enum CodingKeys: String, CodingKey {
        case licenseId = "license_id", licenseIdCamel = "licenseId"
        case licenseUrl = "license_url", licenseUrlCamel = "licenseUrl"
        case attribution
        case source
        case sourceUrl = "source_url", sourceUrlCamel = "sourceUrl"
        case exportEncumbered = "export_encumbered"
        case exportEncumberedCamel = "exportEncumbered"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        licenseId = c.flex(String.self, [.licenseId, .licenseIdCamel]) ?? ""
        licenseUrl = c.flex(String.self, [.licenseUrl, .licenseUrlCamel]) ?? ""
        attribution = c.flex(String.self, [.attribution]) ?? ""
        source = c.flex(String.self, [.source]) ?? ""
        sourceUrl = c.flex(String.self, [.sourceUrl, .sourceUrlCamel]) ?? ""
        exportEncumbered =
            c.flex(Bool.self, [.exportEncumbered, .exportEncumberedCamel]) ?? false
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

// MARK: - Features

/// The JAMN-analyzed half of a crate row (mirrors `contracts.CrateFeatures`).
/// Nested snake_case on the wire; every field defaulted so a partial analysis
/// parses.
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
        case tempoBpm = "tempo_bpm", tempoBpmCamel = "tempoBpm"
        case detectedKey = "detected_key", detectedKeyCamel = "detectedKey"
        case keyConfidence = "key_confidence", keyConfidenceCamel = "keyConfidence"
        case durationS = "duration_s", durationSCamel = "durationS"
        case sectionCount = "section_count", sectionCountCamel = "sectionCount"
        case energy
        case availableStems = "available_stems", availableStemsCamel = "availableStems"
        case instrumentation
        case hasVocals = "has_vocals", hasVocalsCamel = "hasVocals"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tempoBpm = c.flex(Double.self, [.tempoBpm, .tempoBpmCamel]) ?? 0
        detectedKey = c.flex(String.self, [.detectedKey, .detectedKeyCamel])
        keyConfidence =
            c.flex(Double.self, [.keyConfidence, .keyConfidenceCamel]) ?? 0
        durationS = c.flex(Double.self, [.durationS, .durationSCamel]) ?? 0
        sectionCount = c.flex(Int.self, [.sectionCount, .sectionCountCamel]) ?? 0
        energy = c.flex(Double.self, [.energy]) ?? 0
        availableStems =
            c.flex([String].self, [.availableStems, .availableStemsCamel]) ?? []
        instrumentation = c.flex([String].self, [.instrumentation]) ?? []
        hasVocals = c.flex(Bool.self, [.hasVocals, .hasVocalsCamel]) ?? false
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

// MARK: - Track (browse row)

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

    /// Convenience passthroughs so a row renders without reaching into the
    /// nested shapes at every call site.
    public var attribution: String { license.attribution }
    public var exportEncumbered: Bool { license.exportEncumbered }
    public var tempo: Double { features.tempoBpm }
    public var key: String? { features.detectedKey }
    public var availableStems: [String] { features.availableStems }
    public var hasVocals: Bool { features.hasVocals }
    /// Camelot code for the key badge ("" when unkeyed). Parity with web
    /// keyToCamelot / backend key_to_camelot.
    public var camelot: String { CrateCamelot.code(for: features.detectedKey) }

    private enum CodingKeys: String, CodingKey {
        case id, title, artist, album, year, genre, subgenres, tags, mood
        case license, features, previewUrl
        case graphAvailable = "graph_available", graphAvailableCamel = "graphAvailable"
        case previewUrlSnake = "preview_url"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.flex(String.self, [.title]) ?? id
        artist = c.flex(String.self, [.artist]) ?? ""
        album = c.flex(String.self, [.album]) ?? ""
        year = c.flex(Int.self, [.year])
        genre = c.flex(String.self, [.genre]) ?? ""
        subgenres = c.flex([String].self, [.subgenres]) ?? []
        tags = c.flex([String].self, [.tags]) ?? []
        mood = c.flex(String.self, [.mood]) ?? ""
        // License is mandatory on a real crate row (the compliance artifact),
        // but default an empty record so a malformed row degrades instead of
        // dropping the whole search response.
        license = c.flex(CrateLicense.self, [.license])
            ?? CrateLicense(licenseId: "", attribution: "")
        features = c.flex(CrateFeatures.self, [.features])
            ?? CrateFeatures(tempoBpm: 0)
        previewUrl = c.flex(String.self, [.previewUrl, .previewUrlSnake])
        graphAvailable =
            c.flex(Bool.self, [.graphAvailable, .graphAvailableCamel]) ?? false
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

// MARK: - Candidate (session-matched row)

/// One session-matched crate suggestion — a SUPERSET of a borrow candidate
/// (`borrow.py` candidate: entryId/name/tempo/key/harmonic/tempoDistance) plus
/// the crate's own signals: the weighted `matchScore`, the SOURCE metadata
/// (genre/mood/tags) the pipeline can't compute, and the CC provenance
/// (`attribution` + `licenseId` + `exportEncumbered`) that MUST ride wherever a
/// crate track is shown (CC-BY duty).
///
/// Identity: the crate track id ("crate:jamendo:123456"). Decoded from any of
/// `trackId` / `entryId` / `id`, whichever the backend emits, so the render
/// call (`/api/crate/{id}/borrow`) always has the id it needs.
public struct CrateCandidate: Decodable, Sendable, Identifiable, Equatable {
    public let trackId: String
    public let name: String
    public let artist: String
    public let tempo: Double
    public let key: String?
    /// Content-harmony fit (borrow.py `harmonic_compat`, 0…1).
    public let harmonic: Double
    /// The crate's weighted, confidence-normalized match score (0…1) — the
    /// ranking authority; `harmonic` is one input.
    public let matchScore: Double
    /// Octave-folded tempo distance (0 = exact / half / double time).
    public let tempoDistance: Double
    public let genre: String
    public let mood: String
    public let tags: [String]
    /// Ready-to-display CC credit. REQUIRED on screen wherever this appears.
    public let attribution: String
    public let licenseId: String?
    /// TRUE for CC-BY-SA: badged "export-locked"; the export path (follow-up)
    /// gates on this one boolean, never re-derives it.
    public let exportEncumbered: Bool
    /// Stems this donor supplies — for the "fills a hole in your session" hint.
    public let availableStems: [String]

    public var id: String { trackId }
    /// Melodic parts get key + harmonic hints; drums are tempo-only.
    public var hasKey: Bool { !(key ?? "").isEmpty }
    /// Camelot code for the key badge ("" when unkeyed).
    public var camelot: String { CrateCamelot.code(for: key) }

    private enum CodingKeys: String, CodingKey {
        case trackId, entryId, id
        case name, artist, tempo, key, harmonic
        case matchScore, matchScoreSnake = "match_score", score
        case tempoDistance, tempoDistanceSnake = "tempo_distance"
        case genre, mood, tags, attribution
        case licenseId, licenseIdSnake = "license_id"
        case exportEncumbered, exportEncumberedSnake = "export_encumbered"
        case availableStems, availableStemsSnake = "available_stems"
        case features
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // Any id key; the backend emits `id` + `entryId`, seeds may use trackId.
        if let tid = c.flex(String.self, [.trackId, .entryId, .id]) {
            trackId = tid
        } else {
            throw DecodingError.keyNotFound(
                CodingKeys.trackId,
                .init(codingPath: decoder.codingPath,
                      debugDescription: "crate candidate has no id"))
        }
        name = c.flex(String.self, [.name]) ?? trackId
        artist = c.flex(String.self, [.artist]) ?? ""
        harmonic = c.flex(Double.self, [.harmonic]) ?? 0
        // matchScore is the authority, but a plain borrow row won't carry it —
        // fall back to `score`, then harmonic, so a mixed response still ranks.
        matchScore = c.flex(Double.self, [.matchScore, .matchScoreSnake, .score])
            ?? harmonic
        tempoDistance =
            c.flex(Double.self, [.tempoDistance, .tempoDistanceSnake]) ?? 0
        genre = c.flex(String.self, [.genre]) ?? ""
        mood = c.flex(String.self, [.mood]) ?? ""
        tags = c.flex([String].self, [.tags]) ?? []
        attribution = c.flex(String.self, [.attribution]) ?? ""
        licenseId = c.flex(String.self, [.licenseId, .licenseIdSnake])
        exportEncumbered =
            c.flex(Bool.self, [.exportEncumbered, .exportEncumberedSnake]) ?? false
        // tempo/key live at top level on a candidate; fall back to the nested
        // features (a plain track row reused as a candidate). availableStems is
        // NOT mirrored at top level today, so read it from features.
        let feat = c.flex(CrateFeatures.self, [.features])
        tempo = c.flex(Double.self, [.tempo]) ?? feat?.tempoBpm ?? 0
        key = c.flex(String.self, [.key]) ?? feat?.detectedKey
        availableStems =
            c.flex([String].self, [.availableStems, .availableStemsSnake])
            ?? feat?.availableStems ?? []
    }

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

// MARK: - Envelopes

/// `GET /api/crate/candidates` envelope — a superset of borrow-candidates
/// (analysisId -> sessionId), so the picker reads the session target the backend
/// ranked against.
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

/// `GET /api/crate/search` envelope — the catalog rows plus per-facet counts
/// (genre->N, mood->N, key->N, camelot->N, license->N, stem->N) so the UI can
/// render a faceted sidebar without a second round-trip, and the grand `total`
/// for pagination.
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

// MARK: - Genre-affinity mode

/// Genre affinity mode — a TOGGLE, not a separate code path (spec `s_genre`
/// SIMILAR vs CONTRAST). Sent as `genre_mode` to the candidates endpoint.
public enum CrateGenreMode: String, Sendable, CaseIterable {
    case similar
    case contrast
}

// MARK: - Camelot wheel

/// Pure key -> Camelot code, byte-parity with web keyToCamelot and the backend
/// key_to_camelot facet neighbourhood. Pitch-class (C=0…B=11) -> wheel NUMBER,
/// split by quality (A = minor, B = major; relative maj/min share a number).
public enum CrateCamelot {
    // Index = pitch class of the tonic (SessionKey.roots order).
    private static let major = [8, 3, 10, 5, 12, 7, 2, 9, 4, 11, 6, 1]
    private static let minor = [5, 12, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10]

    public static func code(for key: String?) -> String {
        let raw = (key ?? "").trimmingCharacters(in: .whitespaces)
        // SessionKey.parse defaults garbage to C major, so gate on a real note
        // letter first — otherwise "?" would masquerade as 8B.
        guard let f = raw.first, "ABCDEFGabcdefg".contains(f) else { return "" }
        let p = SessionKey.parse(raw)
        guard let pc = SessionKey.roots.firstIndex(of: p.root) else { return "" }
        let num = p.quality == .minor ? minor[pc] : major[pc]
        return "\(num)\(p.quality == .minor ? "A" : "B")"
    }
}

// MARK: - Facet query (the parity contract)

/// The user's crate browse filter — a pure value the view binds to and the
/// client turns into query items. Keeping it here (not in the view) makes the
/// facet->query mapping unit-testable: the exact same facet filter must produce
/// the exact same request on every surface (parity rule 4), and the candidates
/// + search endpoints share this shape so "apply facets on top of the ranked
/// list" stacks rather than forks. Byte-parity with jam-desktop CrateFacetQuery.
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
        if let genre, !genre.isEmpty { q.append(.init(name: "genre", value: genre)) }
        if let mood, !mood.isEmpty { q.append(.init(name: "mood", value: mood)) }
        for t in tags where !t.isEmpty { q.append(.init(name: "tags", value: t)) }
        if let tempoMin, tempoMin > 0 {
            q.append(.init(name: "tempo_min", value: Self.trim(tempoMin)))
        }
        if let tempoMax, tempoMax > 0 {
            q.append(.init(name: "tempo_max", value: Self.trim(tempoMax)))
        }
        if let key, !key.isEmpty { q.append(.init(name: "key", value: key)) }
        if let camelot, !camelot.isEmpty {
            q.append(.init(name: "camelot", value: camelot))
        }
        for s in stems where !s.isEmpty { q.append(.init(name: "stems", value: s)) }
        if let hasVocals {
            q.append(.init(name: "has_vocals", value: hasVocals ? "1" : "0"))
        }
        if let license, !license.isEmpty {
            q.append(.init(name: "license", value: license))
        }
        if cleanExportOnly { q.append(.init(name: "clean_export", value: "1")) }
        if let durationMin, durationMin > 0 {
            q.append(.init(name: "duration_min", value: Self.trim(durationMin)))
        }
        if let durationMax, durationMax > 0 {
            q.append(.init(name: "duration_max", value: Self.trim(durationMax)))
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
    private static func trim(_ v: Double) -> String {
        v == v.rounded() ? String(Int(v)) : String(v)
    }
}
