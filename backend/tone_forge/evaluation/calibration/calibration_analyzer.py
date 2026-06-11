"""Calibration analyzer for MIDI extraction confidence scores.

Analyzes how well confidence scores predict actual correctness:
- A note with 90% confidence should be correct ~90% of the time
- Computes Expected Calibration Error (ECE)
- Provides per-bucket accuracy breakdown
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class NoteWithConfidence:
    """A note with confidence score and correctness label."""

    pitch: int
    start: float
    end: float
    confidence: float
    is_correct: bool
    pipeline: str = ""
    stem_type: str = ""


@dataclass
class CalibrationBucket:
    """A single bucket in calibration analysis."""

    confidence_range: Tuple[float, float]  # (lower, upper)
    note_count: int
    correct_count: int

    @property
    def accuracy(self) -> float:
        """Actual correctness rate in this bucket."""
        return self.correct_count / self.note_count if self.note_count > 0 else 0.0

    @property
    def expected_accuracy(self) -> float:
        """Expected accuracy (midpoint of confidence range)."""
        return (self.confidence_range[0] + self.confidence_range[1]) / 2

    @property
    def calibration_error(self) -> float:
        """Difference between actual and expected accuracy."""
        return self.accuracy - self.expected_accuracy

    @property
    def abs_calibration_error(self) -> float:
        """Absolute calibration error."""
        return abs(self.calibration_error)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "confidence_range": self.confidence_range,
            "note_count": self.note_count,
            "correct_count": self.correct_count,
            "accuracy": self.accuracy,
            "expected_accuracy": self.expected_accuracy,
            "calibration_error": self.calibration_error,
        }


@dataclass
class CalibrationAnalysis:
    """Complete calibration analysis results."""

    # Buckets
    buckets: List[CalibrationBucket] = field(default_factory=list)

    # Aggregate metrics
    total_notes: int = 0
    total_correct: int = 0

    # Calibration quality metrics
    expected_calibration_error: float = 0.0  # ECE
    maximum_calibration_error: float = 0.0  # MCE
    brier_score: float = 0.0

    # Reliability indicators
    overconfidence_ratio: float = 0.0  # % of buckets where accuracy < expected
    underconfidence_ratio: float = 0.0  # % of buckets where accuracy > expected

    # Per-pipeline breakdown
    per_pipeline_ece: Dict[str, float] = field(default_factory=dict)
    per_stem_ece: Dict[str, float] = field(default_factory=dict)

    @property
    def overall_accuracy(self) -> float:
        """Overall accuracy across all notes."""
        return self.total_correct / self.total_notes if self.total_notes > 0 else 0.0

    @property
    def is_well_calibrated(self) -> bool:
        """Check if calibration is acceptable (ECE < 0.1)."""
        return self.expected_calibration_error < 0.1

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "buckets": [b.to_dict() for b in self.buckets],
            "total_notes": self.total_notes,
            "total_correct": self.total_correct,
            "overall_accuracy": self.overall_accuracy,
            "expected_calibration_error": self.expected_calibration_error,
            "maximum_calibration_error": self.maximum_calibration_error,
            "brier_score": self.brier_score,
            "overconfidence_ratio": self.overconfidence_ratio,
            "underconfidence_ratio": self.underconfidence_ratio,
            "per_pipeline_ece": self.per_pipeline_ece,
            "per_stem_ece": self.per_stem_ece,
            "is_well_calibrated": self.is_well_calibrated,
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        status = "WELL CALIBRATED" if self.is_well_calibrated else "NEEDS CALIBRATION"

        lines = [
            f"Calibration Analysis ({status})",
            "=" * 50,
            f"Total notes:    {self.total_notes}",
            f"Overall acc:    {self.overall_accuracy:.1%}",
            "",
            "Calibration Metrics:",
            f"  ECE:          {self.expected_calibration_error:.3f} (target < 0.1)",
            f"  MCE:          {self.maximum_calibration_error:.3f}",
            f"  Brier Score:  {self.brier_score:.3f}",
            "",
            f"Overconfident:  {self.overconfidence_ratio:.1%} of buckets",
            f"Underconfident: {self.underconfidence_ratio:.1%} of buckets",
            "",
            "Per-Bucket Breakdown:",
        ]

        for bucket in self.buckets:
            if bucket.note_count > 0:
                conf_str = f"[{bucket.confidence_range[0]:.1f}-{bucket.confidence_range[1]:.1f}]"
                acc_str = f"{bucket.accuracy:.1%}"
                exp_str = f"{bucket.expected_accuracy:.1%}"
                err_str = f"{bucket.calibration_error:+.1%}"
                lines.append(
                    f"  {conf_str}: acc={acc_str} exp={exp_str} err={err_str} n={bucket.note_count}"
                )

        if self.per_pipeline_ece:
            lines.extend(["", "Per-Pipeline ECE:"])
            for pipeline, ece in sorted(self.per_pipeline_ece.items()):
                lines.append(f"  {pipeline}: {ece:.3f}")

        if self.per_stem_ece:
            lines.extend(["", "Per-Stem ECE:"])
            for stem, ece in sorted(self.per_stem_ece.items()):
                lines.append(f"  {stem}: {ece:.3f}")

        return "\n".join(lines)


class CalibrationAnalyzer:
    """Analyzer for confidence calibration."""

    def __init__(
        self,
        num_buckets: int = 10,
        min_bucket_size: int = 10,
    ):
        """Initialize analyzer.

        Args:
            num_buckets: Number of confidence buckets (default 10 for deciles)
            min_bucket_size: Minimum notes per bucket for reporting
        """
        self.num_buckets = num_buckets
        self.min_bucket_size = min_bucket_size

    def analyze(
        self,
        notes: List[NoteWithConfidence],
    ) -> CalibrationAnalysis:
        """Analyze calibration of confidence scores.

        Args:
            notes: List of notes with confidence and correctness

        Returns:
            CalibrationAnalysis
        """
        if not notes:
            return CalibrationAnalysis()

        # Create buckets
        bucket_edges = np.linspace(0, 1, self.num_buckets + 1)
        buckets: List[CalibrationBucket] = []

        for i in range(self.num_buckets):
            lower = bucket_edges[i]
            upper = bucket_edges[i + 1]

            # Get notes in this bucket
            bucket_notes = [
                n for n in notes
                if lower <= n.confidence < upper or (i == self.num_buckets - 1 and n.confidence == 1.0)
            ]

            bucket = CalibrationBucket(
                confidence_range=(lower, upper),
                note_count=len(bucket_notes),
                correct_count=sum(1 for n in bucket_notes if n.is_correct),
            )
            buckets.append(bucket)

        # Compute ECE (Expected Calibration Error)
        total_notes = len(notes)
        ece = 0.0
        mce = 0.0

        for bucket in buckets:
            if bucket.note_count > 0:
                weight = bucket.note_count / total_notes
                ece += weight * bucket.abs_calibration_error
                mce = max(mce, bucket.abs_calibration_error)

        # Compute Brier score
        confidences = np.array([n.confidence for n in notes])
        labels = np.array([1.0 if n.is_correct else 0.0 for n in notes])
        brier = np.mean((confidences - labels) ** 2)

        # Compute over/under confidence ratios
        non_empty_buckets = [b for b in buckets if b.note_count >= self.min_bucket_size]
        if non_empty_buckets:
            overconfident = sum(1 for b in non_empty_buckets if b.calibration_error < 0)
            underconfident = sum(1 for b in non_empty_buckets if b.calibration_error > 0)
            overconfidence_ratio = overconfident / len(non_empty_buckets)
            underconfidence_ratio = underconfident / len(non_empty_buckets)
        else:
            overconfidence_ratio = 0.0
            underconfidence_ratio = 0.0

        # Per-pipeline analysis
        per_pipeline_ece = {}
        pipelines = set(n.pipeline for n in notes if n.pipeline)
        for pipeline in pipelines:
            pipeline_notes = [n for n in notes if n.pipeline == pipeline]
            if len(pipeline_notes) >= self.min_bucket_size:
                pipeline_analysis = self.analyze(pipeline_notes)
                per_pipeline_ece[pipeline] = pipeline_analysis.expected_calibration_error

        # Per-stem analysis
        per_stem_ece = {}
        stems = set(n.stem_type for n in notes if n.stem_type)
        for stem in stems:
            stem_notes = [n for n in notes if n.stem_type == stem]
            if len(stem_notes) >= self.min_bucket_size:
                stem_analysis = self.analyze(stem_notes)
                per_stem_ece[stem] = stem_analysis.expected_calibration_error

        return CalibrationAnalysis(
            buckets=buckets,
            total_notes=total_notes,
            total_correct=sum(1 for n in notes if n.is_correct),
            expected_calibration_error=ece,
            maximum_calibration_error=mce,
            brier_score=float(brier),
            overconfidence_ratio=overconfidence_ratio,
            underconfidence_ratio=underconfidence_ratio,
            per_pipeline_ece=per_pipeline_ece,
            per_stem_ece=per_stem_ece,
        )

    def analyze_from_matches(
        self,
        extracted_notes: List[Any],
        match_indices: List[int],
        pipeline: str = "",
        stem_type: str = "",
    ) -> CalibrationAnalysis:
        """Analyze calibration from matched notes.

        Args:
            extracted_notes: List of extracted notes with confidence attribute
            match_indices: Indices of extracted notes that matched ground truth
            pipeline: Pipeline name for breakdown
            stem_type: Stem type for breakdown

        Returns:
            CalibrationAnalysis
        """
        notes_with_conf = []
        match_set = set(match_indices)

        for i, note in enumerate(extracted_notes):
            confidence = getattr(note, 'confidence', 0.5)
            is_correct = i in match_set

            notes_with_conf.append(NoteWithConfidence(
                pitch=note.pitch,
                start=note.start,
                end=note.end,
                confidence=confidence,
                is_correct=is_correct,
                pipeline=pipeline,
                stem_type=stem_type,
            ))

        return self.analyze(notes_with_conf)


def analyze_calibration(
    notes: List[NoteWithConfidence],
    num_buckets: int = 10,
) -> CalibrationAnalysis:
    """Convenience function for calibration analysis.

    Args:
        notes: Notes with confidence and correctness
        num_buckets: Number of confidence buckets

    Returns:
        CalibrationAnalysis
    """
    analyzer = CalibrationAnalyzer(num_buckets=num_buckets)
    return analyzer.analyze(notes)


def create_notes_from_extraction(
    extracted: List[Any],
    ground_truth: List[Any],
    onset_tolerance: float = 0.05,
    offset_tolerance: float = 0.1,
    pipeline: str = "",
    stem_type: str = "",
) -> List[NoteWithConfidence]:
    """Create NoteWithConfidence list from extraction results.

    Args:
        extracted: Extracted notes with confidence
        ground_truth: Ground truth notes
        onset_tolerance: Onset matching tolerance
        offset_tolerance: Offset matching tolerance
        pipeline: Pipeline name
        stem_type: Stem type

    Returns:
        List of NoteWithConfidence
    """
    # Match notes
    matched_gt = set()
    results = []

    for ext_note in extracted:
        confidence = getattr(ext_note, 'confidence', 0.5)
        is_correct = False

        # Find matching ground truth note
        for i, gt_note in enumerate(ground_truth):
            if i in matched_gt:
                continue

            # Check match criteria
            pitch_match = ext_note.pitch == gt_note.pitch
            onset_match = abs(ext_note.start - gt_note.start) <= onset_tolerance
            offset_match = abs(ext_note.end - gt_note.end) <= offset_tolerance

            if pitch_match and onset_match and offset_match:
                is_correct = True
                matched_gt.add(i)
                break

        results.append(NoteWithConfidence(
            pitch=ext_note.pitch,
            start=ext_note.start,
            end=ext_note.end,
            confidence=confidence,
            is_correct=is_correct,
            pipeline=pipeline,
            stem_type=stem_type,
        ))

    return results
