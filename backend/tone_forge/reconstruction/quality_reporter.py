"""Unified quality reporting for the reconstruction pipeline.

Integrates stem quality, MIDI extraction, and confidence analysis
into actionable quality reports with user-facing warnings.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .stem_quality import StemQuality
from .contamination import ContaminationAnalysis
from .artifact_detection import ArtifactAnalysis
from .confidence_map import ConfidenceMap, RegionConfidence
from .quality_gates import (
    QualityGates,
    QualityThresholds,
    QualityReport,
    QualityLevel,
    GateStatus,
)

logger = logging.getLogger(__name__)


class WarningLevel(str, Enum):
    """Severity level for quality warnings."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class WarningCategory(str, Enum):
    """Category of quality warning."""

    STEM_QUALITY = "stem_quality"
    CONTAMINATION = "contamination"
    ARTIFACT = "artifact"
    MIDI_EXTRACTION = "midi_extraction"
    CONFIDENCE = "confidence"
    TIMING = "timing"
    GENERAL = "general"


@dataclass
class QualityWarning:
    """A single quality warning to surface to users."""

    level: WarningLevel
    category: WarningCategory
    message: str
    details: str = ""
    time_range: Optional[Tuple[float, float]] = None
    recommendation: str = ""

    @property
    def is_critical(self) -> bool:
        """Whether this is a critical warning."""
        return self.level == WarningLevel.CRITICAL

    @property
    def is_error(self) -> bool:
        """Whether this is an error-level warning."""
        return self.level in (WarningLevel.ERROR, WarningLevel.CRITICAL)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "level": self.level.value,
            "category": self.category.value,
            "message": self.message,
            "details": self.details,
            "time_range": self.time_range,
            "recommendation": self.recommendation,
        }


@dataclass
class MIDIQualityMetrics:
    """Quality metrics specific to MIDI extraction."""

    note_count: int
    average_confidence: float
    low_confidence_note_count: int
    high_confidence_note_count: int
    average_velocity: float
    velocity_variance: float
    note_density: float  # Notes per second
    timing_regularity: float  # 0-1, how regular timing is
    quantization_applied: bool
    effects_removed: int

    @classmethod
    def from_extraction_result(cls, result) -> "MIDIQualityMetrics":
        """Build metrics from a MIDIExtractionResult."""
        if not result.notes:
            return cls(
                note_count=0,
                average_confidence=0.0,
                low_confidence_note_count=0,
                high_confidence_note_count=0,
                average_velocity=0.0,
                velocity_variance=0.0,
                note_density=0.0,
                timing_regularity=0.0,
                quantization_applied=False,
                effects_removed=0,
            )

        confidences = [n.confidence for n in result.notes]
        velocities = [n.velocity for n in result.notes]

        # Calculate note density
        if result.notes:
            duration = max(n.end for n in result.notes)
            note_density = len(result.notes) / max(duration, 0.01)
        else:
            note_density = 0.0

        # Calculate timing regularity
        if len(result.notes) > 1:
            onsets = sorted([n.start for n in result.notes])
            iois = np.diff(onsets)
            if len(iois) > 1 and np.mean(iois) > 0:
                cv = np.std(iois) / np.mean(iois)
                timing_regularity = 1.0 - min(1.0, cv)
            else:
                timing_regularity = 0.5
        else:
            timing_regularity = 0.5

        # Check for quantization and effect removal
        from ..midi.passes.base import NoteFlag

        quantization_applied = any(
            NoteFlag.QUANTIZED in n.flags for n in result.notes
        )
        effects_removed = sum(
            1 for n in result.notes
            if NoteFlag.DELAY_REMOVED in n.flags or NoteFlag.REVERB_REMOVED in n.flags
        )

        return cls(
            note_count=len(result.notes),
            average_confidence=float(np.mean(confidences)),
            low_confidence_note_count=sum(1 for c in confidences if c < 0.5),
            high_confidence_note_count=sum(1 for c in confidences if c >= 0.8),
            average_velocity=float(np.mean(velocities)),
            velocity_variance=float(np.var(velocities)),
            note_density=float(note_density),
            timing_regularity=float(timing_regularity),
            quantization_applied=quantization_applied,
            effects_removed=effects_removed,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "note_count": self.note_count,
            "average_confidence": self.average_confidence,
            "low_confidence_note_count": self.low_confidence_note_count,
            "high_confidence_note_count": self.high_confidence_note_count,
            "average_velocity": self.average_velocity,
            "velocity_variance": self.velocity_variance,
            "note_density": self.note_density,
            "timing_regularity": self.timing_regularity,
            "quantization_applied": self.quantization_applied,
            "effects_removed": self.effects_removed,
        }


@dataclass
class UnifiedQualityReport:
    """Comprehensive quality report combining all analysis."""

    stem_type: str
    overall_quality: QualityLevel
    overall_confidence: float
    should_proceed: bool
    warnings: List[QualityWarning] = field(default_factory=list)

    # Component scores
    stem_quality_score: float = 0.0
    midi_quality_score: float = 0.0
    confidence_map_score: float = 0.0

    # Detailed reports
    gate_report: Optional[QualityReport] = None
    midi_metrics: Optional[MIDIQualityMetrics] = None

    # Time-based issues
    problematic_regions: List[Tuple[float, float, str]] = field(default_factory=list)

    # Summary stats
    total_warnings: int = 0
    critical_warnings: int = 0
    error_warnings: int = 0

    def __post_init__(self):
        """Compute summary stats."""
        self.total_warnings = len(self.warnings)
        self.critical_warnings = sum(1 for w in self.warnings if w.is_critical)
        self.error_warnings = sum(1 for w in self.warnings if w.is_error)

    @property
    def has_critical_issues(self) -> bool:
        """Whether there are critical issues."""
        return self.critical_warnings > 0

    @property
    def has_errors(self) -> bool:
        """Whether there are error-level issues."""
        return self.error_warnings > 0

    @property
    def warnings_by_category(self) -> Dict[str, List[QualityWarning]]:
        """Get warnings grouped by category."""
        result: Dict[str, List[QualityWarning]] = {}
        for warning in self.warnings:
            cat = warning.category.value
            if cat not in result:
                result[cat] = []
            result[cat].append(warning)
        return result

    def get_user_summary(self) -> str:
        """Get a user-friendly summary string."""
        lines = [
            f"Quality Assessment: {self.overall_quality.value.upper()}",
            f"Confidence: {self.overall_confidence:.0%}",
        ]

        if not self.should_proceed:
            lines.append("⚠️ Quality below threshold - results may be unreliable")

        if self.critical_warnings > 0:
            lines.append(f"❌ {self.critical_warnings} critical issue(s)")

        if self.error_warnings > 0:
            lines.append(f"⚠️ {self.error_warnings} error(s)")

        # Top warnings
        important_warnings = [w for w in self.warnings if w.is_error][:3]
        if important_warnings:
            lines.append("\nKey Issues:")
            for w in important_warnings:
                lines.append(f"  • {w.message}")

        # Recommendations
        recs = list(set(w.recommendation for w in self.warnings if w.recommendation))[:3]
        if recs:
            lines.append("\nRecommendations:")
            for rec in recs:
                lines.append(f"  • {rec}")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "stem_type": self.stem_type,
            "overall_quality": self.overall_quality.value,
            "overall_confidence": self.overall_confidence,
            "should_proceed": self.should_proceed,
            "stem_quality_score": self.stem_quality_score,
            "midi_quality_score": self.midi_quality_score,
            "confidence_map_score": self.confidence_map_score,
            "total_warnings": self.total_warnings,
            "critical_warnings": self.critical_warnings,
            "error_warnings": self.error_warnings,
            "warnings": [w.to_dict() for w in self.warnings],
            "problematic_regions": [
                {"start": s, "end": e, "issue": i}
                for s, e, i in self.problematic_regions
            ],
            "midi_metrics": self.midi_metrics.to_dict() if self.midi_metrics else None,
        }


class QualityReporter:
    """Unified quality reporter for the reconstruction pipeline.

    Combines stem quality, MIDI extraction results, and confidence
    analysis into comprehensive quality reports with actionable
    warnings for users.
    """

    def __init__(
        self,
        thresholds: Optional[QualityThresholds] = None,
        min_proceed_confidence: float = 0.3,
    ):
        """Initialize the quality reporter.

        Args:
            thresholds: Quality thresholds (uses default if None)
            min_proceed_confidence: Minimum confidence to proceed
        """
        self.gates = QualityGates(thresholds)
        self.thresholds = thresholds or QualityThresholds()
        self.min_proceed_confidence = min_proceed_confidence

    def generate_report(
        self,
        stem_type: str,
        stem_quality: Optional[StemQuality] = None,
        contamination: Optional[ContaminationAnalysis] = None,
        artifacts: Optional[ArtifactAnalysis] = None,
        confidence_map: Optional[ConfidenceMap] = None,
        midi_result=None,  # MIDIExtractionResult
        genre: Optional[str] = None,
    ) -> UnifiedQualityReport:
        """Generate a comprehensive quality report.

        Args:
            stem_type: Type of stem being analyzed
            stem_quality: Stem quality analysis
            contamination: Contamination analysis
            artifacts: Artifact analysis
            confidence_map: Confidence map
            midi_result: MIDI extraction result
            genre: Detected genre (for threshold adjustment)

        Returns:
            UnifiedQualityReport with all findings
        """
        warnings: List[QualityWarning] = []
        problematic_regions: List[Tuple[float, float, str]] = []

        # Adjust thresholds for genre if provided
        if genre:
            self.thresholds = QualityThresholds.for_genre(genre)
            self.gates = QualityGates(self.thresholds)

        # Run gate evaluation
        gate_report = self.gates.evaluate(
            stem_type=stem_type,
            stem_quality=stem_quality,
            contamination=contamination,
            artifacts=artifacts,
            confidence_map=confidence_map,
        )

        # Convert gate warnings to QualityWarnings
        warnings.extend(self._convert_gate_warnings(gate_report))

        # Add stem quality warnings
        if stem_quality:
            warnings.extend(self._analyze_stem_quality(stem_quality))

        # Add contamination warnings
        if contamination:
            w, regions = self._analyze_contamination(contamination)
            warnings.extend(w)
            problematic_regions.extend(regions)

        # Add artifact warnings
        if artifacts:
            w, regions = self._analyze_artifacts(artifacts)
            warnings.extend(w)
            problematic_regions.extend(regions)

        # Add confidence map warnings
        if confidence_map:
            w, regions = self._analyze_confidence_map(confidence_map)
            warnings.extend(w)
            problematic_regions.extend(regions)

        # Add MIDI extraction warnings
        midi_metrics = None
        if midi_result:
            midi_metrics = MIDIQualityMetrics.from_extraction_result(midi_result)
            warnings.extend(self._analyze_midi_result(midi_result, midi_metrics))

        # Calculate component scores
        stem_quality_score = stem_quality.overall_quality if stem_quality else 0.5
        midi_quality_score = midi_metrics.average_confidence if midi_metrics else 0.5
        confidence_map_score = confidence_map.global_confidence if confidence_map else 0.5

        # Calculate overall confidence
        overall_confidence = (
            stem_quality_score * 0.35 +
            midi_quality_score * 0.35 +
            confidence_map_score * 0.30
        )

        # Determine overall quality level
        overall_quality = self._classify_quality(overall_confidence)

        # Determine if should proceed
        should_proceed = (
            overall_confidence >= self.min_proceed_confidence and
            gate_report.passed
        )

        # Sort warnings by severity
        warnings.sort(key=lambda w: (
            0 if w.level == WarningLevel.CRITICAL else
            1 if w.level == WarningLevel.ERROR else
            2 if w.level == WarningLevel.WARNING else 3
        ))

        return UnifiedQualityReport(
            stem_type=stem_type,
            overall_quality=overall_quality,
            overall_confidence=overall_confidence,
            should_proceed=should_proceed,
            warnings=warnings,
            stem_quality_score=stem_quality_score,
            midi_quality_score=midi_quality_score,
            confidence_map_score=confidence_map_score,
            gate_report=gate_report,
            midi_metrics=midi_metrics,
            problematic_regions=problematic_regions,
        )

    def _convert_gate_warnings(self, report: QualityReport) -> List[QualityWarning]:
        """Convert gate report to quality warnings."""
        warnings = []

        # Failed gates become errors
        for gate in report.failed_gates:
            warnings.append(QualityWarning(
                level=WarningLevel.ERROR,
                category=self._gate_to_category(gate.gate_name),
                message=f"{gate.gate_name.replace('_', ' ').title()} check failed",
                details=gate.message,
                recommendation=self._get_gate_recommendation(gate.gate_name),
            ))

        # Warning gates become warnings
        for gate in report.warning_gates:
            warnings.append(QualityWarning(
                level=WarningLevel.WARNING,
                category=self._gate_to_category(gate.gate_name),
                message=f"{gate.gate_name.replace('_', ' ').title()} below recommended level",
                details=gate.message,
            ))

        # Add recommendations from gate report
        for rec in report.recommendations:
            warnings.append(QualityWarning(
                level=WarningLevel.INFO,
                category=WarningCategory.GENERAL,
                message=rec,
            ))

        return warnings

    def _analyze_stem_quality(self, quality: StemQuality) -> List[QualityWarning]:
        """Analyze stem quality for warnings."""
        warnings = []

        if quality.contamination_score > 0.5:
            warnings.append(QualityWarning(
                level=WarningLevel.WARNING,
                category=WarningCategory.CONTAMINATION,
                message="High contamination detected in stem",
                details=f"Contamination score: {quality.contamination_score:.0%}",
                recommendation="Consider using a different separation model",
            ))

        if quality.transient_integrity < 0.3:
            warnings.append(QualityWarning(
                level=WarningLevel.WARNING,
                category=WarningCategory.STEM_QUALITY,
                message="Transient attacks are degraded",
                details=f"Transient integrity: {quality.transient_integrity:.0%}",
                recommendation="MIDI timing may be less accurate - consider manual adjustment",
            ))

        if quality.reverb_density > 0.8:
            warnings.append(QualityWarning(
                level=WarningLevel.INFO,
                category=WarningCategory.STEM_QUALITY,
                message="High reverb content detected",
                details=f"Reverb density: {quality.reverb_density:.0%}",
                recommendation="Note boundaries may extend into reverb tails",
            ))

        if quality.snr_estimate < 10:
            warnings.append(QualityWarning(
                level=WarningLevel.WARNING,
                category=WarningCategory.STEM_QUALITY,
                message="Low signal-to-noise ratio",
                details=f"Estimated SNR: {quality.snr_estimate:.1f} dB",
                recommendation="Some quiet notes may be missed",
            ))

        return warnings

    def _analyze_contamination(
        self,
        contamination: ContaminationAnalysis,
    ) -> Tuple[List[QualityWarning], List[Tuple[float, float, str]]]:
        """Analyze contamination for warnings and problem regions."""
        warnings = []
        regions = []

        if contamination.overall_contamination > 0.6:
            warnings.append(QualityWarning(
                level=WarningLevel.ERROR,
                category=WarningCategory.CONTAMINATION,
                message="Severe contamination detected",
                details=f"Overall contamination: {contamination.overall_contamination:.0%}",
                recommendation="Stem separation quality is poor - results may be unreliable",
            ))

        # Find worst contamination events
        severe_events = [e for e in contamination.events if e.severity > 0.6]
        for event in severe_events[:5]:
            warnings.append(QualityWarning(
                level=WarningLevel.WARNING,
                category=WarningCategory.CONTAMINATION,
                message=f"{event.contamination_type.value.replace('_', ' ').title()} detected",
                details=event.description,
                time_range=(event.time_start, event.time_end),
            ))
            regions.append((event.time_start, event.time_end, event.contamination_type.value))

        return warnings, regions

    def _analyze_artifacts(
        self,
        artifacts: ArtifactAnalysis,
    ) -> Tuple[List[QualityWarning], List[Tuple[float, float, str]]]:
        """Analyze artifacts for warnings and problem regions."""
        warnings = []
        regions = []

        if artifacts.overall_artifact_score > 0.5:
            warnings.append(QualityWarning(
                level=WarningLevel.WARNING,
                category=WarningCategory.ARTIFACT,
                message="Significant artifacts detected",
                details=f"Artifact score: {artifacts.overall_artifact_score:.0%}",
            ))

        # Find worst artifacts
        severe_artifacts = [a for a in artifacts.artifacts if a.severity > 0.5]
        for artifact in severe_artifacts[:3]:
            warnings.append(QualityWarning(
                level=WarningLevel.INFO,
                category=WarningCategory.ARTIFACT,
                message=f"{artifact.artifact_type.value.replace('_', ' ').title()}",
                details=artifact.description,
                time_range=(artifact.time_start, artifact.time_end),
            ))
            regions.append((artifact.time_start, artifact.time_end, artifact.artifact_type.value))

        return warnings, regions

    def _analyze_confidence_map(
        self,
        conf_map: ConfidenceMap,
    ) -> Tuple[List[QualityWarning], List[Tuple[float, float, str]]]:
        """Analyze confidence map for warnings and problem regions."""
        warnings = []
        regions = []

        if conf_map.global_confidence < 0.4:
            warnings.append(QualityWarning(
                level=WarningLevel.WARNING,
                category=WarningCategory.CONFIDENCE,
                message="Low overall extraction confidence",
                details=f"Global confidence: {conf_map.global_confidence:.0%}",
                recommendation="Manual review recommended",
            ))

        # Check low confidence regions
        low_conf_duration = sum(
            end - start for start, end in conf_map.low_confidence_regions
        )
        low_conf_ratio = low_conf_duration / max(conf_map.duration, 0.01)

        if low_conf_ratio > 0.3:
            warnings.append(QualityWarning(
                level=WarningLevel.WARNING,
                category=WarningCategory.CONFIDENCE,
                message=f"{low_conf_ratio:.0%} of audio has low confidence",
                details=f"{len(conf_map.low_confidence_regions)} low confidence regions",
            ))

        # Flag specific regions
        for start, end in conf_map.low_confidence_regions[:5]:
            regions.append((start, end, "low_confidence"))

        return warnings, regions

    def _analyze_midi_result(
        self,
        result,
        metrics: MIDIQualityMetrics,
    ) -> List[QualityWarning]:
        """Analyze MIDI extraction result for warnings."""
        warnings = []

        if metrics.note_count == 0:
            warnings.append(QualityWarning(
                level=WarningLevel.CRITICAL,
                category=WarningCategory.MIDI_EXTRACTION,
                message="No MIDI notes extracted",
                recommendation="Check audio quality or try different extraction settings",
            ))
            return warnings

        if metrics.note_count < 5:
            warnings.append(QualityWarning(
                level=WarningLevel.WARNING,
                category=WarningCategory.MIDI_EXTRACTION,
                message=f"Only {metrics.note_count} notes extracted",
                recommendation="May need harmonic recovery pass or lower thresholds",
            ))

        if metrics.average_confidence < 0.5:
            warnings.append(QualityWarning(
                level=WarningLevel.WARNING,
                category=WarningCategory.MIDI_EXTRACTION,
                message="Low average note confidence",
                details=f"Average confidence: {metrics.average_confidence:.0%}",
            ))

        low_conf_ratio = metrics.low_confidence_note_count / max(metrics.note_count, 1)
        if low_conf_ratio > 0.4:
            warnings.append(QualityWarning(
                level=WarningLevel.WARNING,
                category=WarningCategory.MIDI_EXTRACTION,
                message=f"{low_conf_ratio:.0%} of notes have low confidence",
                details=f"{metrics.low_confidence_note_count} notes below 50% confidence",
            ))

        if metrics.effects_removed > metrics.note_count * 0.3:
            warnings.append(QualityWarning(
                level=WarningLevel.INFO,
                category=WarningCategory.MIDI_EXTRACTION,
                message=f"{metrics.effects_removed} effect artifacts removed",
                details="Heavy delay/reverb processing detected",
            ))

        # Check for pass-specific warnings
        for pass_result in result.pass_results:
            for warning in pass_result.warnings:
                warnings.append(QualityWarning(
                    level=WarningLevel.INFO,
                    category=WarningCategory.MIDI_EXTRACTION,
                    message=warning,
                ))

        return warnings

    def _gate_to_category(self, gate_name: str) -> WarningCategory:
        """Map gate name to warning category."""
        if "contamination" in gate_name:
            return WarningCategory.CONTAMINATION
        elif "artifact" in gate_name:
            return WarningCategory.ARTIFACT
        elif "confidence" in gate_name:
            return WarningCategory.CONFIDENCE
        elif "transient" in gate_name or "reverb" in gate_name:
            return WarningCategory.STEM_QUALITY
        else:
            return WarningCategory.GENERAL

    def _get_gate_recommendation(self, gate_name: str) -> str:
        """Get recommendation for a failed gate."""
        recommendations = {
            "stem_quality": "Try a different stem separation model or higher quality source",
            "transient_integrity": "MIDI timing may be inaccurate - manual adjustment recommended",
            "contamination": "High bleed from other instruments detected",
            "reverb_density": "Consider using dry/wet separation if available",
            "artifacts": "Audio processing artifacts detected - may affect extraction",
            "global_confidence": "Overall extraction confidence is low",
            "low_confidence_ratio": "Many regions have uncertain extraction",
        }
        return recommendations.get(gate_name, "Review extraction settings")

    def _classify_quality(self, confidence: float) -> QualityLevel:
        """Classify overall quality level."""
        if confidence >= 0.85:
            return QualityLevel.EXCELLENT
        elif confidence >= 0.7:
            return QualityLevel.GOOD
        elif confidence >= 0.55:
            return QualityLevel.ACCEPTABLE
        elif confidence >= 0.4:
            return QualityLevel.MARGINAL
        elif confidence >= 0.25:
            return QualityLevel.POOR
        else:
            return QualityLevel.UNACCEPTABLE


# Module-level singleton
_reporter: Optional[QualityReporter] = None


def get_quality_reporter(
    thresholds: Optional[QualityThresholds] = None,
) -> QualityReporter:
    """Get the global quality reporter instance.

    Args:
        thresholds: Optional custom thresholds

    Returns:
        QualityReporter instance
    """
    global _reporter
    if _reporter is None or thresholds is not None:
        _reporter = QualityReporter(thresholds=thresholds)
    return _reporter


def generate_quality_report(
    stem_type: str,
    stem_quality: Optional[StemQuality] = None,
    contamination: Optional[ContaminationAnalysis] = None,
    artifacts: Optional[ArtifactAnalysis] = None,
    confidence_map: Optional[ConfidenceMap] = None,
    midi_result=None,
    genre: Optional[str] = None,
) -> UnifiedQualityReport:
    """Convenience function to generate a quality report.

    Args:
        stem_type: Type of stem
        stem_quality: Stem quality analysis
        contamination: Contamination analysis
        artifacts: Artifact analysis
        confidence_map: Confidence map
        midi_result: MIDI extraction result
        genre: Detected genre

    Returns:
        UnifiedQualityReport
    """
    reporter = get_quality_reporter()
    return reporter.generate_report(
        stem_type=stem_type,
        stem_quality=stem_quality,
        contamination=contamination,
        artifacts=artifacts,
        confidence_map=confidence_map,
        midi_result=midi_result,
        genre=genre,
    )
