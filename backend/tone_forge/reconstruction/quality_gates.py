"""Quality gates for reconstruction pipeline.

Provides configurable quality thresholds that gate decisions
based on confidence and quality metrics.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

from .stem_quality import StemQuality
from .contamination import ContaminationAnalysis
from .artifact_detection import ArtifactAnalysis
from .confidence_map import ConfidenceMap

logger = logging.getLogger(__name__)


class QualityLevel(str, Enum):
    """Quality level classification."""

    EXCELLENT = "excellent"
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    MARGINAL = "marginal"
    POOR = "poor"
    UNACCEPTABLE = "unacceptable"


class GateStatus(str, Enum):
    """Status of a quality gate check."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


@dataclass
class QualityThresholds:
    """Configurable quality thresholds.

    All thresholds are 0-1 unless otherwise noted.
    """

    # Stem quality thresholds
    min_stem_quality: float = 0.4
    min_transient_integrity: float = 0.3
    max_contamination: float = 0.6
    max_reverb_density: float = 0.8

    # MIDI extraction thresholds
    min_note_confidence: float = 0.3
    min_timing_confidence: float = 0.4

    # Descriptor thresholds
    min_descriptor_confidence: float = 0.5

    # Region thresholds
    min_region_confidence: float = 0.35
    max_low_confidence_ratio: float = 0.4  # Max ratio of low-conf regions

    # Artifact thresholds
    max_artifact_score: float = 0.5

    # Warning thresholds (less strict)
    warn_stem_quality: float = 0.55
    warn_contamination: float = 0.4
    warn_artifact_score: float = 0.3

    @classmethod
    def strict(cls) -> "QualityThresholds":
        """Get strict thresholds for high-quality output."""
        return cls(
            min_stem_quality=0.6,
            min_transient_integrity=0.5,
            max_contamination=0.4,
            max_reverb_density=0.6,
            min_note_confidence=0.5,
            min_timing_confidence=0.6,
            min_descriptor_confidence=0.6,
            min_region_confidence=0.5,
            max_low_confidence_ratio=0.2,
            max_artifact_score=0.3,
            warn_stem_quality=0.7,
            warn_contamination=0.25,
            warn_artifact_score=0.2,
        )

    @classmethod
    def lenient(cls) -> "QualityThresholds":
        """Get lenient thresholds for more permissive output."""
        return cls(
            min_stem_quality=0.25,
            min_transient_integrity=0.2,
            max_contamination=0.75,
            max_reverb_density=0.9,
            min_note_confidence=0.2,
            min_timing_confidence=0.25,
            min_descriptor_confidence=0.35,
            min_region_confidence=0.25,
            max_low_confidence_ratio=0.6,
            max_artifact_score=0.7,
            warn_stem_quality=0.4,
            warn_contamination=0.5,
            warn_artifact_score=0.4,
        )

    @classmethod
    def for_genre(cls, genre: str) -> "QualityThresholds":
        """Get genre-appropriate thresholds."""
        # Genres with high reverb need looser reverb thresholds
        high_reverb_genres = {"ambient", "shoegaze", "dream_pop", "synthwave", "post_rock"}

        # Genres with clean production
        clean_genres = {"pop", "edm", "hip_hop", "country"}

        # Genres with dense/layered production
        dense_genres = {"metal", "progressive", "orchestral"}

        if genre.lower() in high_reverb_genres:
            return cls(
                max_reverb_density=0.95,
                max_contamination=0.65,
                min_transient_integrity=0.2,  # Soft attacks expected
                min_stem_quality=0.35,
            )
        elif genre.lower() in clean_genres:
            return cls.strict()
        elif genre.lower() in dense_genres:
            return cls(
                max_contamination=0.7,  # More bleed expected
                min_stem_quality=0.3,
            )
        else:
            return cls()  # Default


@dataclass
class GateResult:
    """Result of a single gate check."""

    gate_name: str
    status: GateStatus
    actual_value: float
    threshold: float
    message: str = ""

    @property
    def passed(self) -> bool:
        """Whether the gate passed."""
        return self.status == GateStatus.PASSED

    @property
    def failed(self) -> bool:
        """Whether the gate failed."""
        return self.status == GateStatus.FAILED


@dataclass
class QualityReport:
    """Complete quality report from gate evaluation."""

    stem_type: str
    overall_status: GateStatus
    overall_quality: QualityLevel
    gate_results: List[GateResult] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    # Summary scores
    stem_quality_score: float = 0.0
    midi_confidence_score: float = 0.0
    descriptor_confidence_score: float = 0.0
    overall_confidence: float = 0.0

    @property
    def passed(self) -> bool:
        """Whether all required gates passed."""
        return self.overall_status != GateStatus.FAILED

    @property
    def has_warnings(self) -> bool:
        """Whether any warnings were raised."""
        return len(self.warnings) > 0 or self.overall_status == GateStatus.WARNING

    @property
    def failed_gates(self) -> List[GateResult]:
        """Get list of failed gates."""
        return [g for g in self.gate_results if g.failed]

    @property
    def warning_gates(self) -> List[GateResult]:
        """Get list of warning gates."""
        return [g for g in self.gate_results if g.status == GateStatus.WARNING]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "stem_type": self.stem_type,
            "overall_status": self.overall_status.value,
            "overall_quality": self.overall_quality.value,
            "passed": self.passed,
            "has_warnings": self.has_warnings,
            "stem_quality_score": self.stem_quality_score,
            "midi_confidence_score": self.midi_confidence_score,
            "descriptor_confidence_score": self.descriptor_confidence_score,
            "overall_confidence": self.overall_confidence,
            "gate_results": [
                {
                    "gate_name": g.gate_name,
                    "status": g.status.value,
                    "actual_value": g.actual_value,
                    "threshold": g.threshold,
                    "message": g.message,
                }
                for g in self.gate_results
            ],
            "recommendations": self.recommendations,
            "warnings": self.warnings,
            "errors": self.errors,
        }


class QualityGates:
    """Quality gate system for reconstruction pipeline.

    Evaluates quality metrics against configurable thresholds
    and provides actionable feedback.
    """

    def __init__(
        self,
        thresholds: Optional[QualityThresholds] = None,
    ):
        """Initialize quality gates.

        Args:
            thresholds: Quality thresholds to use (default if None)
        """
        self.thresholds = thresholds or QualityThresholds()

    def evaluate(
        self,
        stem_type: str,
        stem_quality: Optional[StemQuality] = None,
        contamination: Optional[ContaminationAnalysis] = None,
        artifacts: Optional[ArtifactAnalysis] = None,
        confidence_map: Optional[ConfidenceMap] = None,
    ) -> QualityReport:
        """Evaluate quality against gates.

        Args:
            stem_type: Type of stem being evaluated
            stem_quality: Optional stem quality analysis
            contamination: Optional contamination analysis
            artifacts: Optional artifact analysis
            confidence_map: Optional confidence map

        Returns:
            QualityReport with gate results and recommendations
        """
        gate_results: List[GateResult] = []
        recommendations: List[str] = []
        warnings: List[str] = []
        errors: List[str] = []

        # Track scores for summary
        stem_quality_score = 0.5
        midi_confidence_score = 0.5
        descriptor_confidence_score = 0.5

        # Evaluate stem quality gates
        if stem_quality:
            stem_quality_score = stem_quality.overall_quality

            # Overall quality gate
            gate_results.append(self._check_gate(
                "stem_quality",
                stem_quality.overall_quality,
                self.thresholds.min_stem_quality,
                self.thresholds.warn_stem_quality,
                higher_is_better=True,
            ))

            # Transient integrity gate
            gate_results.append(self._check_gate(
                "transient_integrity",
                stem_quality.transient_integrity,
                self.thresholds.min_transient_integrity,
                higher_is_better=True,
            ))

            # Contamination gate
            gate_results.append(self._check_gate(
                "contamination",
                stem_quality.contamination_score,
                self.thresholds.max_contamination,
                self.thresholds.warn_contamination,
                higher_is_better=False,
            ))

            # Reverb density gate
            gate_results.append(self._check_gate(
                "reverb_density",
                stem_quality.reverb_density,
                self.thresholds.max_reverb_density,
                higher_is_better=False,
            ))

            # Add recommendations based on issues
            for issue in stem_quality.issues:
                if "contamination" in issue.lower():
                    recommendations.append(
                        "Consider using a different separation model or adjusting separation parameters"
                    )
                elif "transient" in issue.lower():
                    recommendations.append(
                        "Transient preservation is low - MIDI timing may be less accurate"
                    )
                elif "reverb" in issue.lower():
                    recommendations.append(
                        "High reverb density detected - consider using dry/wet separation"
                    )

        # Evaluate contamination gates
        if contamination:
            gate_results.append(self._check_gate(
                "contamination_events",
                contamination.overall_contamination,
                self.thresholds.max_contamination,
                self.thresholds.warn_contamination,
                higher_is_better=False,
            ))

            # Add specific warnings for contamination types
            for event in contamination.events[:5]:  # Top 5 events
                if event.severity > 0.5:
                    warnings.append(
                        f"{event.contamination_type.value}: {event.description} "
                        f"({event.time_start:.2f}s - {event.time_end:.2f}s)"
                    )

        # Evaluate artifact gates
        if artifacts:
            gate_results.append(self._check_gate(
                "artifacts",
                artifacts.overall_artifact_score,
                self.thresholds.max_artifact_score,
                self.thresholds.warn_artifact_score,
                higher_is_better=False,
            ))

            # Specific artifact warnings
            for artifact in artifacts.artifacts[:5]:
                if artifact.severity > 0.5:
                    warnings.append(
                        f"{artifact.artifact_type.value}: {artifact.description}"
                    )

        # Evaluate confidence map gates
        if confidence_map:
            midi_confidence_score = confidence_map.global_confidence
            descriptor_confidence_score = confidence_map.global_confidence

            # Global confidence gate
            gate_results.append(self._check_gate(
                "global_confidence",
                confidence_map.global_confidence,
                self.thresholds.min_region_confidence,
                higher_is_better=True,
            ))

            # Low confidence ratio gate
            total_duration = confidence_map.duration
            low_conf_duration = sum(
                end - start for start, end in confidence_map.low_confidence_regions
            )
            low_conf_ratio = low_conf_duration / total_duration if total_duration > 0 else 0

            gate_results.append(self._check_gate(
                "low_confidence_ratio",
                low_conf_ratio,
                self.thresholds.max_low_confidence_ratio,
                higher_is_better=False,
            ))

            # Flag specific low confidence regions
            for start, end in confidence_map.low_confidence_regions[:3]:
                warnings.append(
                    f"Low confidence region: {start:.2f}s - {end:.2f}s"
                )

        # Determine overall status
        failed_count = sum(1 for g in gate_results if g.failed)
        warning_count = sum(1 for g in gate_results if g.status == GateStatus.WARNING)

        if failed_count > 0:
            overall_status = GateStatus.FAILED
            errors.append(f"{failed_count} quality gate(s) failed")
        elif warning_count > 0:
            overall_status = GateStatus.WARNING
        else:
            overall_status = GateStatus.PASSED

        # Determine quality level
        overall_confidence = (
            stem_quality_score * 0.4 +
            midi_confidence_score * 0.3 +
            descriptor_confidence_score * 0.3
        )
        overall_quality = self._classify_quality(overall_confidence)

        return QualityReport(
            stem_type=stem_type,
            overall_status=overall_status,
            overall_quality=overall_quality,
            gate_results=gate_results,
            recommendations=list(set(recommendations)),  # Deduplicate
            warnings=warnings,
            errors=errors,
            stem_quality_score=stem_quality_score,
            midi_confidence_score=midi_confidence_score,
            descriptor_confidence_score=descriptor_confidence_score,
            overall_confidence=overall_confidence,
        )

    def stem_quality_sufficient(
        self,
        stem_quality: StemQuality,
    ) -> bool:
        """Quick check if stem quality is sufficient for extraction.

        Args:
            stem_quality: Stem quality analysis

        Returns:
            True if quality is sufficient
        """
        return (
            stem_quality.overall_quality >= self.thresholds.min_stem_quality and
            stem_quality.contamination_score <= self.thresholds.max_contamination
        )

    def should_proceed(
        self,
        report: QualityReport,
    ) -> bool:
        """Whether quality is sufficient to proceed with extraction.

        Args:
            report: Quality report

        Returns:
            True if extraction should proceed
        """
        return report.passed

    def should_warn_user(
        self,
        report: QualityReport,
    ) -> bool:
        """Whether user should be warned about quality issues.

        Args:
            report: Quality report

        Returns:
            True if user should be warned
        """
        return report.has_warnings or not report.passed

    def get_quality_summary(
        self,
        report: QualityReport,
    ) -> str:
        """Get human-readable quality summary.

        Args:
            report: Quality report

        Returns:
            Summary string
        """
        lines = [
            f"Quality Assessment for {report.stem_type}:",
            f"  Overall: {report.overall_quality.value} ({report.overall_confidence:.0%})",
            f"  Status: {report.overall_status.value}",
        ]

        if report.failed_gates:
            lines.append("  Failed gates:")
            for gate in report.failed_gates:
                lines.append(f"    - {gate.gate_name}: {gate.message}")

        if report.warnings:
            lines.append("  Warnings:")
            for warning in report.warnings[:5]:
                lines.append(f"    - {warning}")

        if report.recommendations:
            lines.append("  Recommendations:")
            for rec in report.recommendations[:3]:
                lines.append(f"    - {rec}")

        return "\n".join(lines)

    def _check_gate(
        self,
        gate_name: str,
        value: float,
        fail_threshold: float,
        warn_threshold: Optional[float] = None,
        higher_is_better: bool = True,
    ) -> GateResult:
        """Check a single gate.

        Args:
            gate_name: Name of the gate
            value: Actual value
            fail_threshold: Threshold for failure
            warn_threshold: Threshold for warning (optional)
            higher_is_better: Whether higher values are better

        Returns:
            GateResult
        """
        if higher_is_better:
            failed = value < fail_threshold
            warning = warn_threshold and value < warn_threshold and not failed
        else:
            failed = value > fail_threshold
            warning = warn_threshold and value > warn_threshold and not failed

        if failed:
            status = GateStatus.FAILED
            if higher_is_better:
                message = f"{value:.2f} < {fail_threshold:.2f} (minimum)"
            else:
                message = f"{value:.2f} > {fail_threshold:.2f} (maximum)"
        elif warning:
            status = GateStatus.WARNING
            if higher_is_better:
                message = f"{value:.2f} below recommended {warn_threshold:.2f}"
            else:
                message = f"{value:.2f} above recommended {warn_threshold:.2f}"
        else:
            status = GateStatus.PASSED
            message = f"{value:.2f} within acceptable range"

        return GateResult(
            gate_name=gate_name,
            status=status,
            actual_value=value,
            threshold=fail_threshold,
            message=message,
        )

    def _classify_quality(
        self,
        confidence: float,
    ) -> QualityLevel:
        """Classify overall quality level.

        Args:
            confidence: Overall confidence score

        Returns:
            QualityLevel
        """
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
_gates: Optional[QualityGates] = None


def get_quality_gates(
    thresholds: Optional[QualityThresholds] = None,
) -> QualityGates:
    """Get the global quality gates instance.

    Args:
        thresholds: Optional custom thresholds

    Returns:
        QualityGates instance
    """
    global _gates
    if _gates is None or thresholds is not None:
        _gates = QualityGates(thresholds=thresholds)
    return _gates
