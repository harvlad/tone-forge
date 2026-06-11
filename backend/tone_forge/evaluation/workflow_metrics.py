"""Workflow-focused evaluation metrics for producer usefulness.

Instead of pure accuracy metrics (F1, precision, recall), these
metrics focus on how useful the extraction is for real production:
- How much cleanup time will this save?
- Is this usable as-is, with minor edits, or needs major work?
- Does the output feel musical and natural?
- Can producers iterate quickly with this starting point?

The goal: measure whether producers get 70% there dramatically faster.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class WorkflowMetrics:
    """Producer-focused metrics for extraction quality.

    These metrics assess usefulness from a workflow perspective,
    not just technical accuracy.
    """

    # Time estimates
    cleanup_time_estimate_min: float = 0.0  # Estimated cleanup time in minutes
    time_saved_estimate_min: float = 0.0  # Time saved vs manual transcription

    # Quality tiers
    retained_usefulness: float = 0.0  # 0-1: % of content usable after cleanup
    editable_quality: str = "unknown"  # "drop-in", "minor-edits", "major-cleanup", "unusable"

    # Iteration speed
    creative_iteration_speed: float = 0.0  # 0-1: how fast can producer iterate

    # Musical feel
    timing_naturalness: float = 0.0  # 0-1: does timing feel natural (not robotic)
    velocity_dynamics: float = 0.0  # 0-1: are dynamics preserved
    phrase_integrity: float = 0.0  # 0-1: are musical phrases intact

    # Artifact levels
    artifact_density: float = 0.0  # 0-1: density of false positives / artifacts
    gap_density: float = 0.0  # 0-1: density of missing notes / false negatives

    # Overall scores
    producer_satisfaction_estimate: float = 0.0  # 0-1: estimated producer satisfaction
    workflow_score: float = 0.0  # 0-1: overall workflow usefulness

    # Detailed breakdown
    issue_summary: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "time_estimates": {
                "cleanup_time_min": self.cleanup_time_estimate_min,
                "time_saved_min": self.time_saved_estimate_min,
            },
            "quality": {
                "retained_usefulness": self.retained_usefulness,
                "editable_quality": self.editable_quality,
                "creative_iteration_speed": self.creative_iteration_speed,
            },
            "musical_feel": {
                "timing_naturalness": self.timing_naturalness,
                "velocity_dynamics": self.velocity_dynamics,
                "phrase_integrity": self.phrase_integrity,
            },
            "artifacts": {
                "artifact_density": self.artifact_density,
                "gap_density": self.gap_density,
            },
            "overall": {
                "producer_satisfaction_estimate": self.producer_satisfaction_estimate,
                "workflow_score": self.workflow_score,
            },
            "issue_summary": self.issue_summary,
        }


@dataclass
class NoteAssessment:
    """Assessment of a single extracted note."""

    pitch: int
    start: float
    end: float
    velocity: int
    confidence: float

    # Assessment flags
    is_artifact: bool = False  # Likely false positive
    is_on_grid: bool = True  # Aligned to beat grid
    velocity_appropriate: bool = True  # Velocity fits context
    timing_natural: bool = True  # Timing feels natural
    pitch_plausible: bool = True  # Pitch makes musical sense

    # Issues
    issues: List[str] = field(default_factory=list)


class WorkflowEvaluator:
    """Evaluates extraction quality from workflow/producer perspective.

    Focuses on practical usefulness rather than pure accuracy metrics.
    """

    def __init__(
        self,
        artifact_confidence_threshold: float = 0.4,
        velocity_variance_tolerance: float = 0.3,
        timing_tolerance_ms: float = 30.0,
        min_velocity_range: int = 20,
    ):
        """Initialize evaluator.

        Args:
            artifact_confidence_threshold: Below this, notes are potential artifacts
            velocity_variance_tolerance: Acceptable velocity variance (0-1)
            timing_tolerance_ms: Timing offset tolerance in ms
            min_velocity_range: Minimum velocity range for good dynamics
        """
        self.artifact_confidence_threshold = artifact_confidence_threshold
        self.velocity_variance_tolerance = velocity_variance_tolerance
        self.timing_tolerance_ms = timing_tolerance_ms
        self.min_velocity_range = min_velocity_range

        # Time estimates (empirical, adjustable)
        self.manual_transcription_time_per_note = 0.5  # seconds per note
        self.cleanup_time_per_artifact = 2.0  # seconds per artifact
        self.cleanup_time_per_gap = 3.0  # seconds per gap to fill

    def evaluate(
        self,
        notes: List[Tuple[int, float, float, int]],
        confidences: Optional[List[float]] = None,
        tempo: float = 120.0,
        duration_sec: float = 0.0,
        reference_notes: Optional[List[Tuple[int, float, float, int]]] = None,
    ) -> WorkflowMetrics:
        """Evaluate extraction from workflow perspective.

        Args:
            notes: List of (pitch, start, end, velocity) tuples
            confidences: Optional confidence scores per note
            tempo: Tempo in BPM
            duration_sec: Total duration in seconds
            reference_notes: Optional ground truth for comparison

        Returns:
            WorkflowMetrics with assessment
        """
        if len(notes) == 0:
            return WorkflowMetrics(
                editable_quality="unusable",
                workflow_score=0.0,
            )

        # Ensure duration
        if duration_sec == 0 and notes:
            duration_sec = max(n[2] for n in notes) + 1.0

        # Assign default confidences
        if confidences is None:
            confidences = [0.7] * len(notes)

        # Assess each note
        assessments = self._assess_notes(notes, confidences, tempo)

        # Calculate metrics
        metrics = WorkflowMetrics()

        # Artifact density
        artifacts = [a for a in assessments if a.is_artifact]
        metrics.artifact_density = len(artifacts) / len(assessments) if assessments else 0

        # Gap density (estimated from note spacing)
        metrics.gap_density = self._estimate_gap_density(notes, duration_sec, tempo)

        # Timing naturalness
        natural_timing = [a for a in assessments if a.timing_natural]
        metrics.timing_naturalness = len(natural_timing) / len(assessments) if assessments else 0

        # Velocity dynamics
        metrics.velocity_dynamics = self._assess_velocity_dynamics(notes)

        # Phrase integrity
        metrics.phrase_integrity = self._assess_phrase_integrity(notes, tempo)

        # Calculate quality tier and retained usefulness
        metrics.editable_quality, metrics.retained_usefulness = self._determine_quality_tier(
            metrics.artifact_density,
            metrics.gap_density,
            metrics.timing_naturalness,
            metrics.velocity_dynamics,
        )

        # Time estimates
        metrics.cleanup_time_estimate_min = self._estimate_cleanup_time(
            len(notes),
            len(artifacts),
            metrics.gap_density * len(notes),
        )

        manual_time = len(notes) * self.manual_transcription_time_per_note / 60.0
        metrics.time_saved_estimate_min = max(0, manual_time - metrics.cleanup_time_estimate_min)

        # Creative iteration speed (inverse of cleanup needed)
        metrics.creative_iteration_speed = max(0, 1 - metrics.cleanup_time_estimate_min / max(manual_time, 1))

        # Overall scores
        metrics.producer_satisfaction_estimate = self._estimate_satisfaction(metrics)
        metrics.workflow_score = self._calculate_workflow_score(metrics)

        # Issue summary
        metrics.issue_summary = self._summarize_issues(assessments)

        return metrics

    def _assess_notes(
        self,
        notes: List[Tuple[int, float, float, int]],
        confidences: List[float],
        tempo: float,
    ) -> List[NoteAssessment]:
        """Assess each note individually."""
        assessments = []

        beat_duration = 60.0 / tempo
        grid_size = beat_duration / 4  # 16th notes

        for i, (pitch, start, end, velocity) in enumerate(notes):
            conf = confidences[i] if i < len(confidences) else 0.7

            assessment = NoteAssessment(
                pitch=pitch,
                start=start,
                end=end,
                velocity=velocity,
                confidence=conf,
            )

            # Check for artifact (low confidence)
            if conf < self.artifact_confidence_threshold:
                assessment.is_artifact = True
                assessment.issues.append("low_confidence")

            # Check grid alignment
            grid_offset = start % grid_size
            on_grid = grid_offset < self.timing_tolerance_ms / 1000 or \
                      grid_offset > grid_size - self.timing_tolerance_ms / 1000
            assessment.is_on_grid = on_grid

            if not on_grid:
                # Off-grid isn't always bad (swing, humanization)
                # But check if it's wildly off
                if grid_offset > grid_size * 0.3 and grid_offset < grid_size * 0.7:
                    assessment.timing_natural = False
                    assessment.issues.append("timing_off_grid")

            # Check pitch plausibility (very low or very high)
            if pitch < 24 or pitch > 108:  # Below C1 or above C8
                assessment.pitch_plausible = False
                assessment.issues.append("pitch_extreme")

            # Check velocity (0 or 127 are suspicious)
            if velocity <= 1:
                assessment.velocity_appropriate = False
                assessment.issues.append("velocity_zero")
            elif velocity >= 127:
                assessment.velocity_appropriate = False
                assessment.issues.append("velocity_max")

            # Check note duration
            duration = end - start
            if duration < 0.01:  # Less than 10ms
                assessment.is_artifact = True
                assessment.issues.append("duration_tiny")
            elif duration > 30.0:  # More than 30 seconds
                assessment.issues.append("duration_extreme")

            assessments.append(assessment)

        return assessments

    def _estimate_gap_density(
        self,
        notes: List[Tuple[int, float, float, int]],
        duration_sec: float,
        tempo: float,
    ) -> float:
        """Estimate density of gaps/missing notes."""
        if len(notes) < 2:
            return 0.5  # Can't tell

        # Sort by start time
        sorted_notes = sorted(notes, key=lambda n: n[1])

        # Calculate inter-onset intervals
        iois = []
        for i in range(1, len(sorted_notes)):
            ioi = sorted_notes[i][1] - sorted_notes[i-1][1]
            iois.append(ioi)

        if not iois:
            return 0.0

        # Large gaps suggest missing notes
        beat_duration = 60.0 / tempo
        large_gaps = sum(1 for ioi in iois if ioi > beat_duration * 2)

        return min(1.0, large_gaps / len(iois))

    def _assess_velocity_dynamics(
        self,
        notes: List[Tuple[int, float, float, int]],
    ) -> float:
        """Assess velocity dynamics quality."""
        if len(notes) < 3:
            return 0.5

        velocities = [n[3] for n in notes]

        # Check range
        vel_range = max(velocities) - min(velocities)
        if vel_range < self.min_velocity_range:
            return 0.3  # Flat dynamics

        # Check variance
        vel_std = np.std(velocities)
        vel_mean = np.mean(velocities)

        if vel_mean == 0:
            return 0.0

        # Coefficient of variation
        cv = vel_std / vel_mean

        # Good dynamics have moderate variance
        if cv < 0.05:
            return 0.3  # Too flat
        elif cv > 0.5:
            return 0.6  # Too erratic
        else:
            return min(1.0, 0.6 + cv * 0.8)  # Good range

    def _assess_phrase_integrity(
        self,
        notes: List[Tuple[int, float, float, int]],
        tempo: float,
    ) -> float:
        """Assess whether musical phrases are intact."""
        if len(notes) < 4:
            return 0.5

        sorted_notes = sorted(notes, key=lambda n: n[1])

        # Look for phrase-like groupings
        beat_duration = 60.0 / tempo
        phrase_boundary = beat_duration * 4  # 1 bar

        # Count notes per phrase
        phrases = []
        current_phrase = []

        for i, note in enumerate(sorted_notes):
            if i == 0:
                current_phrase.append(note)
            else:
                gap = note[1] - sorted_notes[i-1][2]
                if gap > phrase_boundary:
                    phrases.append(current_phrase)
                    current_phrase = [note]
                else:
                    current_phrase.append(note)

        if current_phrase:
            phrases.append(current_phrase)

        if not phrases:
            return 0.5

        # Good phrase integrity: phrases have 4+ notes and consistent sizes
        valid_phrases = [p for p in phrases if len(p) >= 4]
        phrase_sizes = [len(p) for p in phrases]

        if not valid_phrases:
            return 0.3

        # Consistency of phrase sizes
        size_std = np.std(phrase_sizes)
        size_mean = np.mean(phrase_sizes)

        if size_mean == 0:
            return 0.5

        consistency = 1.0 - min(1.0, size_std / size_mean)
        valid_ratio = len(valid_phrases) / len(phrases)

        return 0.5 * consistency + 0.5 * valid_ratio

    def _determine_quality_tier(
        self,
        artifact_density: float,
        gap_density: float,
        timing_naturalness: float,
        velocity_dynamics: float,
    ) -> Tuple[str, float]:
        """Determine quality tier and retained usefulness."""
        # Weighted quality score
        quality_score = (
            (1 - artifact_density) * 0.35 +
            (1 - gap_density) * 0.25 +
            timing_naturalness * 0.25 +
            velocity_dynamics * 0.15
        )

        # Determine tier
        if quality_score >= 0.85:
            return "drop-in", quality_score
        elif quality_score >= 0.65:
            return "minor-edits", quality_score
        elif quality_score >= 0.40:
            return "major-cleanup", quality_score
        else:
            return "unusable", quality_score

    def _estimate_cleanup_time(
        self,
        note_count: int,
        artifact_count: int,
        estimated_gaps: float,
    ) -> float:
        """Estimate cleanup time in minutes."""
        # Time to remove artifacts
        artifact_time = artifact_count * self.cleanup_time_per_artifact

        # Time to fill gaps
        gap_time = estimated_gaps * self.cleanup_time_per_gap

        # Total in minutes
        return (artifact_time + gap_time) / 60.0

    def _estimate_satisfaction(self, metrics: WorkflowMetrics) -> float:
        """Estimate producer satisfaction 0-1."""
        # Satisfaction factors (weighted)
        factors = [
            (1 - metrics.artifact_density, 0.3),  # Low artifacts
            (1 - metrics.gap_density, 0.2),  # Few gaps
            (metrics.timing_naturalness, 0.2),  # Natural timing
            (metrics.velocity_dynamics, 0.1),  # Good dynamics
            (metrics.phrase_integrity, 0.1),  # Intact phrases
            (metrics.creative_iteration_speed, 0.1),  # Fast iteration
        ]

        return sum(factor * weight for factor, weight in factors)

    def _calculate_workflow_score(self, metrics: WorkflowMetrics) -> float:
        """Calculate overall workflow score 0-1."""
        # Combine all factors
        score = (
            metrics.retained_usefulness * 0.4 +
            metrics.producer_satisfaction_estimate * 0.3 +
            metrics.creative_iteration_speed * 0.3
        )

        return min(1.0, max(0.0, score))

    def _summarize_issues(self, assessments: List[NoteAssessment]) -> Dict[str, int]:
        """Summarize issues across all notes."""
        issue_counts: Dict[str, int] = {}

        for assessment in assessments:
            for issue in assessment.issues:
                issue_counts[issue] = issue_counts.get(issue, 0) + 1

        return issue_counts


def evaluate_workflow(
    notes: List[Tuple[int, float, float, int]],
    confidences: Optional[List[float]] = None,
    tempo: float = 120.0,
    duration_sec: float = 0.0,
) -> WorkflowMetrics:
    """Convenience function for workflow evaluation.

    Args:
        notes: List of (pitch, start, end, velocity) tuples
        confidences: Optional confidence scores
        tempo: Tempo in BPM
        duration_sec: Total duration

    Returns:
        WorkflowMetrics
    """
    evaluator = WorkflowEvaluator()
    return evaluator.evaluate(notes, confidences, tempo, duration_sec)


def get_quality_recommendation(metrics: WorkflowMetrics) -> str:
    """Get a human-readable quality recommendation.

    Args:
        metrics: WorkflowMetrics from evaluation

    Returns:
        Recommendation string
    """
    if metrics.editable_quality == "drop-in":
        return (
            "Excellent quality - ready for direct use in production. "
            "Minor touch-ups optional."
        )
    elif metrics.editable_quality == "minor-edits":
        return (
            f"Good quality - usable with minor edits. "
            f"Estimated cleanup: {metrics.cleanup_time_estimate_min:.1f} min. "
            f"Focus on: {_get_top_issues(metrics.issue_summary)}"
        )
    elif metrics.editable_quality == "major-cleanup":
        return (
            f"Moderate quality - requires significant cleanup. "
            f"Estimated time: {metrics.cleanup_time_estimate_min:.1f} min. "
            f"Main issues: {_get_top_issues(metrics.issue_summary)}"
        )
    else:
        return (
            "Poor quality - may be faster to transcribe manually. "
            "Consider re-running with different parameters."
        )


def _get_top_issues(issue_summary: Dict[str, int], top_n: int = 3) -> str:
    """Get top N issues as string."""
    if not issue_summary:
        return "none identified"

    sorted_issues = sorted(issue_summary.items(), key=lambda x: -x[1])[:top_n]
    return ", ".join(f"{issue} ({count})" for issue, count in sorted_issues)
