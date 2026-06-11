"""Comprehensive benchmark validation runner.

Runs all validation phases:
1. Benchmark validity audit
2. Matching strategy comparison
3. Cross-dataset validation (placeholder)
4. False positive analysis
5. False negative analysis
6. Confidence calibration (integrates with existing)
7. Human usability scoring (placeholder)

Generates a complete validation report.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from .matching_auditor import MatchingAuditor, MatchingAuditReport
from .matching_strategies import (
    compare_strategies,
    StrategyComparison,
    StrictMatcher,
    MusicalMatcher,
    ReconstructionMatcher,
)
from .false_positive_analyzer import FalsePositiveAnalyzer, FalsePositiveReport
from .false_negative_analyzer import FalseNegativeAnalyzer, FalseNegativeReport

logger = logging.getLogger(__name__)


@dataclass
class ValidationSampleResult:
    """Complete validation result for a single sample."""

    sample_id: str

    # Extracted and GT notes
    extracted_note_count: int = 0
    ground_truth_note_count: int = 0

    # Phase 1: Matching audit
    audit_report: Optional[MatchingAuditReport] = None

    # Phase 2: Strategy comparison
    strategy_comparison: Optional[StrategyComparison] = None

    # Phase 4: FP analysis
    fp_report: Optional[FalsePositiveReport] = None

    # Phase 5: FN analysis
    fn_report: Optional[FalseNegativeReport] = None

    # Summary metrics
    original_f1: float = 0.0
    corrected_f1: float = 0.0
    optimal_f1: float = 0.0
    strict_f1: float = 0.0
    musical_f1: float = 0.0
    reconstruction_f1: float = 0.0

    # Flags
    metric_confidence: float = 1.0
    has_issues: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "note_counts": {
                "extracted": self.extracted_note_count,
                "ground_truth": self.ground_truth_note_count,
            },
            "metrics": {
                "original_f1": self.original_f1,
                "corrected_f1": self.corrected_f1,
                "optimal_f1": self.optimal_f1,
                "strict_f1": self.strict_f1,
                "musical_f1": self.musical_f1,
                "reconstruction_f1": self.reconstruction_f1,
            },
            "metric_confidence": self.metric_confidence,
            "has_issues": self.has_issues,
            "audit": self.audit_report.to_dict() if self.audit_report else None,
            "strategy_comparison": self.strategy_comparison.to_dict() if self.strategy_comparison else None,
            "fp_analysis": self.fp_report.to_dict() if self.fp_report else None,
            "fn_analysis": self.fn_report.to_dict() if self.fn_report else None,
        }


@dataclass
class BenchmarkValidationReport:
    """Complete benchmark validation report."""

    # Metadata
    run_timestamp: str = ""
    manifest_name: str = ""
    total_samples: int = 0
    successful_samples: int = 0

    # Per-sample results
    sample_results: List[ValidationSampleResult] = field(default_factory=list)

    # Aggregate metrics
    aggregate_original_f1: float = 0.0
    aggregate_corrected_f1: float = 0.0
    aggregate_optimal_f1: float = 0.0
    aggregate_strict_f1: float = 0.0
    aggregate_musical_f1: float = 0.0
    aggregate_reconstruction_f1: float = 0.0

    # Metric confidence
    aggregate_metric_confidence: float = 0.0
    samples_with_issues: int = 0

    # Phase 1: Audit summary
    samples_with_duplicate_inflation: int = 0
    samples_with_greedy_suboptimality: int = 0
    samples_with_timing_drift: int = 0
    samples_with_octave_confusion: int = 0

    # Phase 2: Strategy delta summary
    avg_musical_vs_strict_delta: float = 0.0
    avg_recon_vs_strict_delta: float = 0.0

    # Phase 4: FP category summary
    fp_category_totals: Dict[str, int] = field(default_factory=dict)
    total_false_positives: int = 0
    dominant_fp_category: str = ""

    # Phase 5: FN category summary
    fn_category_totals: Dict[str, int] = field(default_factory=dict)
    total_false_negatives: int = 0
    dominant_fn_category: str = ""

    # Blind spots
    common_blind_spots: Dict[str, int] = field(default_factory=dict)

    # Overall assessment
    benchmark_validity: str = "unknown"  # "valid", "suspicious", "unreliable"
    validity_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metadata": {
                "run_timestamp": self.run_timestamp,
                "manifest_name": self.manifest_name,
                "total_samples": self.total_samples,
                "successful_samples": self.successful_samples,
            },
            "aggregate_metrics": {
                "original_f1": self.aggregate_original_f1,
                "corrected_f1": self.aggregate_corrected_f1,
                "optimal_f1": self.aggregate_optimal_f1,
                "strict_f1": self.aggregate_strict_f1,
                "musical_f1": self.aggregate_musical_f1,
                "reconstruction_f1": self.aggregate_reconstruction_f1,
            },
            "metric_confidence": {
                "aggregate": self.aggregate_metric_confidence,
                "samples_with_issues": self.samples_with_issues,
            },
            "audit_summary": {
                "duplicate_inflation": self.samples_with_duplicate_inflation,
                "greedy_suboptimality": self.samples_with_greedy_suboptimality,
                "timing_drift": self.samples_with_timing_drift,
                "octave_confusion": self.samples_with_octave_confusion,
            },
            "strategy_comparison": {
                "avg_musical_vs_strict": self.avg_musical_vs_strict_delta,
                "avg_recon_vs_strict": self.avg_recon_vs_strict_delta,
            },
            "fp_summary": {
                "total": self.total_false_positives,
                "by_category": self.fp_category_totals,
                "dominant": self.dominant_fp_category,
            },
            "fn_summary": {
                "total": self.total_false_negatives,
                "by_category": self.fn_category_totals,
                "dominant": self.dominant_fn_category,
            },
            "blind_spots": self.common_blind_spots,
            "overall_assessment": {
                "validity": self.benchmark_validity,
                "reasons": self.validity_reasons,
            },
            "sample_results": [r.to_dict() for r in self.sample_results],
        }

    def summary(self) -> str:
        lines = [
            "=" * 70,
            "BENCHMARK VALIDATION REPORT",
            "=" * 70,
            "",
            f"Run: {self.run_timestamp}",
            f"Manifest: {self.manifest_name}",
            f"Samples: {self.successful_samples}/{self.total_samples}",
            "",
            "AGGREGATE METRICS",
            "-" * 40,
            f"{'Metric':<25} {'Value':>10}",
            "-" * 40,
            f"{'Original F1':<25} {self.aggregate_original_f1:>10.1%}",
            f"{'Corrected F1':<25} {self.aggregate_corrected_f1:>10.1%}",
            f"{'Optimal F1':<25} {self.aggregate_optimal_f1:>10.1%}",
            f"{'Strict F1':<25} {self.aggregate_strict_f1:>10.1%}",
            f"{'Musical F1':<25} {self.aggregate_musical_f1:>10.1%}",
            f"{'Reconstruction F1':<25} {self.aggregate_reconstruction_f1:>10.1%}",
            "",
            "METRIC CONFIDENCE",
            "-" * 40,
            f"Aggregate confidence: {self.aggregate_metric_confidence:.1%}",
            f"Samples with issues: {self.samples_with_issues}/{self.successful_samples}",
            "",
            "AUDIT FINDINGS",
            "-" * 40,
            f"Duplicate inflation: {self.samples_with_duplicate_inflation} samples",
            f"Greedy suboptimality: {self.samples_with_greedy_suboptimality} samples",
            f"Timing drift: {self.samples_with_timing_drift} samples",
            f"Octave confusion: {self.samples_with_octave_confusion} samples",
            "",
            "STRATEGY COMPARISON",
            "-" * 40,
            f"Musical vs Strict delta: {self.avg_musical_vs_strict_delta:+.1%}",
            f"Reconstruction vs Strict: {self.avg_recon_vs_strict_delta:+.1%}",
            "",
        ]

        if self.total_false_positives > 0:
            lines.extend([
                f"FALSE POSITIVES: {self.total_false_positives}",
                "-" * 40,
            ])
            for cat, count in sorted(self.fp_category_totals.items(), key=lambda x: -x[1])[:5]:
                pct = count / self.total_false_positives
                lines.append(f"  {cat:25s} {count:5d} ({pct:5.1%})")
            lines.append("")

        if self.total_false_negatives > 0:
            lines.extend([
                f"FALSE NEGATIVES: {self.total_false_negatives}",
                "-" * 40,
            ])
            for cat, count in sorted(self.fn_category_totals.items(), key=lambda x: -x[1])[:5]:
                pct = count / self.total_false_negatives
                lines.append(f"  {cat:25s} {count:5d} ({pct:5.1%})")
            lines.append("")

        if self.common_blind_spots:
            lines.extend([
                "EXTRACTION BLIND SPOTS",
                "-" * 40,
            ])
            for spot, count in self.common_blind_spots.items():
                lines.append(f"  {spot}: {count} samples affected")
            lines.append("")

        lines.extend([
            "OVERALL ASSESSMENT",
            "=" * 40,
            f"Benchmark validity: {self.benchmark_validity.upper()}",
            "",
        ])

        for reason in self.validity_reasons:
            lines.append(f"  - {reason}")

        return "\n".join(lines)


class BenchmarkValidationRunner:
    """Runs comprehensive benchmark validation.

    Validates that F1 scores are trustworthy and identifies
    systematic extraction failures.
    """

    def __init__(
        self,
        onset_tolerance_ms: float = 50.0,
        pitch_tolerance_cents: float = 50.0,
    ):
        """Initialize the validation runner.

        Args:
            onset_tolerance_ms: Matching tolerance for onsets
            pitch_tolerance_cents: Matching tolerance for pitch
        """
        self.onset_tolerance_ms = onset_tolerance_ms
        self.pitch_tolerance_cents = pitch_tolerance_cents

        self.auditor = MatchingAuditor(
            onset_tolerance_ms=onset_tolerance_ms,
            pitch_tolerance_cents=pitch_tolerance_cents,
        )
        self.fp_analyzer = FalsePositiveAnalyzer(onset_tolerance_ms=onset_tolerance_ms)
        self.fn_analyzer = FalseNegativeAnalyzer(onset_tolerance_ms=onset_tolerance_ms)

    def validate_sample(
        self,
        extracted_notes: List[Tuple[int, float, float, int]],
        ground_truth_notes: List[Tuple[int, float, float, int]],
        sample_id: str = "",
        tempo_bpm: float = 120.0,
    ) -> ValidationSampleResult:
        """Run full validation on a single sample.

        Args:
            extracted_notes: Extracted notes (pitch, onset, offset, velocity)
            ground_truth_notes: Ground truth notes
            sample_id: Sample identifier
            tempo_bpm: Tempo for reconstruction scoring

        Returns:
            ValidationSampleResult with all analyses
        """
        result = ValidationSampleResult(sample_id=sample_id)
        result.extracted_note_count = len(extracted_notes)
        result.ground_truth_note_count = len(ground_truth_notes)

        # Phase 1: Audit matching
        result.audit_report = self.auditor.audit(
            extracted_notes, ground_truth_notes, sample_id
        )
        result.original_f1 = result.audit_report.original_f1
        result.corrected_f1 = result.audit_report.corrected_f1
        result.optimal_f1 = result.audit_report.optimal_f1
        result.metric_confidence = result.audit_report.metric_confidence

        if (result.audit_report.has_duplicate_inflation or
            result.audit_report.has_greedy_suboptimality or
            result.audit_report.has_timing_drift or
            result.audit_report.has_octave_confusion):
            result.has_issues = True

        # Phase 2: Strategy comparison
        result.strategy_comparison = compare_strategies(
            extracted_notes, ground_truth_notes, sample_id, tempo_bpm
        )
        result.strict_f1 = result.strategy_comparison.strict_result.f1
        result.musical_f1 = result.strategy_comparison.musical_result.f1
        result.reconstruction_f1 = result.strategy_comparison.reconstruction_result.weighted_f1

        # Get matched indices from strict matcher (for FP/FN analysis)
        strict = StrictMatcher()
        strict_result = strict.match(extracted_notes, ground_truth_notes)
        matched_ext = set(m.extracted_idx for m in strict_result.matches)
        matched_gt = set(m.ground_truth_idx for m in strict_result.matches)

        # Phase 4: FP analysis
        result.fp_report = self.fp_analyzer.analyze(
            extracted_notes, ground_truth_notes, matched_ext, sample_id
        )

        # Phase 5: FN analysis
        result.fn_report = self.fn_analyzer.analyze(
            extracted_notes, ground_truth_notes, matched_gt, sample_id
        )

        return result

    def validate_batch(
        self,
        samples: List[Tuple[str, List, List]],  # (sample_id, extracted, gt)
        manifest_name: str = "benchmark",
    ) -> BenchmarkValidationReport:
        """Run validation on a batch of samples.

        Args:
            samples: List of (sample_id, extracted_notes, ground_truth_notes)
            manifest_name: Name of the benchmark manifest

        Returns:
            BenchmarkValidationReport with aggregate results
        """
        report = BenchmarkValidationReport()
        report.run_timestamp = datetime.now().isoformat()
        report.manifest_name = manifest_name
        report.total_samples = len(samples)

        # Validate each sample
        for sample_id, extracted, gt in samples:
            try:
                sample_result = self.validate_sample(extracted, gt, sample_id)
                report.sample_results.append(sample_result)
                report.successful_samples += 1
            except Exception as e:
                logger.error(f"Failed to validate {sample_id}: {e}")

        if not report.sample_results:
            report.benchmark_validity = "unreliable"
            report.validity_reasons.append("No samples successfully validated")
            return report

        # Aggregate metrics
        report.aggregate_original_f1 = np.mean([r.original_f1 for r in report.sample_results])
        report.aggregate_corrected_f1 = np.mean([r.corrected_f1 for r in report.sample_results])
        report.aggregate_optimal_f1 = np.mean([r.optimal_f1 for r in report.sample_results])
        report.aggregate_strict_f1 = np.mean([r.strict_f1 for r in report.sample_results])
        report.aggregate_musical_f1 = np.mean([r.musical_f1 for r in report.sample_results])
        report.aggregate_reconstruction_f1 = np.mean([r.reconstruction_f1 for r in report.sample_results])
        report.aggregate_metric_confidence = np.mean([r.metric_confidence for r in report.sample_results])

        # Count issues
        report.samples_with_issues = sum(1 for r in report.sample_results if r.has_issues)
        report.samples_with_duplicate_inflation = sum(
            1 for r in report.sample_results
            if r.audit_report and r.audit_report.has_duplicate_inflation
        )
        report.samples_with_greedy_suboptimality = sum(
            1 for r in report.sample_results
            if r.audit_report and r.audit_report.has_greedy_suboptimality
        )
        report.samples_with_timing_drift = sum(
            1 for r in report.sample_results
            if r.audit_report and r.audit_report.has_timing_drift
        )
        report.samples_with_octave_confusion = sum(
            1 for r in report.sample_results
            if r.audit_report and r.audit_report.has_octave_confusion
        )

        # Strategy deltas
        musical_deltas = [
            r.musical_f1 - r.strict_f1
            for r in report.sample_results
        ]
        recon_deltas = [
            r.reconstruction_f1 - r.strict_f1
            for r in report.sample_results
        ]
        report.avg_musical_vs_strict_delta = np.mean(musical_deltas)
        report.avg_recon_vs_strict_delta = np.mean(recon_deltas)

        # Aggregate FP categories
        for r in report.sample_results:
            if r.fp_report:
                report.total_false_positives += r.fp_report.total_false_positives
                for cat, count in r.fp_report.category_counts.items():
                    report.fp_category_totals[cat] = report.fp_category_totals.get(cat, 0) + count

        if report.fp_category_totals:
            report.dominant_fp_category = max(report.fp_category_totals.items(), key=lambda x: x[1])[0]

        # Aggregate FN categories
        for r in report.sample_results:
            if r.fn_report:
                report.total_false_negatives += r.fn_report.total_false_negatives
                for cat, count in r.fn_report.category_counts.items():
                    report.fn_category_totals[cat] = report.fn_category_totals.get(cat, 0) + count

                # Aggregate blind spots
                for spot in r.fn_report.blind_spots.keys():
                    report.common_blind_spots[spot] = report.common_blind_spots.get(spot, 0) + 1

        if report.fn_category_totals:
            report.dominant_fn_category = max(report.fn_category_totals.items(), key=lambda x: x[1])[0]

        # Determine overall validity
        report.benchmark_validity, report.validity_reasons = self._assess_validity(report)

        return report

    def _assess_validity(
        self,
        report: BenchmarkValidationReport,
    ) -> Tuple[str, List[str]]:
        """Assess overall benchmark validity."""
        reasons = []
        score = 1.0  # Start with valid

        # Check metric confidence
        if report.aggregate_metric_confidence < 0.7:
            score *= 0.7
            reasons.append(f"Low metric confidence: {report.aggregate_metric_confidence:.1%}")

        # Check for widespread issues
        if report.successful_samples > 0:
            issue_ratio = report.samples_with_issues / report.successful_samples
            if issue_ratio > 0.3:
                score *= 0.7
                reasons.append(f"Many samples have issues: {issue_ratio:.1%}")

        # Check greedy vs optimal gap
        greedy_optimal_gap = abs(report.aggregate_original_f1 - report.aggregate_optimal_f1)
        if greedy_optimal_gap > 0.02:
            score *= 0.9
            reasons.append(f"Greedy matching suboptimal by {greedy_optimal_gap:.1%}")

        # Check for duplicate inflation
        if report.samples_with_duplicate_inflation > report.successful_samples * 0.1:
            score *= 0.8
            reasons.append(f"Duplicate inflation in {report.samples_with_duplicate_inflation} samples")

        # Check musical vs strict gap (large gap may indicate tolerance abuse)
        if report.avg_musical_vs_strict_delta > 0.2:
            score *= 0.9
            reasons.append(f"Large musical vs strict gap: {report.avg_musical_vs_strict_delta:.1%}")

        # Assess final validity
        if score >= 0.85:
            validity = "valid"
            if not reasons:
                reasons.append("No significant issues detected")
        elif score >= 0.6:
            validity = "suspicious"
            if not reasons:
                reasons.append("Some concerns about metric reliability")
        else:
            validity = "unreliable"
            if not reasons:
                reasons.append("Significant issues with benchmark metrics")

        return validity, reasons

    def save_report(
        self,
        report: BenchmarkValidationReport,
        output_path: Path,
    ) -> None:
        """Save validation report to JSON."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w") as f:
            json.dump(report.to_dict(), f, indent=2, default=str)

        logger.info(f"Saved validation report to {output_path}")

        # Also save summary
        summary_path = output_path.with_suffix(".txt")
        with open(summary_path, "w") as f:
            f.write(report.summary())
        logger.info(f"Saved summary to {summary_path}")
