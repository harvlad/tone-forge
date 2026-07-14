// ArrangementModels.swift
//
// Decodables for the Studio Phase 3 endpoints:
//   POST /api/preview-waveform  (peaks/rms envelope, ~1000 points)
//   POST /api/detect-sections   (ArrangementAnalysis.to_dict)
//   POST /api/analyze-region    (RegionAnalysisResult.to_dict)
// All optional/tolerant — shapes verified against tone_forge_api.py,
// tone_forge/analysis/sections.py:205 and
// tone_forge/reconstruction/region_analyzer.py:139.

import Foundation

// MARK: - preview-waveform

public struct WaveformPreview: Decodable, Sendable {
    public let peaksPositive: [Double]
    public let peaksNegative: [Double]
    public let rms: [Double]
    public let sampleRate: Int?
    public let durationSec: Double?
    public let filename: String?

    enum CodingKeys: String, CodingKey {
        case peaksPositive = "peaks_positive"
        case peaksNegative = "peaks_negative"
        case rms
        case sampleRate = "sample_rate"
        case durationSec = "duration_sec"
        case filename
    }

    public init(
        peaksPositive: [Double], peaksNegative: [Double], rms: [Double],
        sampleRate: Int? = nil, durationSec: Double? = nil,
        filename: String? = nil
    ) {
        self.peaksPositive = peaksPositive
        self.peaksNegative = peaksNegative
        self.rms = rms
        self.sampleRate = sampleRate
        self.durationSec = durationSec
        self.filename = filename
    }
}

// MARK: - detect-sections

public struct ArrangementAnalysisDTO: Decodable, Sendable {
    public let sections: [ArrangementSection]
    public let duration: Double?
    public let tempoBpm: Double?
    public let key: String?
    public let energyCurve: [Double]?
    public let energyCurveSr: Double?

    enum CodingKeys: String, CodingKey {
        case sections, duration, key
        case tempoBpm = "tempo_bpm"
        case energyCurve = "energy_curve"
        case energyCurveSr = "energy_curve_sr"
    }

    public init(
        sections: [ArrangementSection], duration: Double? = nil,
        tempoBpm: Double? = nil, key: String? = nil,
        energyCurve: [Double]? = nil, energyCurveSr: Double? = nil
    ) {
        self.sections = sections
        self.duration = duration
        self.tempoBpm = tempoBpm
        self.key = key
        self.energyCurve = energyCurve
        self.energyCurveSr = energyCurveSr
    }
}

public struct ArrangementSection: Decodable, Sendable, Identifiable {
    public let type: String?
    public let startTime: Double
    public let endTime: Double
    public let confidence: Double?
    public let energyMean: Double?
    public let energyPeak: Double?
    public let noteDensity: Double?
    public let guidanceMode: String?
    public let guidanceConfidence: Double?
    public let guidanceReason: String?
    public let dominantStem: String?
    public let structuralRole: String?
    public let bpm: Double?

    public var id: Double { startTime }
    public var duration: Double { endTime - startTime }

    enum CodingKeys: String, CodingKey {
        case type, confidence, bpm
        case startTime = "start_time"
        case endTime = "end_time"
        case energyMean = "energy_mean"
        case energyPeak = "energy_peak"
        case noteDensity = "note_density"
        case guidanceMode = "guidance_mode"
        case guidanceConfidence = "guidance_confidence"
        case guidanceReason = "guidance_reason"
        case dominantStem = "dominant_stem"
        case structuralRole = "structural_role"
    }

    public init(
        type: String? = nil, startTime: Double, endTime: Double,
        confidence: Double? = nil, energyMean: Double? = nil,
        energyPeak: Double? = nil, noteDensity: Double? = nil,
        guidanceMode: String? = nil, guidanceConfidence: Double? = nil,
        guidanceReason: String? = nil, dominantStem: String? = nil,
        structuralRole: String? = nil, bpm: Double? = nil
    ) {
        self.type = type
        self.startTime = startTime
        self.endTime = endTime
        self.confidence = confidence
        self.energyMean = energyMean
        self.energyPeak = energyPeak
        self.noteDensity = noteDensity
        self.guidanceMode = guidanceMode
        self.guidanceConfidence = guidanceConfidence
        self.guidanceReason = guidanceReason
        self.dominantStem = dominantStem
        self.structuralRole = structuralRole
        self.bpm = bpm
    }
}

// MARK: - analyze-region

public struct RegionAnalysisDTO: Decodable, Sendable {
    public let bounds: RegionBounds?
    public let sectionType: String?
    public let notes: [RegionNote]?
    public let noteCount: Int?
    public let confidence: RegionConfidence?
    public let provenance: RegionProvenance?
    public let audioFeatures: RegionAudioFeatures?

    enum CodingKeys: String, CodingKey {
        case bounds, notes, confidence, provenance
        case sectionType = "section_type"
        case noteCount = "note_count"
        case audioFeatures = "audio_features"
    }

    public init(
        bounds: RegionBounds? = nil, sectionType: String? = nil,
        notes: [RegionNote]? = nil, noteCount: Int? = nil,
        confidence: RegionConfidence? = nil,
        provenance: RegionProvenance? = nil,
        audioFeatures: RegionAudioFeatures? = nil
    ) {
        self.bounds = bounds
        self.sectionType = sectionType
        self.notes = notes
        self.noteCount = noteCount
        self.confidence = confidence
        self.provenance = provenance
        self.audioFeatures = audioFeatures
    }
}

public struct RegionBounds: Decodable, Sendable {
    public let start: Double?
    public let end: Double?
    public let duration: Double?

    public init(
        start: Double? = nil, end: Double? = nil, duration: Double? = nil
    ) {
        self.start = start
        self.end = end
        self.duration = duration
    }
}

public struct RegionNote: Decodable, Sendable {
    public let pitch: Double?
    public let start: Double?
    public let end: Double?
    public let velocity: Double?
    public let confidence: Double?

    public init(
        pitch: Double? = nil, start: Double? = nil, end: Double? = nil,
        velocity: Double? = nil, confidence: Double? = nil
    ) {
        self.pitch = pitch
        self.start = start
        self.end = end
        self.velocity = velocity
        self.confidence = confidence
    }
}

public struct RegionConfidence: Decodable, Sendable {
    public let overall: Double?
    public let noteConfidence: Double?
    public let timingConfidence: Double?
    public let pitchConfidence: Double?
    public let velocityConfidence: Double?
    public let needsCleanup: Bool?
    public let suggestedPasses: [String]?

    enum CodingKeys: String, CodingKey {
        case overall
        case noteConfidence = "note_confidence"
        case timingConfidence = "timing_confidence"
        case pitchConfidence = "pitch_confidence"
        case velocityConfidence = "velocity_confidence"
        case needsCleanup = "needs_cleanup"
        case suggestedPasses = "suggested_passes"
    }

    public init(
        overall: Double? = nil, noteConfidence: Double? = nil,
        timingConfidence: Double? = nil, pitchConfidence: Double? = nil,
        velocityConfidence: Double? = nil, needsCleanup: Bool? = nil,
        suggestedPasses: [String]? = nil
    ) {
        self.overall = overall
        self.noteConfidence = noteConfidence
        self.timingConfidence = timingConfidence
        self.pitchConfidence = pitchConfidence
        self.velocityConfidence = velocityConfidence
        self.needsCleanup = needsCleanup
        self.suggestedPasses = suggestedPasses
    }
}

public struct RegionProvenance: Decodable, Sendable {
    public let detectorContributions: [String: Double]?
    public let cleanupPassesApplied: [String]?
    public let correctionsMade: Int?
    public let fpRisk: Double?
    public let fnRisk: Double?

    enum CodingKeys: String, CodingKey {
        case detectorContributions = "detector_contributions"
        case cleanupPassesApplied = "cleanup_passes_applied"
        case correctionsMade = "corrections_made"
        case fpRisk = "fp_risk"
        case fnRisk = "fn_risk"
    }

    public init(
        detectorContributions: [String: Double]? = nil,
        cleanupPassesApplied: [String]? = nil, correctionsMade: Int? = nil,
        fpRisk: Double? = nil, fnRisk: Double? = nil
    ) {
        self.detectorContributions = detectorContributions
        self.cleanupPassesApplied = cleanupPassesApplied
        self.correctionsMade = correctionsMade
        self.fpRisk = fpRisk
        self.fnRisk = fnRisk
    }
}

public struct RegionAudioFeatures: Decodable, Sendable {
    public let energyMean: Double?
    public let energyPeak: Double?
    public let spectralCentroid: Double?
    public let tempoLocal: Double?

    enum CodingKeys: String, CodingKey {
        case energyMean = "energy_mean"
        case energyPeak = "energy_peak"
        case spectralCentroid = "spectral_centroid"
        case tempoLocal = "tempo_local"
    }

    public init(
        energyMean: Double? = nil, energyPeak: Double? = nil,
        spectralCentroid: Double? = nil, tempoLocal: Double? = nil
    ) {
        self.energyMean = energyMean
        self.energyPeak = energyPeak
        self.spectralCentroid = spectralCentroid
        self.tempoLocal = tempoLocal
    }
}
