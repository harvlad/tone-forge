"""Matching auditor for verifying benchmark metric integrity.

Audits the note matching logic for:
- Duplicate counting (same GT note matched multiple times)
- False true-positive matches (loose tolerance abuse)
- Note collapsing (multiple notes merged incorrectly)
- Timing drift issues
- Greedy vs optimal assignment differences
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from scipy.optimize import linear_sum_assignment

logger = logging.getLogger(__name__)


class MatchIssue(Enum):
    """Types of matching issues."""
    DUPLICATE_GT_MATCH = "duplicate_gt_match"
    DUPLICATE_EXT_MATCH = "duplicate_ext_match"
    TOLERANCE_ABUSE = "tolerance_abuse"
    GREEDY_SUBOPTIMAL = "greedy_suboptimal"
    TIMING_DRIFT = "timing_drift"
    OCTAVE_MISMATCH = "octave_mismatch"
    NOTE_COLLAPSING = "note_collapsing"
    SUSPICIOUS_CLUSTER = "suspicious_cluster"


@dataclass
class SuspiciousMatch:
    """A match flagged as potentially suspicious."""

    issue_type: MatchIssue
    extracted_idx: int
    ground_truth_idx: int
    extracted_note: Tuple[int, float, float, int]  # pitch, onset, offset, vel
    ground_truth_note: Tuple[int, float, float, int]

    # Details
    onset_error_ms: float = 0.0
    pitch_error_cents: float = 0.0
    confidence_impact: float = 0.0  # How much this match inflates F1

    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue_type": self.issue_type.value,
            "extracted_idx": self.extracted_idx,
            "ground_truth_idx": self.ground_truth_idx,
            "extracted_note": self.extracted_note,
            "ground_truth_note": self.ground_truth_note,
            "onset_error_ms": self.onset_error_ms,
            "pitch_error_cents": self.pitch_error_cents,
            "confidence_impact": self.confidence_impact,
            "description": self.description,
        }


@dataclass
class DuplicateInflation:
    """Analysis of potential duplicate counting."""

    gt_matched_counts: Dict[int, int] = field(default_factory=dict)
    ext_matched_counts: Dict[int, int] = field(default_factory=dict)

    gt_duplicates: int = 0  # GT notes matched more than once
    ext_duplicates: int = 0  # Extracted notes matched more than once

    inflated_tp: int = 0  # TP that would be lower with proper dedup
    corrected_f1: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gt_duplicates": self.gt_duplicates,
            "ext_duplicates": self.ext_duplicates,
            "inflated_tp": self.inflated_tp,
            "corrected_f1": self.corrected_f1,
        }


@dataclass
class MatchingAuditReport:
    """Complete audit report for matching validity."""

    # Sample identification
    sample_id: str = ""

    # Original metrics
    original_f1: float = 0.0
    original_precision: float = 0.0
    original_recall: float = 0.0
    original_tp: int = 0
    original_fp: int = 0
    original_fn: int = 0

    # Corrected metrics (after dedup)
    corrected_f1: float = 0.0
    corrected_precision: float = 0.0
    corrected_recall: float = 0.0
    corrected_tp: int = 0
    corrected_fp: int = 0
    corrected_fn: int = 0

    # Optimal assignment metrics (Hungarian algorithm)
    optimal_f1: float = 0.0
    optimal_tp: int = 0
    greedy_vs_optimal_delta: float = 0.0

    # Issues found
    suspicious_matches: List[SuspiciousMatch] = field(default_factory=list)
    duplicate_analysis: Optional[DuplicateInflation] = None

    # Statistics
    avg_onset_error_ms: float = 0.0
    max_onset_error_ms: float = 0.0
    avg_pitch_error_cents: float = 0.0
    onset_error_std: float = 0.0

    # Edge case flags
    has_timing_drift: bool = False
    has_octave_confusion: bool = False
    has_duplicate_inflation: bool = False
    has_greedy_suboptimality: bool = False

    # Confidence in metrics
    metric_confidence: float = 1.0  # 1.0 = fully confident, 0 = suspicious

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "original_metrics": {
                "f1": self.original_f1,
                "precision": self.original_precision,
                "recall": self.original_recall,
                "tp": self.original_tp,
                "fp": self.original_fp,
                "fn": self.original_fn,
            },
            "corrected_metrics": {
                "f1": self.corrected_f1,
                "precision": self.corrected_precision,
                "recall": self.corrected_recall,
                "tp": self.corrected_tp,
                "fp": self.corrected_fp,
                "fn": self.corrected_fn,
            },
            "optimal_metrics": {
                "f1": self.optimal_f1,
                "tp": self.optimal_tp,
                "greedy_vs_optimal_delta": self.greedy_vs_optimal_delta,
            },
            "suspicious_matches": [m.to_dict() for m in self.suspicious_matches],
            "duplicate_analysis": self.duplicate_analysis.to_dict() if self.duplicate_analysis else None,
            "statistics": {
                "avg_onset_error_ms": self.avg_onset_error_ms,
                "max_onset_error_ms": self.max_onset_error_ms,
                "avg_pitch_error_cents": self.avg_pitch_error_cents,
                "onset_error_std": self.onset_error_std,
            },
            "flags": {
                "has_timing_drift": self.has_timing_drift,
                "has_octave_confusion": self.has_octave_confusion,
                "has_duplicate_inflation": self.has_duplicate_inflation,
                "has_greedy_suboptimality": self.has_greedy_suboptimality,
            },
            "metric_confidence": self.metric_confidence,
        }

    def summary(self) -> str:
        lines = [
            f"Matching Audit Report: {self.sample_id}",
            "=" * 60,
            "",
            "Original Metrics:",
            f"  F1: {self.original_f1:.1%}  P: {self.original_precision:.1%}  R: {self.original_recall:.1%}",
            f"  TP: {self.original_tp}  FP: {self.original_fp}  FN: {self.original_fn}",
            "",
        ]

        if self.has_duplicate_inflation or self.has_greedy_suboptimality:
            lines.extend([
                "Corrected Metrics:",
                f"  F1: {self.corrected_f1:.1%}  P: {self.corrected_precision:.1%}  R: {self.corrected_recall:.1%}",
                f"  TP: {self.corrected_tp}  FP: {self.corrected_fp}  FN: {self.corrected_fn}",
                "",
                "Optimal (Hungarian) Assignment:",
                f"  F1: {self.optimal_f1:.1%}  TP: {self.optimal_tp}",
                f"  Greedy vs Optimal Delta: {self.greedy_vs_optimal_delta:+.1%}",
                "",
            ])

        lines.extend([
            "Timing Statistics:",
            f"  Avg onset error: {self.avg_onset_error_ms:.1f}ms",
            f"  Max onset error: {self.max_onset_error_ms:.1f}ms",
            f"  Onset error std: {self.onset_error_std:.1f}ms",
            f"  Avg pitch error: {self.avg_pitch_error_cents:.1f} cents",
            "",
        ])

        if self.suspicious_matches:
            lines.extend([
                f"Suspicious Matches: {len(self.suspicious_matches)}",
            ])
            for sm in self.suspicious_matches[:5]:
                lines.append(f"  - {sm.issue_type.value}: {sm.description}")
            if len(self.suspicious_matches) > 5:
                lines.append(f"  ... and {len(self.suspicious_matches) - 5} more")
            lines.append("")

        flags = []
        if self.has_timing_drift:
            flags.append("TIMING_DRIFT")
        if self.has_octave_confusion:
            flags.append("OCTAVE_CONFUSION")
        if self.has_duplicate_inflation:
            flags.append("DUPLICATE_INFLATION")
        if self.has_greedy_suboptimality:
            flags.append("GREEDY_SUBOPTIMAL")

        if flags:
            lines.append(f"Flags: {', '.join(flags)}")

        lines.append(f"Metric Confidence: {self.metric_confidence:.0%}")

        return "\n".join(lines)


class MatchingAuditor:
    """Audits note matching for benchmark validity.

    Validates that:
    1. No duplicate GT notes are matched
    2. No duplicate extracted notes match same GT
    3. Greedy matching doesn't miss better assignments
    4. Timing tolerance isn't being abused
    5. Octave errors aren't inflating scores
    """

    def __init__(
        self,
        onset_tolerance_ms: float = 50.0,
        pitch_tolerance_cents: float = 50.0,
        suspicious_onset_threshold_ms: float = 45.0,  # Flag if close to limit
        drift_detection_window_ms: float = 100.0,
    ):
        """Initialize the auditor.

        Args:
            onset_tolerance_ms: Matching tolerance for onsets
            pitch_tolerance_cents: Matching tolerance for pitch
            suspicious_onset_threshold_ms: Flag matches above this
            drift_detection_window_ms: Window for detecting systematic drift
        """
        self.onset_tolerance_ms = onset_tolerance_ms
        self.pitch_tolerance_cents = pitch_tolerance_cents
        self.suspicious_onset_threshold_ms = suspicious_onset_threshold_ms
        self.drift_detection_window_ms = drift_detection_window_ms

    def audit(
        self,
        extracted_notes: List[Tuple[int, float, float, int]],
        ground_truth_notes: List[Tuple[int, float, float, int]],
        sample_id: str = "",
    ) -> MatchingAuditReport:
        """Audit the matching between extracted and ground truth notes.

        Args:
            extracted_notes: List of (pitch, onset, offset, velocity)
            ground_truth_notes: List of (pitch, onset, offset, velocity)
            sample_id: Sample identifier for reporting

        Returns:
            MatchingAuditReport with all findings
        """
        report = MatchingAuditReport(sample_id=sample_id)

        if not ground_truth_notes:
            report.metric_confidence = 0.0
            return report

        # Run greedy matching (as implemented)
        greedy_matches, onset_errors, pitch_errors = self._greedy_match(
            extracted_notes, ground_truth_notes
        )

        # Compute original metrics
        tp = len(greedy_matches)
        fp = len(extracted_notes) - tp
        fn = len(ground_truth_notes) - len(set(m[1] for m in greedy_matches))

        report.original_tp = tp
        report.original_fp = fp
        report.original_fn = fn
        report.original_precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        report.original_recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        report.original_f1 = (
            2 * report.original_precision * report.original_recall /
            (report.original_precision + report.original_recall)
            if (report.original_precision + report.original_recall) > 0 else 0
        )

        # Analyze for duplicates
        report.duplicate_analysis = self._analyze_duplicates(greedy_matches)
        if report.duplicate_analysis.gt_duplicates > 0 or report.duplicate_analysis.ext_duplicates > 0:
            report.has_duplicate_inflation = True

        # Run optimal (Hungarian) matching
        optimal_matches = self._optimal_match(extracted_notes, ground_truth_notes)
        optimal_tp = len(optimal_matches)

        report.optimal_tp = optimal_tp
        optimal_precision = optimal_tp / len(extracted_notes) if extracted_notes else 0
        optimal_recall = optimal_tp / len(ground_truth_notes) if ground_truth_notes else 0
        report.optimal_f1 = (
            2 * optimal_precision * optimal_recall /
            (optimal_precision + optimal_recall)
            if (optimal_precision + optimal_recall) > 0 else 0
        )

        report.greedy_vs_optimal_delta = report.original_f1 - report.optimal_f1
        if abs(report.greedy_vs_optimal_delta) > 0.01:  # >1% difference
            report.has_greedy_suboptimality = True

        # Compute timing statistics
        if onset_errors:
            report.avg_onset_error_ms = np.mean(onset_errors)
            report.max_onset_error_ms = np.max(onset_errors)
            report.onset_error_std = np.std(onset_errors)

        if pitch_errors:
            report.avg_pitch_error_cents = np.mean(pitch_errors)

        # Detect timing drift
        if self._detect_timing_drift(onset_errors):
            report.has_timing_drift = True

        # Find suspicious matches
        report.suspicious_matches = self._find_suspicious_matches(
            greedy_matches, extracted_notes, ground_truth_notes, onset_errors, pitch_errors
        )

        # Detect octave confusion
        octave_issues = [
            sm for sm in report.suspicious_matches
            if sm.issue_type == MatchIssue.OCTAVE_MISMATCH
        ]
        if len(octave_issues) > 0.1 * tp:  # >10% octave issues
            report.has_octave_confusion = True

        # Compute corrected metrics (after dedup)
        corrected_matches = self._deduplicate_matches(greedy_matches)
        report.corrected_tp = len(corrected_matches)
        report.corrected_fp = len(extracted_notes) - report.corrected_tp
        report.corrected_fn = len(ground_truth_notes) - len(set(m[1] for m in corrected_matches))

        report.corrected_precision = (
            report.corrected_tp / (report.corrected_tp + report.corrected_fp)
            if (report.corrected_tp + report.corrected_fp) > 0 else 0
        )
        report.corrected_recall = (
            report.corrected_tp / (report.corrected_tp + report.corrected_fn)
            if (report.corrected_tp + report.corrected_fn) > 0 else 0
        )
        report.corrected_f1 = (
            2 * report.corrected_precision * report.corrected_recall /
            (report.corrected_precision + report.corrected_recall)
            if (report.corrected_precision + report.corrected_recall) > 0 else 0
        )

        # Compute overall confidence
        report.metric_confidence = self._compute_confidence(report)

        return report

    def _greedy_match(
        self,
        extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
    ) -> Tuple[List[Tuple[int, int]], List[float], List[float]]:
        """Run greedy matching (replicating the original algorithm).

        Returns:
            (matches, onset_errors_ms, pitch_errors_cents)
            matches is list of (extracted_idx, gt_idx) pairs
        """
        onset_tol_sec = self.onset_tolerance_ms / 1000.0
        matches: List[Tuple[int, int]] = []
        matched_gt: Set[int] = set()
        onset_errors: List[float] = []
        pitch_errors: List[float] = []

        for i, ext in enumerate(extracted):
            ext_pitch, ext_onset, _, _ = ext
            best_match = None
            best_dist = float('inf')

            for j, truth in enumerate(ground_truth):
                if j in matched_gt:
                    continue

                truth_pitch, truth_onset, _, _ = truth

                # Pitch check
                pitch_diff_cents = abs(ext_pitch - truth_pitch) * 100
                if pitch_diff_cents > self.pitch_tolerance_cents:
                    continue

                # Onset check
                onset_diff = abs(ext_onset - truth_onset)
                if onset_diff > onset_tol_sec:
                    continue

                # Combined distance
                dist = onset_diff + pitch_diff_cents / 1000
                if dist < best_dist:
                    best_dist = dist
                    best_match = j

            if best_match is not None:
                matches.append((i, best_match))
                matched_gt.add(best_match)

                truth = ground_truth[best_match]
                onset_errors.append(abs(ext_onset - truth[1]) * 1000)
                pitch_errors.append(abs(ext_pitch - truth[0]) * 100)

        return matches, onset_errors, pitch_errors

    def _optimal_match(
        self,
        extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
    ) -> List[Tuple[int, int]]:
        """Run optimal matching using Hungarian algorithm.

        Returns:
            List of (extracted_idx, gt_idx) pairs
        """
        if not extracted or not ground_truth:
            return []

        onset_tol_sec = self.onset_tolerance_ms / 1000.0
        n_ext = len(extracted)
        n_gt = len(ground_truth)

        # Build cost matrix (large cost = no match possible)
        INF = 1e9
        cost_matrix = np.full((n_ext, n_gt), INF)

        for i, ext in enumerate(extracted):
            ext_pitch, ext_onset, _, _ = ext
            for j, truth in enumerate(ground_truth):
                truth_pitch, truth_onset, _, _ = truth

                pitch_diff_cents = abs(ext_pitch - truth_pitch) * 100
                onset_diff = abs(ext_onset - truth_onset)

                if pitch_diff_cents <= self.pitch_tolerance_cents and onset_diff <= onset_tol_sec:
                    cost_matrix[i, j] = onset_diff + pitch_diff_cents / 1000

        # Run Hungarian algorithm
        row_ind, col_ind = linear_sum_assignment(cost_matrix)

        # Filter out invalid matches (INF cost)
        matches = []
        for i, j in zip(row_ind, col_ind):
            if cost_matrix[i, j] < INF:
                matches.append((i, j))

        return matches

    def _analyze_duplicates(
        self,
        matches: List[Tuple[int, int]],
    ) -> DuplicateInflation:
        """Analyze for duplicate counting."""
        analysis = DuplicateInflation()

        # Count how many times each GT note is matched
        for ext_idx, gt_idx in matches:
            analysis.gt_matched_counts[gt_idx] = analysis.gt_matched_counts.get(gt_idx, 0) + 1
            analysis.ext_matched_counts[ext_idx] = analysis.ext_matched_counts.get(ext_idx, 0) + 1

        # Find duplicates
        analysis.gt_duplicates = sum(1 for c in analysis.gt_matched_counts.values() if c > 1)
        analysis.ext_duplicates = sum(1 for c in analysis.ext_matched_counts.values() if c > 1)

        # Count inflated TP
        analysis.inflated_tp = sum(c - 1 for c in analysis.gt_matched_counts.values() if c > 1)

        return analysis

    def _deduplicate_matches(
        self,
        matches: List[Tuple[int, int]],
    ) -> List[Tuple[int, int]]:
        """Remove duplicate matches, keeping only one per GT note."""
        seen_gt: Set[int] = set()
        seen_ext: Set[int] = set()
        deduped = []

        for ext_idx, gt_idx in matches:
            if gt_idx not in seen_gt and ext_idx not in seen_ext:
                deduped.append((ext_idx, gt_idx))
                seen_gt.add(gt_idx)
                seen_ext.add(ext_idx)

        return deduped

    def _detect_timing_drift(self, onset_errors: List[float]) -> bool:
        """Detect systematic timing drift."""
        if len(onset_errors) < 5:
            return False

        # Check if errors are predominantly in one direction
        # (This would require signed errors, which we don't have in the current impl)
        # For now, check if the mean error is suspiciously high
        mean_error = np.mean(onset_errors)
        if mean_error > self.onset_tolerance_ms * 0.7:  # >70% of tolerance
            return True

        return False

    def _find_suspicious_matches(
        self,
        matches: List[Tuple[int, int]],
        extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
        onset_errors: List[float],
        pitch_errors: List[float],
    ) -> List[SuspiciousMatch]:
        """Find matches that seem suspicious."""
        suspicious = []

        for idx, (ext_idx, gt_idx) in enumerate(matches):
            ext_note = extracted[ext_idx]
            gt_note = ground_truth[gt_idx]

            onset_err = onset_errors[idx] if idx < len(onset_errors) else 0
            pitch_err = pitch_errors[idx] if idx < len(pitch_errors) else 0

            # Flag tolerance boundary cases
            if onset_err > self.suspicious_onset_threshold_ms:
                suspicious.append(SuspiciousMatch(
                    issue_type=MatchIssue.TOLERANCE_ABUSE,
                    extracted_idx=ext_idx,
                    ground_truth_idx=gt_idx,
                    extracted_note=ext_note,
                    ground_truth_note=gt_note,
                    onset_error_ms=onset_err,
                    pitch_error_cents=pitch_err,
                    description=f"Onset error {onset_err:.1f}ms close to {self.onset_tolerance_ms}ms limit",
                ))

            # Flag octave mismatches
            pitch_diff = abs(ext_note[0] - gt_note[0])
            if pitch_diff in [11, 12, 13, 23, 24, 25]:  # Near octave boundaries
                suspicious.append(SuspiciousMatch(
                    issue_type=MatchIssue.OCTAVE_MISMATCH,
                    extracted_idx=ext_idx,
                    ground_truth_idx=gt_idx,
                    extracted_note=ext_note,
                    ground_truth_note=gt_note,
                    onset_error_ms=onset_err,
                    pitch_error_cents=pitch_err,
                    description=f"Pitch diff of {pitch_diff} semitones (near octave)",
                ))

        return suspicious

    def _compute_confidence(self, report: MatchingAuditReport) -> float:
        """Compute overall confidence in the metrics."""
        confidence = 1.0

        # Penalize for issues
        if report.has_duplicate_inflation:
            confidence *= 0.8
        if report.has_greedy_suboptimality:
            confidence *= 0.9
        if report.has_timing_drift:
            confidence *= 0.85
        if report.has_octave_confusion:
            confidence *= 0.8

        # Penalize for high average errors
        if report.avg_onset_error_ms > self.onset_tolerance_ms * 0.6:
            confidence *= 0.9

        # Penalize for many suspicious matches
        if report.original_tp > 0:
            suspicious_ratio = len(report.suspicious_matches) / report.original_tp
            if suspicious_ratio > 0.2:
                confidence *= 0.7
            elif suspicious_ratio > 0.1:
                confidence *= 0.85

        return max(confidence, 0.0)
