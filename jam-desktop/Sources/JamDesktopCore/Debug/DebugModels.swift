// DebugModels.swift
//
// Wire models for the Debug window (port of backend/static/debug.js).
// Four public endpoints, no auth:
//   GET /api/debug/sessions → session picker catalog
//   GET /api/session/{id}   → asdict(SessionBundle) — NOT the SongBundle
//                             shape ToneForgeEngine decodes, so the
//                             Inspector needs its own Decodables
//   GET /api/debug/corpus   → song_trial_corpus.json verbatim
//   GET /api/history        → lightweight history projection (already
//                             carries tempo_bpm/detected_key; the web's
//                             full=1 fetch is unnecessary here)
//
// Field names mirror backend/tone_forge/contracts.py. Everything but
// identity fields is optional: payloads pass through numpy conversion
// and legacy entries omit keys freely.

import Foundation

// MARK: - /api/debug/sessions

public struct DebugSessionsResponse: Decodable, Sendable {
    public let sessions: [DebugSessionSummary]
}

public struct DebugSessionSummary: Decodable, Identifiable, Sendable, Equatable {
    public let id: String
    public let name: String
    public let timestamp: String?
    public let detectedType: String?
    public let sectionCount: Int?
    public let hasDebugFeatures: Bool?

    enum CodingKeys: String, CodingKey {
        case id, name, timestamp
        case detectedType = "detected_type"
        case sectionCount = "section_count"
        case hasDebugFeatures = "has_debug_features"
    }

    public init(
        id: String, name: String, timestamp: String? = nil,
        detectedType: String? = nil, sectionCount: Int? = nil,
        hasDebugFeatures: Bool? = nil
    ) {
        self.id = id
        self.name = name
        self.timestamp = timestamp
        self.detectedType = detectedType
        self.sectionCount = sectionCount
        self.hasDebugFeatures = hasDebugFeatures
    }
}

// MARK: - /api/session/{id} (SessionBundle projection)

public struct DebugBundle: Decodable, Sendable {
    public let sessionId: String?
    public let audio: DebugAudio?
    public let understanding: DebugUnderstanding?

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case audio, understanding
    }

    public init(
        sessionId: String? = nil, audio: DebugAudio? = nil,
        understanding: DebugUnderstanding? = nil
    ) {
        self.sessionId = sessionId
        self.audio = audio
        self.understanding = understanding
    }

    public var sections: [DebugSection] { understanding?.sections ?? [] }

    /// Beat-snapped chords when present (parity with debug.js getChords).
    public var chords: [DebugChord] {
        if let snapped = understanding?.chordsBeatSnapped, !snapped.isEmpty {
            return snapped
        }
        return understanding?.chords ?? []
    }

    /// Audio duration, falling back to the last section edge.
    public var duration: Double {
        if let d = audio?.durationS, d > 0 { return d }
        return sections.map { $0.endS ?? 0 }.max() ?? 0
    }
}

public struct DebugAudio: Decodable, Sendable {
    public let durationS: Double?

    enum CodingKeys: String, CodingKey {
        case durationS = "duration_s"
    }

    public init(durationS: Double? = nil) {
        self.durationS = durationS
    }
}

public struct DebugUnderstanding: Decodable, Sendable {
    public let sections: [DebugSection]?
    public let chords: [DebugChord]?
    public let chordsBeatSnapped: [DebugChord]?

    enum CodingKeys: String, CodingKey {
        case sections, chords
        case chordsBeatSnapped = "chords_beat_snapped"
    }

    public init(
        sections: [DebugSection]? = nil, chords: [DebugChord]? = nil,
        chordsBeatSnapped: [DebugChord]? = nil
    ) {
        self.sections = sections
        self.chords = chords
        self.chordsBeatSnapped = chordsBeatSnapped
    }
}

public struct DebugSection: Decodable, Sendable {
    public let startS: Double?
    public let endS: Double?
    public let label: String?
    public let guidanceMode: String?
    public let guidanceConfidence: Double?
    public let guidanceReason: String?
    public let dominantStem: String?
    public let bpm: Double?
    public let landmarkNotes: [LandmarkNote]?
    public let debugFeatures: [StemDebugFeatures]?

    enum CodingKeys: String, CodingKey {
        case label, bpm
        case startS = "start_s"
        case endS = "end_s"
        case guidanceMode = "guidance_mode"
        case guidanceConfidence = "guidance_confidence"
        case guidanceReason = "guidance_reason"
        case dominantStem = "dominant_stem"
        case landmarkNotes = "landmark_notes"
        case debugFeatures = "debug_features"
    }

    public init(
        startS: Double? = nil, endS: Double? = nil, label: String? = nil,
        guidanceMode: String? = nil, guidanceConfidence: Double? = nil,
        guidanceReason: String? = nil, dominantStem: String? = nil,
        bpm: Double? = nil, landmarkNotes: [LandmarkNote]? = nil,
        debugFeatures: [StemDebugFeatures]? = nil
    ) {
        self.startS = startS
        self.endS = endS
        self.label = label
        self.guidanceMode = guidanceMode
        self.guidanceConfidence = guidanceConfidence
        self.guidanceReason = guidanceReason
        self.dominantStem = dominantStem
        self.bpm = bpm
        self.landmarkNotes = landmarkNotes
        self.debugFeatures = debugFeatures
    }
}

public struct LandmarkNote: Decodable, Sendable {
    public let pitch: Double?
    public let start: Double?
    public let end: Double?
    public let velocity: Int?

    public init(
        pitch: Double? = nil, start: Double? = nil, end: Double? = nil,
        velocity: Int? = nil
    ) {
        self.pitch = pitch
        self.start = start
        self.end = end
        self.velocity = velocity
    }
}

public struct DebugChord: Decodable, Sendable {
    public let startS: Double?
    public let endS: Double?
    public let symbol: String?

    enum CodingKeys: String, CodingKey {
        case symbol
        case startS = "start_s"
        case endS = "end_s"
    }

    public init(startS: Double? = nil, endS: Double? = nil, symbol: String? = nil) {
        self.startS = startS
        self.endS = endS
        self.symbol = symbol
    }
}

/// Per-stem SectionFeatures — the schema is a frozen backend dataclass
/// (section_features.py), so a fixed struct replaces the web's dynamic
/// column reflection. Section-level fields (chord_density_per_s,
/// chord_count_in_section, duration_s) still decode for the radar but
/// the per-stem table hides them, matching debug.js
/// SECTION_LEVEL_FEATURES.
public struct StemDebugFeatures: Decodable, Sendable {
    public let stemName: String?
    public let chordDensityPerS: Double?
    public let monophonicRatio: Double?
    public let repetitionScore: Double?
    public let repetitionPeriodBeats: Double?
    public let polyphonyScore: Double?
    public let leadActivityScore: Double?
    public let pitchClassDiversity: Double?
    public let voicedFrameRatio: Double?
    public let noteCount: Int?

    enum CodingKeys: String, CodingKey {
        case stemName = "stem_name"
        case chordDensityPerS = "chord_density_per_s"
        case monophonicRatio = "monophonic_ratio"
        case repetitionScore = "repetition_score"
        case repetitionPeriodBeats = "repetition_period_beats"
        case polyphonyScore = "polyphony_score"
        case leadActivityScore = "lead_activity_score"
        case pitchClassDiversity = "pitch_class_diversity"
        case voicedFrameRatio = "voiced_frame_ratio"
        case noteCount = "note_count"
    }

    public init(
        stemName: String? = nil, chordDensityPerS: Double? = nil,
        monophonicRatio: Double? = nil, repetitionScore: Double? = nil,
        repetitionPeriodBeats: Double? = nil, polyphonyScore: Double? = nil,
        leadActivityScore: Double? = nil, pitchClassDiversity: Double? = nil,
        voicedFrameRatio: Double? = nil, noteCount: Int? = nil
    ) {
        self.stemName = stemName
        self.chordDensityPerS = chordDensityPerS
        self.monophonicRatio = monophonicRatio
        self.repetitionScore = repetitionScore
        self.repetitionPeriodBeats = repetitionPeriodBeats
        self.polyphonyScore = polyphonyScore
        self.leadActivityScore = leadActivityScore
        self.pitchClassDiversity = pitchClassDiversity
        self.voicedFrameRatio = voicedFrameRatio
        self.noteCount = noteCount
    }
}

// MARK: - /api/debug/corpus

public struct DebugCorpus: Decodable, Sendable {
    public let songs: [CorpusSong]

    public init(songs: [CorpusSong]) {
        self.songs = songs
    }
}

public struct CorpusSong: Decodable, Sendable {
    public let slug: String?
    public let title: String?
    public let artist: String?
    public let groundTruthSections: [GroundTruthSection]?

    enum CodingKeys: String, CodingKey {
        case slug, title, artist
        case groundTruthSections = "ground_truth_sections"
    }

    public init(
        slug: String? = nil, title: String? = nil, artist: String? = nil,
        groundTruthSections: [GroundTruthSection]? = nil
    ) {
        self.slug = slug
        self.title = title
        self.artist = artist
        self.groundTruthSections = groundTruthSections
    }
}

public struct GroundTruthSection: Decodable, Sendable {
    public let label: String?
    public let guidanceMode: String?

    enum CodingKeys: String, CodingKey {
        case label
        case guidanceMode = "guidance_mode"
    }

    public init(label: String? = nil, guidanceMode: String? = nil) {
        self.label = label
        self.guidanceMode = guidanceMode
    }
}

// MARK: - /api/history (Debug projection)

/// Lightweight history row for the History tab. ToneForgeEngine's
/// HistoryEntry lacks tempo_bpm/detected_key/filename, hence a
/// Debug-local type.
public struct DebugHistoryRow: Decodable, Identifiable, Sendable {
    public let id: String
    public let timestamp: String?
    public let name: String?
    public let filename: String?
    public let detectedType: String?
    public let tempoBpm: Double?
    public let detectedKey: String?

    enum CodingKeys: String, CodingKey {
        case id, timestamp, name, filename
        case detectedType = "detected_type"
        case tempoBpm = "tempo_bpm"
        case detectedKey = "detected_key"
    }

    public init(
        id: String, timestamp: String? = nil, name: String? = nil,
        filename: String? = nil, detectedType: String? = nil,
        tempoBpm: Double? = nil, detectedKey: String? = nil
    ) {
        self.id = id
        self.timestamp = timestamp
        self.name = name
        self.filename = filename
        self.detectedType = detectedType
        self.tempoBpm = tempoBpm
        self.detectedKey = detectedKey
    }
}

public struct DebugHistoryResponse: Decodable, Sendable {
    public let history: [DebugHistoryRow]
}
