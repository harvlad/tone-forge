"""Overfit detection for MIDI extraction parameters.

Identifies parameters/passes that help on training data but
hurt on test data - signs of overfitting to specific samples.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class OverfitHeuristic:
    """A potentially overfit parameter or heuristic."""

    name: str  # Parameter/pass name
    train_improvement: float  # Improvement on training set
    test_regression: float  # Regression on test set
    confidence: float  # Confidence in overfit diagnosis (0-1)
    recommendation: str = ""  # Suggested action

    @property
    def is_overfit(self) -> bool:
        """Check if this is likely overfit (helps train, hurts test)."""
        return self.train_improvement > 0.02 and self.test_regression > 0.02

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "train_improvement": self.train_improvement,
            "test_regression": self.test_regression,
            "confidence": self.confidence,
            "recommendation": self.recommendation,
            "is_overfit": self.is_overfit,
        }


@dataclass
class ParameterStability:
    """Stability analysis for a single parameter."""

    parameter_name: str
    values_by_genre: Dict[str, Any]  # Optimal value per genre
    is_stable: bool  # Same optimal across genres
    variance: float = 0.0  # Variance of optimal values
    recommendation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parameter_name": self.parameter_name,
            "values_by_genre": self.values_by_genre,
            "is_stable": self.is_stable,
            "variance": self.variance,
            "recommendation": self.recommendation,
        }


@dataclass
class OverfitReport:
    """Complete overfit analysis report."""

    # Detected overfit heuristics
    overfit_heuristics: List[OverfitHeuristic] = field(default_factory=list)

    # Parameter stability analysis
    parameter_stability: List[ParameterStability] = field(default_factory=list)

    # Summary
    total_parameters_checked: int = 0
    overfit_count: int = 0
    unstable_count: int = 0

    @property
    def has_overfit_issues(self) -> bool:
        """Check if any overfit issues detected."""
        return self.overfit_count > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overfit_heuristics": [h.to_dict() for h in self.overfit_heuristics],
            "parameter_stability": [p.to_dict() for p in self.parameter_stability],
            "summary": {
                "total_parameters_checked": self.total_parameters_checked,
                "overfit_count": self.overfit_count,
                "unstable_count": self.unstable_count,
                "has_overfit_issues": self.has_overfit_issues,
            },
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        status = "OVERFIT DETECTED" if self.has_overfit_issues else "NO OVERFIT"
        lines = [
            f"Overfit Analysis ({status})",
            "=" * 50,
            "",
            f"Parameters checked:  {self.total_parameters_checked}",
            f"Overfit parameters:  {self.overfit_count}",
            f"Unstable parameters: {self.unstable_count}",
        ]

        if self.overfit_heuristics:
            lines.extend(["", "Overfit Heuristics:"])
            for h in sorted(self.overfit_heuristics, key=lambda x: -x.confidence):
                if h.is_overfit:
                    lines.append(
                        f"  {h.name}: train +{h.train_improvement:.1%}, "
                        f"test -{h.test_regression:.1%} (conf: {h.confidence:.0%})"
                    )
                    if h.recommendation:
                        lines.append(f"    -> {h.recommendation}")

        if self.parameter_stability:
            unstable = [p for p in self.parameter_stability if not p.is_stable]
            if unstable:
                lines.extend(["", "Unstable Parameters:"])
                for p in unstable:
                    lines.append(f"  {p.parameter_name}: variance={p.variance:.3f}")
                    if p.recommendation:
                        lines.append(f"    -> {p.recommendation}")

        return "\n".join(lines)


class OverfitDetector:
    """Detects overfitting in extraction parameters."""

    def __init__(
        self,
        train_threshold: float = 0.02,
        test_threshold: float = 0.02,
        stability_threshold: float = 0.1,
    ):
        """Initialize detector.

        Args:
            train_threshold: Min improvement to consider "helps training"
            test_threshold: Min regression to consider "hurts testing"
            stability_threshold: Max variance for "stable" parameter
        """
        self.train_threshold = train_threshold
        self.test_threshold = test_threshold
        self.stability_threshold = stability_threshold

    def detect(
        self,
        baseline_train_metrics: Dict[str, float],
        baseline_test_metrics: Dict[str, float],
        modified_train_metrics: Dict[str, Dict[str, float]],
        modified_test_metrics: Dict[str, Dict[str, float]],
    ) -> OverfitReport:
        """Detect overfit parameters.

        Args:
            baseline_train_metrics: Baseline metrics on training set {"f1": 0.8, ...}
            baseline_test_metrics: Baseline metrics on test set
            modified_train_metrics: Metrics with each modification {"param_name": {"f1": ...}}
            modified_test_metrics: Test metrics with each modification

        Returns:
            OverfitReport
        """
        heuristics: List[OverfitHeuristic] = []

        for param_name in modified_train_metrics.keys():
            train_metrics = modified_train_metrics[param_name]
            test_metrics = modified_test_metrics.get(param_name, {})

            # Calculate improvements/regressions
            train_improvement = train_metrics.get("f1", 0) - baseline_train_metrics.get("f1", 0)
            test_change = test_metrics.get("f1", 0) - baseline_test_metrics.get("f1", 0)

            # If improves train but hurts test, likely overfit
            if train_improvement > self.train_threshold and test_change < -self.test_threshold:
                # Calculate confidence based on magnitude
                confidence = min(1.0, (train_improvement + abs(test_change)) / 0.2)

                recommendation = self._generate_recommendation(
                    param_name, train_improvement, test_change
                )

                heuristics.append(OverfitHeuristic(
                    name=param_name,
                    train_improvement=train_improvement,
                    test_regression=abs(test_change),
                    confidence=confidence,
                    recommendation=recommendation,
                ))

        overfit_count = sum(1 for h in heuristics if h.is_overfit)

        return OverfitReport(
            overfit_heuristics=heuristics,
            total_parameters_checked=len(modified_train_metrics),
            overfit_count=overfit_count,
        )

    def analyze_parameter_stability(
        self,
        optimal_params_by_genre: Dict[str, Dict[str, Any]],
    ) -> List[ParameterStability]:
        """Analyze which parameters are stable across genres.

        Args:
            optimal_params_by_genre: Optimal parameters for each genre

        Returns:
            List of ParameterStability analyses
        """
        if not optimal_params_by_genre:
            return []

        # Get all parameter names
        sample_params = list(optimal_params_by_genre.values())[0]
        param_names = list(sample_params.keys())

        stability_results = []

        for param_name in param_names:
            values = {}
            numeric_values = []

            for genre, params in optimal_params_by_genre.items():
                value = params.get(param_name)
                values[genre] = value

                if isinstance(value, (int, float)):
                    numeric_values.append(value)

            # Calculate stability
            if numeric_values:
                variance = np.var(numeric_values)
                is_stable = variance < self.stability_threshold

                # Generate recommendation
                if not is_stable:
                    mean_val = np.mean(numeric_values)
                    recommendation = f"Consider using genre-specific values (mean: {mean_val:.2f})"
                else:
                    recommendation = "Parameter is stable across genres"
            else:
                variance = 0.0
                unique_values = set(str(v) for v in values.values())
                is_stable = len(unique_values) == 1
                recommendation = "" if is_stable else "Consider genre-specific settings"

            stability_results.append(ParameterStability(
                parameter_name=param_name,
                values_by_genre=values,
                is_stable=is_stable,
                variance=float(variance),
                recommendation=recommendation,
            ))

        return stability_results

    def _generate_recommendation(
        self,
        param_name: str,
        train_improvement: float,
        test_change: float,
    ) -> str:
        """Generate recommendation for overfit parameter."""
        if "threshold" in param_name.lower():
            return "Consider relaxing this threshold for better generalization"
        elif "filter" in param_name.lower():
            return "This filter may be too aggressive - consider making it more permissive"
        elif "cleanup" in param_name.lower() or "correction" in param_name.lower():
            return "This cleanup pass may be removing valid notes - consider disabling"
        else:
            return "Consider removing or tuning this parameter more conservatively"


def detect_overfit(
    baseline_f1_train: float,
    baseline_f1_test: float,
    modifications: Dict[str, Tuple[float, float]],  # param -> (train_f1, test_f1)
) -> OverfitReport:
    """Convenience function for overfit detection.

    Args:
        baseline_f1_train: Baseline F1 on training
        baseline_f1_test: Baseline F1 on testing
        modifications: Parameter name -> (train_f1, test_f1) with that param

    Returns:
        OverfitReport
    """
    baseline_train = {"f1": baseline_f1_train}
    baseline_test = {"f1": baseline_f1_test}

    modified_train = {name: {"f1": f1s[0]} for name, f1s in modifications.items()}
    modified_test = {name: {"f1": f1s[1]} for name, f1s in modifications.items()}

    detector = OverfitDetector()
    return detector.detect(
        baseline_train, baseline_test,
        modified_train, modified_test,
    )


def identify_genre_specific_tuning(
    per_genre_metrics: Dict[str, Dict[str, float]],
    parameter_variations: Dict[str, Dict[str, Dict[str, float]]],
) -> Dict[str, str]:
    """Identify which parameters benefit from genre-specific tuning.

    Args:
        per_genre_metrics: Baseline metrics per genre
        parameter_variations: For each parameter, metrics by genre with different values

    Returns:
        Dictionary mapping parameter to recommendation
    """
    recommendations = {}

    for param_name, genre_metrics in parameter_variations.items():
        # Check if there's significant variation in optimal values
        optimal_by_genre = {}

        for genre, metrics_by_value in genre_metrics.items():
            # Find best value for this genre
            if isinstance(metrics_by_value, dict):
                best_value = max(metrics_by_value.items(), key=lambda x: x[1].get("f1", 0))
                optimal_by_genre[genre] = best_value[0]

        if optimal_by_genre:
            unique_optima = set(str(v) for v in optimal_by_genre.values())
            if len(unique_optima) > 1:
                recommendations[param_name] = (
                    f"Genre-specific tuning recommended. "
                    f"Optimal values vary: {optimal_by_genre}"
                )
            else:
                recommendations[param_name] = (
                    f"Stable across genres. Use value: {list(optimal_by_genre.values())[0]}"
                )

    return recommendations
