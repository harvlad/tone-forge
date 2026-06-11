"""Profile validation for MIDI extraction profiles.

Validates that extraction profiles meet expected characteristics:
- Precision/recall within expected ranges
- Profile differentiation (profiles should behave differently)
- Consistency across samples
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ProfileExpectation:
    """Expected characteristics for a profile."""

    profile_name: str

    # Metric expectations
    min_f1: float = 0.0
    max_f1: float = 1.0
    min_precision: float = 0.0
    max_precision: float = 1.0
    min_recall: float = 0.0
    max_recall: float = 1.0

    # Relative expectations (vs other profiles)
    should_have_higher_precision_than: List[str] = field(default_factory=list)
    should_have_higher_recall_than: List[str] = field(default_factory=list)

    # Behavior expectations
    expected_note_count_factor: Tuple[float, float] = (0.5, 2.0)  # vs ground truth

    # Description
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "profile_name": self.profile_name,
            "min_f1": self.min_f1,
            "max_f1": self.max_f1,
            "min_precision": self.min_precision,
            "max_precision": self.max_precision,
            "min_recall": self.min_recall,
            "max_recall": self.max_recall,
            "should_have_higher_precision_than": self.should_have_higher_precision_than,
            "should_have_higher_recall_than": self.should_have_higher_recall_than,
            "expected_note_count_factor": self.expected_note_count_factor,
            "description": self.description,
        }


# Predefined expectations for use-case profiles
PROFILE_EXPECTATIONS = {
    "aggressive_recall": ProfileExpectation(
        profile_name="aggressive_recall",
        min_recall=0.7,  # Should have high recall
        max_precision=0.9,  # Precision may suffer
        should_have_higher_recall_than=["precision_first", "balanced"],
        expected_note_count_factor=(1.0, 3.0),  # More notes than ground truth
        description="Maximizes detection, accepts lower precision",
    ),
    "balanced": ProfileExpectation(
        profile_name="balanced",
        min_f1=0.4,  # Reasonable F1
        description="Good balance of precision and recall",
    ),
    "precision_first": ProfileExpectation(
        profile_name="precision_first",
        min_precision=0.6,  # Should have high precision
        max_recall=0.9,  # Recall may suffer
        should_have_higher_precision_than=["aggressive_recall", "balanced"],
        expected_note_count_factor=(0.3, 1.2),  # Fewer notes than ground truth
        description="Prioritizes precision, accepts lower recall",
    ),
    "live_performance": ProfileExpectation(
        profile_name="live_performance",
        description="Optimized for speed, not accuracy",
    ),
    "clean_midi_export": ProfileExpectation(
        profile_name="clean_midi_export",
        min_precision=0.5,  # Should be reasonably precise
        description="Clean output for DAW import",
    ),
}


@dataclass
class ValidationIssue:
    """A single validation issue."""

    severity: str  # "error", "warning", "info"
    message: str
    metric: str = ""
    actual_value: Optional[float] = None
    expected_value: Optional[float] = None


@dataclass
class ProfileMetrics:
    """Metrics for a single profile."""

    profile_name: str
    f1: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    note_count_ratio: float = 1.0  # extracted / ground_truth
    sample_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_name": self.profile_name,
            "f1": self.f1,
            "precision": self.precision,
            "recall": self.recall,
            "note_count_ratio": self.note_count_ratio,
            "sample_count": self.sample_count,
        }


@dataclass
class ValidationResult:
    """Result of profile validation."""

    profile_name: str
    passed: bool
    issues: List[ValidationIssue] = field(default_factory=list)
    metrics: Optional[ProfileMetrics] = None

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_name": self.profile_name,
            "passed": self.passed,
            "issues": [
                {
                    "severity": i.severity,
                    "message": i.message,
                    "metric": i.metric,
                    "actual_value": i.actual_value,
                    "expected_value": i.expected_value,
                }
                for i in self.issues
            ],
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
        }

    def summary(self) -> str:
        """Generate summary string."""
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"Profile: {self.profile_name} [{status}]",
            f"Errors: {self.error_count}, Warnings: {self.warning_count}",
        ]

        if self.metrics:
            lines.extend([
                f"  F1: {self.metrics.f1:.1%}",
                f"  Precision: {self.metrics.precision:.1%}",
                f"  Recall: {self.metrics.recall:.1%}",
            ])

        if self.issues:
            lines.append("Issues:")
            for issue in self.issues:
                lines.append(f"  [{issue.severity.upper()}] {issue.message}")

        return "\n".join(lines)


class ProfileValidator:
    """Validates extraction profiles against expectations."""

    def __init__(
        self,
        expectations: Optional[Dict[str, ProfileExpectation]] = None,
    ):
        """Initialize validator.

        Args:
            expectations: Profile expectations (uses defaults if not provided)
        """
        self.expectations = expectations or PROFILE_EXPECTATIONS

    def validate(
        self,
        profile_name: str,
        metrics: ProfileMetrics,
        other_profiles: Optional[Dict[str, ProfileMetrics]] = None,
    ) -> ValidationResult:
        """Validate a profile against expectations.

        Args:
            profile_name: Name of profile to validate
            metrics: Metrics from running the profile
            other_profiles: Metrics from other profiles (for comparison)

        Returns:
            ValidationResult
        """
        issues: List[ValidationIssue] = []
        expectation = self.expectations.get(profile_name)

        if expectation is None:
            # No explicit expectations - just check basic sanity
            return self._sanity_check(profile_name, metrics)

        # Check metric bounds
        if metrics.f1 < expectation.min_f1:
            issues.append(ValidationIssue(
                severity="error",
                message=f"F1 ({metrics.f1:.1%}) below minimum ({expectation.min_f1:.1%})",
                metric="f1",
                actual_value=metrics.f1,
                expected_value=expectation.min_f1,
            ))
        if metrics.f1 > expectation.max_f1:
            issues.append(ValidationIssue(
                severity="warning",
                message=f"F1 ({metrics.f1:.1%}) above maximum ({expectation.max_f1:.1%})",
                metric="f1",
                actual_value=metrics.f1,
                expected_value=expectation.max_f1,
            ))

        if metrics.precision < expectation.min_precision:
            issues.append(ValidationIssue(
                severity="error",
                message=f"Precision ({metrics.precision:.1%}) below minimum ({expectation.min_precision:.1%})",
                metric="precision",
                actual_value=metrics.precision,
                expected_value=expectation.min_precision,
            ))
        if metrics.precision > expectation.max_precision:
            issues.append(ValidationIssue(
                severity="warning",
                message=f"Precision ({metrics.precision:.1%}) above maximum ({expectation.max_precision:.1%})",
                metric="precision",
                actual_value=metrics.precision,
                expected_value=expectation.max_precision,
            ))

        if metrics.recall < expectation.min_recall:
            issues.append(ValidationIssue(
                severity="error",
                message=f"Recall ({metrics.recall:.1%}) below minimum ({expectation.min_recall:.1%})",
                metric="recall",
                actual_value=metrics.recall,
                expected_value=expectation.min_recall,
            ))
        if metrics.recall > expectation.max_recall:
            issues.append(ValidationIssue(
                severity="warning",
                message=f"Recall ({metrics.recall:.1%}) above maximum ({expectation.max_recall:.1%})",
                metric="recall",
                actual_value=metrics.recall,
                expected_value=expectation.max_recall,
            ))

        # Check note count ratio
        min_ratio, max_ratio = expectation.expected_note_count_factor
        if metrics.note_count_ratio < min_ratio:
            issues.append(ValidationIssue(
                severity="warning",
                message=f"Note count ratio ({metrics.note_count_ratio:.2f}) below expected ({min_ratio:.2f})",
                metric="note_count_ratio",
                actual_value=metrics.note_count_ratio,
                expected_value=min_ratio,
            ))
        if metrics.note_count_ratio > max_ratio:
            issues.append(ValidationIssue(
                severity="warning",
                message=f"Note count ratio ({metrics.note_count_ratio:.2f}) above expected ({max_ratio:.2f})",
                metric="note_count_ratio",
                actual_value=metrics.note_count_ratio,
                expected_value=max_ratio,
            ))

        # Check relative expectations
        if other_profiles:
            for other_name in expectation.should_have_higher_precision_than:
                other = other_profiles.get(other_name)
                if other and metrics.precision <= other.precision:
                    issues.append(ValidationIssue(
                        severity="error",
                        message=f"Precision ({metrics.precision:.1%}) should be higher than {other_name} ({other.precision:.1%})",
                        metric="precision",
                        actual_value=metrics.precision,
                        expected_value=other.precision,
                    ))

            for other_name in expectation.should_have_higher_recall_than:
                other = other_profiles.get(other_name)
                if other and metrics.recall <= other.recall:
                    issues.append(ValidationIssue(
                        severity="error",
                        message=f"Recall ({metrics.recall:.1%}) should be higher than {other_name} ({other.recall:.1%})",
                        metric="recall",
                        actual_value=metrics.recall,
                        expected_value=other.recall,
                    ))

        passed = all(i.severity != "error" for i in issues)

        return ValidationResult(
            profile_name=profile_name,
            passed=passed,
            issues=issues,
            metrics=metrics,
        )

    def _sanity_check(
        self,
        profile_name: str,
        metrics: ProfileMetrics,
    ) -> ValidationResult:
        """Basic sanity check for profiles without explicit expectations."""
        issues: List[ValidationIssue] = []

        # Check for very low metrics
        if metrics.f1 < 0.1:
            issues.append(ValidationIssue(
                severity="warning",
                message=f"Very low F1 score ({metrics.f1:.1%})",
                metric="f1",
                actual_value=metrics.f1,
            ))

        if metrics.precision < 0.1:
            issues.append(ValidationIssue(
                severity="warning",
                message=f"Very low precision ({metrics.precision:.1%})",
                metric="precision",
                actual_value=metrics.precision,
            ))

        if metrics.recall < 0.1:
            issues.append(ValidationIssue(
                severity="warning",
                message=f"Very low recall ({metrics.recall:.1%})",
                metric="recall",
                actual_value=metrics.recall,
            ))

        return ValidationResult(
            profile_name=profile_name,
            passed=True,  # Sanity check only produces warnings
            issues=issues,
            metrics=metrics,
        )

    def validate_all(
        self,
        profile_metrics: Dict[str, ProfileMetrics],
    ) -> Dict[str, ValidationResult]:
        """Validate all profiles.

        Args:
            profile_metrics: Metrics for each profile

        Returns:
            Dictionary mapping profile name to ValidationResult
        """
        results = {}
        for profile_name, metrics in profile_metrics.items():
            results[profile_name] = self.validate(
                profile_name,
                metrics,
                other_profiles=profile_metrics,
            )
        return results

    def check_differentiation(
        self,
        profile_metrics: Dict[str, ProfileMetrics],
        min_f1_difference: float = 0.05,
    ) -> List[ValidationIssue]:
        """Check that profiles are sufficiently differentiated.

        Profiles should have different characteristics, not be redundant.

        Args:
            profile_metrics: Metrics for each profile
            min_f1_difference: Minimum F1 difference between profiles

        Returns:
            List of differentiation issues
        """
        issues = []
        profiles = list(profile_metrics.items())

        for i, (name_a, metrics_a) in enumerate(profiles):
            for name_b, metrics_b in profiles[i + 1:]:
                f1_diff = abs(metrics_a.f1 - metrics_b.f1)
                prec_diff = abs(metrics_a.precision - metrics_b.precision)
                recall_diff = abs(metrics_a.recall - metrics_b.recall)

                # Check if profiles are too similar
                if f1_diff < min_f1_difference and prec_diff < 0.1 and recall_diff < 0.1:
                    issues.append(ValidationIssue(
                        severity="warning",
                        message=f"Profiles '{name_a}' and '{name_b}' are very similar "
                               f"(F1 diff: {f1_diff:.1%})",
                    ))

        return issues


def validate_profile(
    profile_name: str,
    f1: float,
    precision: float,
    recall: float,
    note_count_ratio: float = 1.0,
    sample_count: int = 0,
) -> ValidationResult:
    """Convenience function to validate a profile.

    Args:
        profile_name: Profile name
        f1: F1 score
        precision: Precision score
        recall: Recall score
        note_count_ratio: Extracted / ground truth note count
        sample_count: Number of samples evaluated

    Returns:
        ValidationResult
    """
    metrics = ProfileMetrics(
        profile_name=profile_name,
        f1=f1,
        precision=precision,
        recall=recall,
        note_count_ratio=note_count_ratio,
        sample_count=sample_count,
    )

    validator = ProfileValidator()
    return validator.validate(profile_name, metrics)
