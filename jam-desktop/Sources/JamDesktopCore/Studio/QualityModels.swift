// QualityModels.swift
//
// Decodables for POST /api/admin/analyze-quality (tone_forge_api.py
// admin_analyze_quality). All fields optional — sub-blocks are only
// present when the reconstruction pipeline is available server-side.

import Foundation

public struct QualityAnalysis: Decodable, Sendable {
    public let filename: String?
    public let durationSec: Double?
    public let sampleRate: Int?
    public let loadTimeMs: Double?
    public let reconstructionAvailable: Bool?
    public let stemQuality: StemQualityMetrics?
    public let contamination: ContaminationMetrics?
    public let artifacts: ArtifactMetrics?
    public let role: RoleClassification?
    public let confidenceMap: ConfidenceMapSummary?
    public let continuity: ContinuityMetrics?
    public let priors: ArchetypePriors?
    public let qualityReport: QualityReport?
    public let analysisTimeMs: Double?
    public let confidenceScores: ToneConfidence?
    public let detected: DetectedTone?

    enum CodingKeys: String, CodingKey {
        case filename
        case durationSec = "duration_sec"
        case sampleRate = "sample_rate"
        case loadTimeMs = "load_time_ms"
        case reconstructionAvailable = "reconstruction_available"
        case stemQuality = "stem_quality"
        case contamination, artifacts, role
        case confidenceMap = "confidence_map"
        case continuity, priors
        case qualityReport = "quality_report"
        case analysisTimeMs = "analysis_time_ms"
        case confidenceScores = "confidence_scores"
        case detected
    }

    public init(
        filename: String? = nil, durationSec: Double? = nil,
        sampleRate: Int? = nil, loadTimeMs: Double? = nil,
        reconstructionAvailable: Bool? = nil,
        stemQuality: StemQualityMetrics? = nil,
        contamination: ContaminationMetrics? = nil,
        artifacts: ArtifactMetrics? = nil,
        role: RoleClassification? = nil,
        confidenceMap: ConfidenceMapSummary? = nil,
        continuity: ContinuityMetrics? = nil,
        priors: ArchetypePriors? = nil,
        qualityReport: QualityReport? = nil,
        analysisTimeMs: Double? = nil,
        confidenceScores: ToneConfidence? = nil,
        detected: DetectedTone? = nil
    ) {
        self.filename = filename
        self.durationSec = durationSec
        self.sampleRate = sampleRate
        self.loadTimeMs = loadTimeMs
        self.reconstructionAvailable = reconstructionAvailable
        self.stemQuality = stemQuality
        self.contamination = contamination
        self.artifacts = artifacts
        self.role = role
        self.confidenceMap = confidenceMap
        self.continuity = continuity
        self.priors = priors
        self.qualityReport = qualityReport
        self.analysisTimeMs = analysisTimeMs
        self.confidenceScores = confidenceScores
        self.detected = detected
    }
}

public struct StemQualityMetrics: Decodable, Sendable {
    public let overallQuality: Double?
    public let contaminationScore: Double?
    public let transientIntegrity: Double?
    public let harmonicPurity: Double?
    public let reverbDensity: Double?
    public let stereoCoherence: Double?
    public let snrEstimate: Double?

    enum CodingKeys: String, CodingKey {
        case overallQuality = "overall_quality"
        case contaminationScore = "contamination_score"
        case transientIntegrity = "transient_integrity"
        case harmonicPurity = "harmonic_purity"
        case reverbDensity = "reverb_density"
        case stereoCoherence = "stereo_coherence"
        case snrEstimate = "snr_estimate"
    }

    public init(
        overallQuality: Double? = nil, contaminationScore: Double? = nil,
        transientIntegrity: Double? = nil, harmonicPurity: Double? = nil,
        reverbDensity: Double? = nil, stereoCoherence: Double? = nil,
        snrEstimate: Double? = nil
    ) {
        self.overallQuality = overallQuality
        self.contaminationScore = contaminationScore
        self.transientIntegrity = transientIntegrity
        self.harmonicPurity = harmonicPurity
        self.reverbDensity = reverbDensity
        self.stereoCoherence = stereoCoherence
        self.snrEstimate = snrEstimate
    }
}

public struct ContaminationMetrics: Decodable, Sendable {
    public let overallContamination: Double?
    public let bassBleed: Double?
    public let drumBleed: Double?
    public let vocalBleed: Double?
    public let reverbContamination: Double?

    enum CodingKeys: String, CodingKey {
        case overallContamination = "overall_contamination"
        case bassBleed = "bass_bleed"
        case drumBleed = "drum_bleed"
        case vocalBleed = "vocal_bleed"
        case reverbContamination = "reverb_contamination"
    }

    public init(
        overallContamination: Double? = nil, bassBleed: Double? = nil,
        drumBleed: Double? = nil, vocalBleed: Double? = nil,
        reverbContamination: Double? = nil
    ) {
        self.overallContamination = overallContamination
        self.bassBleed = bassBleed
        self.drumBleed = drumBleed
        self.vocalBleed = vocalBleed
        self.reverbContamination = reverbContamination
    }
}

public struct ArtifactMetrics: Decodable, Sendable {
    public let clippingDetected: Bool?
    public let clippingSeverity: Double?
    public let noiseFloorDb: Double?
    public let dcOffset: Double?
    public let phaseIssues: Bool?

    enum CodingKeys: String, CodingKey {
        case clippingDetected = "clipping_detected"
        case clippingSeverity = "clipping_severity"
        case noiseFloorDb = "noise_floor_db"
        case dcOffset = "dc_offset"
        case phaseIssues = "phase_issues"
    }

    public init(
        clippingDetected: Bool? = nil, clippingSeverity: Double? = nil,
        noiseFloorDb: Double? = nil, dcOffset: Double? = nil,
        phaseIssues: Bool? = nil
    ) {
        self.clippingDetected = clippingDetected
        self.clippingSeverity = clippingSeverity
        self.noiseFloorDb = noiseFloorDb
        self.dcOffset = dcOffset
        self.phaseIssues = phaseIssues
    }
}

public struct RoleClassification: Decodable, Sendable {
    public let primaryRole: String?
    public let confidence: Double?
    public let spectralProfile: String?
    public let temporalProfile: String?

    enum CodingKeys: String, CodingKey {
        case primaryRole = "primary_role"
        case confidence
        case spectralProfile = "spectral_profile"
        case temporalProfile = "temporal_profile"
    }

    public init(
        primaryRole: String? = nil, confidence: Double? = nil,
        spectralProfile: String? = nil, temporalProfile: String? = nil
    ) {
        self.primaryRole = primaryRole
        self.confidence = confidence
        self.spectralProfile = spectralProfile
        self.temporalProfile = temporalProfile
    }
}

public struct ConfidenceMapSummary: Decodable, Sendable {
    public let globalConfidence: Double?
    public let regionCount: Int?
    public let lowConfidenceRegions: Int?
    public let highConfidenceRegions: Int?

    enum CodingKeys: String, CodingKey {
        case globalConfidence = "global_confidence"
        case regionCount = "region_count"
        case lowConfidenceRegions = "low_confidence_regions"
        case highConfidenceRegions = "high_confidence_regions"
    }

    public init(
        globalConfidence: Double? = nil, regionCount: Int? = nil,
        lowConfidenceRegions: Int? = nil, highConfidenceRegions: Int? = nil
    ) {
        self.globalConfidence = globalConfidence
        self.regionCount = regionCount
        self.lowConfidenceRegions = lowConfidenceRegions
        self.highConfidenceRegions = highConfidenceRegions
    }
}

public struct ContinuityMetrics: Decodable, Sendable {
    public let sustainedRegions: Int?
    public let avgSustainDuration: Double?
    public let pitchStability: Double?

    enum CodingKeys: String, CodingKey {
        case sustainedRegions = "sustained_regions"
        case avgSustainDuration = "avg_sustain_duration"
        case pitchStability = "pitch_stability"
    }

    public init(
        sustainedRegions: Int? = nil, avgSustainDuration: Double? = nil,
        pitchStability: Double? = nil
    ) {
        self.sustainedRegions = sustainedRegions
        self.avgSustainDuration = avgSustainDuration
        self.pitchStability = pitchStability
    }
}

public struct ArchetypePriors: Decodable, Sendable {
    public let sourceArchetype: String?
    public let onsetThreshold: Double?
    public let frameThreshold: Double?
    public let minNoteMs: Double?
    public let quantizationStrength: Double?

    enum CodingKeys: String, CodingKey {
        case sourceArchetype = "source_archetype"
        case onsetThreshold = "onset_threshold"
        case frameThreshold = "frame_threshold"
        case minNoteMs = "min_note_ms"
        case quantizationStrength = "quantization_strength"
    }

    public init(
        sourceArchetype: String? = nil, onsetThreshold: Double? = nil,
        frameThreshold: Double? = nil, minNoteMs: Double? = nil,
        quantizationStrength: Double? = nil
    ) {
        self.sourceArchetype = sourceArchetype
        self.onsetThreshold = onsetThreshold
        self.frameThreshold = frameThreshold
        self.minNoteMs = minNoteMs
        self.quantizationStrength = quantizationStrength
    }
}

public struct QualityReport: Decodable, Sendable {
    public let overallConfidence: Double?
    public let qualityLevel: String?
    public let shouldProceed: Bool?
    public let warningCount: Int?
    public let warnings: [QualityWarning]?

    enum CodingKeys: String, CodingKey {
        case overallConfidence = "overall_confidence"
        case qualityLevel = "quality_level"
        case shouldProceed = "should_proceed"
        case warningCount = "warning_count"
        case warnings
    }

    public init(
        overallConfidence: Double? = nil, qualityLevel: String? = nil,
        shouldProceed: Bool? = nil, warningCount: Int? = nil,
        warnings: [QualityWarning]? = nil
    ) {
        self.overallConfidence = overallConfidence
        self.qualityLevel = qualityLevel
        self.shouldProceed = shouldProceed
        self.warningCount = warningCount
        self.warnings = warnings
    }
}

public struct QualityWarning: Decodable, Sendable {
    public let level: String?
    public let category: String?
    public let message: String?
    public let recommendation: String?

    public init(
        level: String? = nil, category: String? = nil,
        message: String? = nil, recommendation: String? = nil
    ) {
        self.level = level
        self.category = category
        self.message = message
        self.recommendation = recommendation
    }
}

public struct DetectedTone: Decodable, Sendable {
    public let ampFamily: String?
    public let gain: Double?

    enum CodingKeys: String, CodingKey {
        case ampFamily = "amp_family"
        case gain
    }

    public init(ampFamily: String? = nil, gain: Double? = nil) {
        self.ampFamily = ampFamily
        self.gain = gain
    }
}
