"""Quality-aware reconstruction pipeline.

Integrates stem quality analysis, MIDI extraction, and quality gates
into a unified pipeline that produces quality-gated results.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .stem_quality import StemQuality, StemQualityAnalyzer, get_analyzer
from .contamination import ContaminationAnalysis, ContaminationDetector, get_detector
from .artifact_detection import ArtifactAnalysis, ArtifactDetector, get_artifact_detector
from .confidence_map import ConfidenceMap, ConfidenceMapper, get_confidence_mapper
from .role_classifier import RoleClassification, RoleClassifier, get_role_classifier
from .temporal_continuity import ContinuityAnalysis, TemporalContinuityAnalyzer, get_continuity_analyzer
from .quality_gates import QualityThresholds, QualityLevel
from .quality_reporter import (
    UnifiedQualityReport,
    QualityReporter,
    QualityWarning,
    WarningLevel,
    get_quality_reporter,
)

# Import archetypes for priors
try:
    from ..archetypes import (
        get_archetype,
        get_extraction_priors,
        ExtractionPriors,
    )
    ARCHETYPES_AVAILABLE = True
except ImportError:
    ARCHETYPES_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class ReconstructionConfig:
    """Configuration for the reconstruction pipeline."""

    # Quality thresholds
    thresholds: Optional[QualityThresholds] = None

    # Analysis options
    analyze_stem_quality: bool = True
    analyze_contamination: bool = True
    analyze_artifacts: bool = True
    build_confidence_map: bool = True
    classify_role: bool = True
    analyze_continuity: bool = False  # Expensive, opt-in

    # MIDI extraction options
    extract_midi: bool = True
    midi_profile: str = "default"  # default, fast, high_quality, synthwave
    use_archetypes: bool = True  # Use genre archetypes for priors

    # Quality gate behavior
    stop_on_low_quality: bool = False
    warn_on_low_quality: bool = True
    min_quality_to_proceed: float = 0.25

    # Performance options
    skip_analysis_for_high_quality: bool = False
    high_quality_threshold: float = 0.8

    @classmethod
    def fast(cls) -> "ReconstructionConfig":
        """Fast configuration - minimal analysis."""
        return cls(
            analyze_contamination=False,
            analyze_artifacts=False,
            analyze_continuity=False,
            midi_profile="fast",
        )

    @classmethod
    def thorough(cls) -> "ReconstructionConfig":
        """Thorough configuration - full analysis."""
        return cls(
            analyze_continuity=True,
            midi_profile="high_quality",
            thresholds=QualityThresholds.strict(),
        )

    @classmethod
    def for_genre(cls, genre: str) -> "ReconstructionConfig":
        """Genre-optimized configuration."""
        config = cls(
            thresholds=QualityThresholds.for_genre(genre),
        )

        # Synthwave benefits from effect suppression
        if genre.lower() in ("synthwave", "retrowave", "vaporwave"):
            config.midi_profile = "synthwave"
            config.analyze_continuity = True

        # Ambient needs continuity analysis
        elif genre.lower() in ("ambient", "drone", "shoegaze"):
            config.analyze_continuity = True

        return config


@dataclass
class AnalysisResults:
    """Results from the analysis phase of the pipeline."""

    stem_quality: Optional[StemQuality] = None
    contamination: Optional[ContaminationAnalysis] = None
    artifacts: Optional[ArtifactAnalysis] = None
    confidence_map: Optional[ConfidenceMap] = None
    role: Optional[RoleClassification] = None
    continuity: Optional[ContinuityAnalysis] = None
    priors: Optional[Any] = None  # ExtractionPriors if archetypes available
    analysis_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "stem_quality": self.stem_quality.to_dict() if self.stem_quality else None,
            "contamination": self.contamination.to_dict() if self.contamination else None,
            "artifacts": self.artifacts.to_dict() if self.artifacts else None,
            "confidence_map": {
                "global_confidence": self.confidence_map.global_confidence,
                "region_count": self.confidence_map.region_count,
            } if self.confidence_map else None,
            "role": self.role.to_dict() if self.role else None,
            "priors": self.priors.to_dict() if self.priors else None,
            "analysis_time_ms": self.analysis_time_ms,
        }


@dataclass
class ReconstructionResult:
    """Complete result from the reconstruction pipeline."""

    stem_type: str
    analysis: AnalysisResults
    midi_result: Optional[Any] = None  # MIDIExtractionResult
    quality_report: Optional[UnifiedQualityReport] = None

    # Processing metadata
    total_time_ms: float = 0.0
    config_used: Optional[ReconstructionConfig] = None
    skipped_extraction: bool = False
    skip_reason: str = ""

    @property
    def success(self) -> bool:
        """Whether reconstruction completed successfully."""
        if self.skipped_extraction:
            return False
        if self.quality_report and not self.quality_report.should_proceed:
            return False
        return True

    @property
    def notes(self) -> List:
        """Get extracted notes (if available)."""
        if self.midi_result:
            return self.midi_result.notes
        return []

    @property
    def overall_confidence(self) -> float:
        """Get overall confidence score."""
        if self.quality_report:
            return self.quality_report.overall_confidence
        return 0.0

    @property
    def warnings(self) -> List[QualityWarning]:
        """Get all quality warnings."""
        if self.quality_report:
            return self.quality_report.warnings
        return []

    def get_user_summary(self) -> str:
        """Get user-friendly summary."""
        if self.skipped_extraction:
            return f"Extraction skipped: {self.skip_reason}"

        if self.quality_report:
            return self.quality_report.get_user_summary()

        return "Reconstruction complete"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "stem_type": self.stem_type,
            "success": self.success,
            "analysis": self.analysis.to_dict(),
            "midi_result": self.midi_result.to_dict() if self.midi_result else None,
            "quality_report": self.quality_report.to_dict() if self.quality_report else None,
            "total_time_ms": self.total_time_ms,
            "skipped_extraction": self.skipped_extraction,
            "skip_reason": self.skip_reason,
        }


class ReconstructionPipeline:
    """Quality-aware reconstruction pipeline.

    Runs the full reconstruction process including:
    1. Stem quality analysis
    2. Contamination and artifact detection
    3. Role classification
    4. Confidence mapping
    5. Quality-gated MIDI extraction
    6. Comprehensive quality reporting
    """

    def __init__(
        self,
        config: Optional[ReconstructionConfig] = None,
    ):
        """Initialize the reconstruction pipeline.

        Args:
            config: Pipeline configuration (uses default if None)
        """
        self.config = config or ReconstructionConfig()

        # Initialize analyzers
        self._stem_analyzer = get_analyzer()
        self._contamination_detector = get_detector()
        self._artifact_detector = get_artifact_detector()
        self._confidence_mapper = get_confidence_mapper()
        self._role_classifier = get_role_classifier()
        self._continuity_analyzer = get_continuity_analyzer()
        self._quality_reporter = get_quality_reporter(self.config.thresholds)

        # Lazy-load MIDI extractor
        self._midi_extractor = None

    def _get_midi_extractor(self):
        """Get or create the MIDI extractor."""
        if self._midi_extractor is None:
            from ..midi import create_extractor
            self._midi_extractor = create_extractor(self.config.midi_profile)
        return self._midi_extractor

    def process(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: str,
        genre: Optional[str] = None,
        tempo: Optional[float] = None,
        key: Optional[Tuple[int, str]] = None,
        other_stems: Optional[Dict[str, np.ndarray]] = None,
    ) -> ReconstructionResult:
        """Run the full reconstruction pipeline.

        Args:
            audio: Audio signal (mono or stereo)
            sr: Sample rate
            stem_type: Type of stem ("bass", "vocals", "synth", etc.)
            genre: Detected or specified genre
            tempo: Known tempo (will be estimated if None)
            key: Known key as (root, mode)
            other_stems: Other stems for cross-stem analysis

        Returns:
            ReconstructionResult with all analysis and extraction
        """
        start_time = time.time()

        # Convert to mono for analysis
        if audio.ndim == 2:
            audio_mono = np.mean(audio, axis=0)
        else:
            audio_mono = audio

        # Phase 1: Analysis
        analysis = self._run_analysis(
            audio_mono, sr, stem_type, genre, other_stems
        )

        # Phase 2: Quality check before extraction
        preliminary_report = self._quality_reporter.generate_report(
            stem_type=stem_type,
            stem_quality=analysis.stem_quality,
            contamination=analysis.contamination,
            artifacts=analysis.artifacts,
            confidence_map=analysis.confidence_map,
            genre=genre,
        )

        # Check if we should proceed with extraction
        if self.config.stop_on_low_quality:
            if preliminary_report.overall_confidence < self.config.min_quality_to_proceed:
                return ReconstructionResult(
                    stem_type=stem_type,
                    analysis=analysis,
                    quality_report=preliminary_report,
                    total_time_ms=(time.time() - start_time) * 1000,
                    config_used=self.config,
                    skipped_extraction=True,
                    skip_reason=f"Quality too low: {preliminary_report.overall_confidence:.0%}",
                )

        # Phase 3: MIDI extraction
        midi_result = None
        if self.config.extract_midi:
            midi_result = self._run_extraction(
                audio_mono, sr, stem_type, genre, tempo, key, analysis
            )

        # Phase 4: Final quality report (including MIDI)
        final_report = self._quality_reporter.generate_report(
            stem_type=stem_type,
            stem_quality=analysis.stem_quality,
            contamination=analysis.contamination,
            artifacts=analysis.artifacts,
            confidence_map=analysis.confidence_map,
            midi_result=midi_result,
            genre=genre,
        )

        total_time = (time.time() - start_time) * 1000

        return ReconstructionResult(
            stem_type=stem_type,
            analysis=analysis,
            midi_result=midi_result,
            quality_report=final_report,
            total_time_ms=total_time,
            config_used=self.config,
        )

    def _run_analysis(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: str,
        genre: Optional[str],
        other_stems: Optional[Dict[str, np.ndarray]],
    ) -> AnalysisResults:
        """Run all analysis passes."""
        start_time = time.time()
        results = AnalysisResults()

        # Stem quality analysis
        if self.config.analyze_stem_quality:
            try:
                results.stem_quality = self._stem_analyzer.analyze(
                    audio, sr, stem_type
                )
            except Exception as e:
                logger.warning(f"Stem quality analysis failed: {e}")

        # Contamination detection
        if self.config.analyze_contamination:
            try:
                results.contamination = self._contamination_detector.detect(
                    audio, sr, stem_type,
                    other_stems=other_stems,
                )
            except Exception as e:
                logger.warning(f"Contamination detection failed: {e}")

        # Artifact detection
        if self.config.analyze_artifacts:
            try:
                results.artifacts = self._artifact_detector.detect(
                    audio, sr, stem_type
                )
            except Exception as e:
                logger.warning(f"Artifact detection failed: {e}")

        # Role classification
        if self.config.classify_role:
            try:
                results.role = self._role_classifier.classify(
                    audio, sr, stem_type
                )
            except Exception as e:
                logger.warning(f"Role classification failed: {e}")

        # Continuity analysis
        if self.config.analyze_continuity:
            try:
                results.continuity = self._continuity_analyzer.analyze(
                    audio, sr
                )
            except Exception as e:
                logger.warning(f"Continuity analysis failed: {e}")

        # Confidence map (depends on other analyses)
        if self.config.build_confidence_map:
            try:
                results.confidence_map = self._confidence_mapper.build_map(
                    audio, sr, stem_type,
                    stem_quality=results.stem_quality,
                    contamination=results.contamination,
                    artifacts=results.artifacts,
                )
            except Exception as e:
                logger.warning(f"Confidence mapping failed: {e}")

        # Get archetype-based priors
        if self.config.use_archetypes and ARCHETYPES_AVAILABLE and genre:
            try:
                results.priors = get_extraction_priors(
                    genre=genre,
                    stem_type=stem_type,
                )
                logger.debug(f"Got priors from archetype: {results.priors.source_archetype}")
            except Exception as e:
                logger.warning(f"Failed to get extraction priors: {e}")

        results.analysis_time_ms = (time.time() - start_time) * 1000
        return results

    def _run_extraction(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: str,
        genre: Optional[str],
        tempo: Optional[float],
        key: Optional[Tuple[int, str]],
        analysis: AnalysisResults,
    ):
        """Run MIDI extraction with quality context and priors."""
        extractor = self._get_midi_extractor()

        # Get extraction parameters from priors if available
        onset_threshold = 0.5
        frame_threshold = 0.4
        min_note_ms = 50.0

        if analysis.priors and hasattr(analysis.priors, 'suggested_onset_threshold'):
            priors = analysis.priors
            # Blend priors with defaults based on confidence
            conf = getattr(priors, 'threshold_confidence', 0.5)
            onset_threshold = onset_threshold * (1 - conf) + priors.suggested_onset_threshold * conf
            frame_threshold = frame_threshold * (1 - conf) + priors.suggested_frame_threshold * conf
            min_note_ms = priors.min_note_ms
            logger.debug(f"Applied priors: onset={onset_threshold:.2f}, frame={frame_threshold:.2f}")

        return extractor.extract(
            audio=audio,
            sr=sr,
            stem_type=stem_type,
            genre=genre,
            tempo=tempo,
            key=key,
            stem_quality=analysis.stem_quality,
            contamination=analysis.contamination,
            role_classification=analysis.role,
            confidence_map=analysis.confidence_map,
            onset_threshold=onset_threshold,
            frame_threshold=frame_threshold,
            min_note_ms=min_note_ms,
        )

    def analyze_only(
        self,
        audio: np.ndarray,
        sr: int,
        stem_type: str,
        genre: Optional[str] = None,
        other_stems: Optional[Dict[str, np.ndarray]] = None,
    ) -> Tuple[AnalysisResults, UnifiedQualityReport]:
        """Run analysis without MIDI extraction.

        Useful for quality assessment before committing to extraction.

        Args:
            audio: Audio signal
            sr: Sample rate
            stem_type: Type of stem
            genre: Detected genre
            other_stems: Other stems for cross-analysis

        Returns:
            Tuple of (AnalysisResults, UnifiedQualityReport)
        """
        if audio.ndim == 2:
            audio = np.mean(audio, axis=0)

        analysis = self._run_analysis(audio, sr, stem_type, genre, other_stems)

        report = self._quality_reporter.generate_report(
            stem_type=stem_type,
            stem_quality=analysis.stem_quality,
            contamination=analysis.contamination,
            artifacts=analysis.artifacts,
            confidence_map=analysis.confidence_map,
            genre=genre,
        )

        return analysis, report


# Module-level singleton
_pipeline: Optional[ReconstructionPipeline] = None


def get_pipeline(
    config: Optional[ReconstructionConfig] = None,
) -> ReconstructionPipeline:
    """Get the global reconstruction pipeline.

    Args:
        config: Optional pipeline configuration

    Returns:
        ReconstructionPipeline instance
    """
    global _pipeline
    if _pipeline is None or config is not None:
        _pipeline = ReconstructionPipeline(config=config)
    return _pipeline


def reconstruct(
    audio: np.ndarray,
    sr: int,
    stem_type: str,
    genre: Optional[str] = None,
    tempo: Optional[float] = None,
    key: Optional[Tuple[int, str]] = None,
    config: Optional[ReconstructionConfig] = None,
) -> ReconstructionResult:
    """Convenience function to run reconstruction.

    Args:
        audio: Audio signal
        sr: Sample rate
        stem_type: Type of stem
        genre: Detected genre
        tempo: Known tempo
        key: Known key
        config: Pipeline configuration

    Returns:
        ReconstructionResult
    """
    pipeline = get_pipeline(config)
    return pipeline.process(
        audio=audio,
        sr=sr,
        stem_type=stem_type,
        genre=genre,
        tempo=tempo,
        key=key,
    )
