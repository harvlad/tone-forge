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
        get_quality_gates,
    )

    # Analyze stem quality
    qualities = analyze_stem_quality(stems, sr)

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
    get_quality_gates,
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
    "get_quality_gates",
]
