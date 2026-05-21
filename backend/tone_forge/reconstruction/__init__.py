"""Semantic reconstruction pipeline for ToneForge.

This module provides contamination-aware, multi-pass reconstruction
that treats stems as approximations rather than ground truth.

Core Components:
- StemQualityAnalyzer: Assess separation quality per stem
- ContaminationDetector: Detect bleed and artifacts
- RoleClassifier: Classify by musical role, not instrument
- TemporalContinuityAnalyzer: Track sustained content
- QualityGates: Gate decisions based on confidence

Usage:
    from tone_forge.reconstruction import (
        analyze_stem_quality,
        detect_contamination,
        classify_role,
        analyze_continuity,
        get_quality_gates,
    )

    # Analyze stem quality
    qualities = analyze_stem_quality(stems, sr)

    # Classify musical role
    role = classify_role(audio, sr, stem_type="bass")

    # Analyze temporal continuity
    continuity = analyze_continuity(audio, sr)

    # Check if quality is sufficient
    gates = get_quality_gates()
    if gates.stem_quality_sufficient(qualities["bass"]):
        # Proceed with extraction
        ...
"""
from __future__ import annotations

from .stem_quality import (
    StemQuality,
    StemQualityAnalyzer,
    ConfidenceRegion,
    get_analyzer,
    analyze_stem_quality,
)
from .contamination import (
    ContaminationType,
    ContaminationEvent,
    ContaminationAnalysis,
    ContaminationDetector,
    get_detector,
    detect_contamination,
)
from .artifact_detection import (
    ArtifactType,
    DetectedArtifact,
    ArtifactAnalysis,
    ArtifactDetector,
    get_artifact_detector,
    detect_artifacts,
)
from .confidence_map import (
    RegionConfidence,
    ConfidenceMap,
    ConfidenceMapper,
    get_confidence_mapper,
    build_confidence_map,
)
from .quality_gates import (
    QualityThresholds,
    QualityReport,
    QualityGates,
    QualityLevel,
    GateStatus,
    get_quality_gates,
)
from .quality_reporter import (
    WarningLevel,
    WarningCategory,
    QualityWarning,
    MIDIQualityMetrics,
    UnifiedQualityReport,
    QualityReporter,
    get_quality_reporter,
    generate_quality_report,
)
from .pipeline import (
    ReconstructionConfig,
    AnalysisResults,
    ReconstructionResult,
    ReconstructionPipeline,
    get_pipeline,
    reconstruct,
)
from .role_classifier import (
    MusicalRole,
    SpectralProfile,
    TemporalProfile,
    RoleFeatures,
    RoleClassification,
    RoleClassifier,
    get_role_classifier,
    classify_role,
)
from .temporal_continuity import (
    EnvelopeType,
    PhraseType,
    HarmonicTrack,
    ContinuityRegion,
    Phrase,
    ContinuityAnalysis,
    HarmonicTracker,
    PhraseDetector,
    TemporalContinuityAnalyzer,
    get_continuity_analyzer,
    get_harmonic_tracker,
    get_phrase_detector,
    analyze_continuity,
)

__all__ = [
    # Stem quality
    "StemQuality",
    "StemQualityAnalyzer",
    "ConfidenceRegion",
    "get_analyzer",
    "analyze_stem_quality",
    # Contamination
    "ContaminationType",
    "ContaminationEvent",
    "ContaminationAnalysis",
    "ContaminationDetector",
    "get_detector",
    "detect_contamination",
    # Artifacts
    "ArtifactType",
    "DetectedArtifact",
    "ArtifactAnalysis",
    "ArtifactDetector",
    "get_artifact_detector",
    "detect_artifacts",
    # Confidence
    "RegionConfidence",
    "ConfidenceMap",
    "ConfidenceMapper",
    "get_confidence_mapper",
    "build_confidence_map",
    # Quality gates
    "QualityThresholds",
    "QualityReport",
    "QualityGates",
    "QualityLevel",
    "GateStatus",
    "get_quality_gates",
    # Quality reporter
    "WarningLevel",
    "WarningCategory",
    "QualityWarning",
    "MIDIQualityMetrics",
    "UnifiedQualityReport",
    "QualityReporter",
    "get_quality_reporter",
    "generate_quality_report",
    # Pipeline
    "ReconstructionConfig",
    "AnalysisResults",
    "ReconstructionResult",
    "ReconstructionPipeline",
    "get_pipeline",
    "reconstruct",
    # Role classification
    "MusicalRole",
    "SpectralProfile",
    "TemporalProfile",
    "RoleFeatures",
    "RoleClassification",
    "RoleClassifier",
    "get_role_classifier",
    "classify_role",
    # Temporal continuity
    "EnvelopeType",
    "PhraseType",
    "HarmonicTrack",
    "ContinuityRegion",
    "Phrase",
    "ContinuityAnalysis",
    "HarmonicTracker",
    "PhraseDetector",
    "TemporalContinuityAnalyzer",
    "get_continuity_analyzer",
    "get_harmonic_tracker",
    "get_phrase_detector",
    "analyze_continuity",
]
