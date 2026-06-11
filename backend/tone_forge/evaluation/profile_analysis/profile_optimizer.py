"""Profile optimization for MIDI extraction.

Provides grid search and parameter sensitivity analysis:
- Find optimal parameter combinations
- Analyze sensitivity of metrics to parameters
- Target-metric optimization (F1, precision, recall)
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class OptimizationConfig:
    """Configuration for profile optimization."""

    # Target metric to optimize
    target_metric: str = "f1"  # "f1", "precision", "recall"

    # Parameter search spaces (name -> list of values to try)
    parameter_grid: Dict[str, List[Any]] = field(default_factory=dict)

    # Constraints
    min_precision: float = 0.0  # Reject configs below this precision
    min_recall: float = 0.0  # Reject configs below this recall
    min_f1: float = 0.0  # Reject configs below this F1

    # Search settings
    max_iterations: Optional[int] = None  # Limit iterations (None = exhaustive)
    random_seed: Optional[int] = None  # For random sampling

    @classmethod
    def default_onset_grid(cls) -> "OptimizationConfig":
        """Create config for optimizing onset/frame thresholds."""
        return cls(
            target_metric="f1",
            parameter_grid={
                "onset_threshold": [0.3, 0.4, 0.5, 0.6, 0.7],
                "frame_threshold": [0.2, 0.3, 0.4, 0.5],
            },
        )

    @classmethod
    def default_full_grid(cls) -> "OptimizationConfig":
        """Create config for full parameter optimization."""
        return cls(
            target_metric="f1",
            parameter_grid={
                "onset_threshold": [0.3, 0.5, 0.7],
                "frame_threshold": [0.2, 0.4],
                "min_note_ms": [30, 50, 80],
                "quantize_strength": [0.3, 0.5, 0.7],
                "key_filter_strictness": [0.3, 0.5, 0.7],
            },
        )


@dataclass
class ParameterConfig:
    """A single parameter configuration."""

    parameters: Dict[str, Any]

    def __hash__(self):
        return hash(tuple(sorted(self.parameters.items())))

    def __eq__(self, other):
        return self.parameters == other.parameters


@dataclass
class EvaluationResult:
    """Result from evaluating a parameter configuration."""

    config: ParameterConfig
    f1: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    note_count_ratio: float = 1.0
    execution_time_ms: float = 0.0

    def get_metric(self, metric: str) -> float:
        """Get metric by name."""
        if metric == "f1":
            return self.f1
        elif metric == "precision":
            return self.precision
        elif metric == "recall":
            return self.recall
        else:
            return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parameters": self.config.parameters,
            "f1": self.f1,
            "precision": self.precision,
            "recall": self.recall,
            "note_count_ratio": self.note_count_ratio,
            "execution_time_ms": self.execution_time_ms,
        }


@dataclass
class OptimizationResult:
    """Result of profile optimization."""

    best_config: ParameterConfig
    best_metrics: EvaluationResult
    all_results: List[EvaluationResult] = field(default_factory=list)
    target_metric: str = "f1"
    total_iterations: int = 0
    valid_iterations: int = 0  # Configs that met constraints

    def to_dict(self) -> Dict[str, Any]:
        return {
            "best_parameters": self.best_config.parameters,
            "best_f1": self.best_metrics.f1,
            "best_precision": self.best_metrics.precision,
            "best_recall": self.best_metrics.recall,
            "target_metric": self.target_metric,
            "total_iterations": self.total_iterations,
            "valid_iterations": self.valid_iterations,
            "all_results": [r.to_dict() for r in self.all_results],
        }

    def summary(self) -> str:
        """Generate summary string."""
        lines = [
            f"Optimization Result (target: {self.target_metric})",
            "=" * 50,
            f"Iterations: {self.valid_iterations}/{self.total_iterations} valid",
            "",
            "Best Configuration:",
        ]

        for param, value in self.best_config.parameters.items():
            lines.append(f"  {param}: {value}")

        lines.extend([
            "",
            "Best Metrics:",
            f"  F1:        {self.best_metrics.f1:.1%}",
            f"  Precision: {self.best_metrics.precision:.1%}",
            f"  Recall:    {self.best_metrics.recall:.1%}",
        ])

        return "\n".join(lines)

    def get_top_configs(self, n: int = 5) -> List[EvaluationResult]:
        """Get top N configurations by target metric."""
        sorted_results = sorted(
            self.all_results,
            key=lambda r: r.get_metric(self.target_metric),
            reverse=True,
        )
        return sorted_results[:n]


class ProfileOptimizer:
    """Grid search optimizer for extraction profiles."""

    def __init__(
        self,
        evaluator: Callable[[Dict[str, Any]], EvaluationResult],
        config: Optional[OptimizationConfig] = None,
    ):
        """Initialize optimizer.

        Args:
            evaluator: Function that evaluates a parameter dict and returns metrics
            config: Optimization configuration
        """
        self.evaluator = evaluator
        self.config = config or OptimizationConfig()

    def optimize(
        self,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> OptimizationResult:
        """Run grid search optimization.

        Args:
            progress_callback: Optional callback(current, total)

        Returns:
            OptimizationResult
        """
        # Generate all parameter combinations
        param_names = list(self.config.parameter_grid.keys())
        param_values = list(self.config.parameter_grid.values())

        all_combinations = list(itertools.product(*param_values))
        total_combinations = len(all_combinations)

        # Limit if specified
        if self.config.max_iterations and self.config.max_iterations < total_combinations:
            if self.config.random_seed is not None:
                np.random.seed(self.config.random_seed)
            indices = np.random.choice(
                total_combinations,
                size=self.config.max_iterations,
                replace=False,
            )
            all_combinations = [all_combinations[i] for i in indices]

        logger.info(f"Starting optimization with {len(all_combinations)} configurations")

        all_results: List[EvaluationResult] = []
        best_result: Optional[EvaluationResult] = None
        best_metric_value = -float('inf')
        valid_count = 0

        for i, combo in enumerate(all_combinations):
            if progress_callback:
                progress_callback(i, len(all_combinations))

            # Create parameter dict
            params = dict(zip(param_names, combo))
            config = ParameterConfig(parameters=params)

            try:
                # Evaluate
                result = self.evaluator(params)
                result.config = config
                all_results.append(result)

                # Check constraints
                meets_constraints = (
                    result.precision >= self.config.min_precision and
                    result.recall >= self.config.min_recall and
                    result.f1 >= self.config.min_f1
                )

                if meets_constraints:
                    valid_count += 1
                    metric_value = result.get_metric(self.config.target_metric)

                    if metric_value > best_metric_value:
                        best_metric_value = metric_value
                        best_result = result

            except Exception as e:
                logger.warning(f"Evaluation failed for {params}: {e}")

        # Handle case with no valid results
        if best_result is None:
            if all_results:
                best_result = max(
                    all_results,
                    key=lambda r: r.get_metric(self.config.target_metric)
                )
            else:
                # No results at all - return empty
                return OptimizationResult(
                    best_config=ParameterConfig(parameters={}),
                    best_metrics=EvaluationResult(
                        config=ParameterConfig(parameters={}),
                    ),
                    all_results=[],
                    target_metric=self.config.target_metric,
                    total_iterations=len(all_combinations),
                    valid_iterations=0,
                )

        return OptimizationResult(
            best_config=best_result.config,
            best_metrics=best_result,
            all_results=all_results,
            target_metric=self.config.target_metric,
            total_iterations=len(all_combinations),
            valid_iterations=valid_count,
        )


@dataclass
class ParameterSensitivity:
    """Sensitivity analysis for a single parameter."""

    parameter_name: str
    values_tested: List[Any]
    f1_by_value: Dict[Any, float]
    precision_by_value: Dict[Any, float]
    recall_by_value: Dict[Any, float]

    @property
    def f1_range(self) -> float:
        """Range of F1 values across parameter settings."""
        values = list(self.f1_by_value.values())
        return max(values) - min(values) if values else 0.0

    @property
    def is_sensitive(self) -> bool:
        """Check if parameter has significant impact (>5% F1 range)."""
        return self.f1_range > 0.05

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parameter_name": self.parameter_name,
            "values_tested": self.values_tested,
            "f1_by_value": {str(k): v for k, v in self.f1_by_value.items()},
            "precision_by_value": {str(k): v for k, v in self.precision_by_value.items()},
            "recall_by_value": {str(k): v for k, v in self.recall_by_value.items()},
            "f1_range": self.f1_range,
            "is_sensitive": self.is_sensitive,
        }


def analyze_parameter_sensitivity(
    optimization_result: OptimizationResult,
) -> Dict[str, ParameterSensitivity]:
    """Analyze how sensitive metrics are to each parameter.

    Args:
        optimization_result: Result from grid search optimization

    Returns:
        Dictionary mapping parameter name to sensitivity analysis
    """
    if not optimization_result.all_results:
        return {}

    # Get all parameter names
    sample_params = optimization_result.all_results[0].config.parameters
    param_names = list(sample_params.keys())

    sensitivities = {}

    for param_name in param_names:
        # Group results by this parameter value
        by_value: Dict[Any, List[EvaluationResult]] = {}

        for result in optimization_result.all_results:
            value = result.config.parameters.get(param_name)
            if value not in by_value:
                by_value[value] = []
            by_value[value].append(result)

        # Compute average metrics for each value
        f1_by_value = {}
        precision_by_value = {}
        recall_by_value = {}

        for value, results in by_value.items():
            f1_by_value[value] = np.mean([r.f1 for r in results])
            precision_by_value[value] = np.mean([r.precision for r in results])
            recall_by_value[value] = np.mean([r.recall for r in results])

        sensitivities[param_name] = ParameterSensitivity(
            parameter_name=param_name,
            values_tested=list(by_value.keys()),
            f1_by_value=f1_by_value,
            precision_by_value=precision_by_value,
            recall_by_value=recall_by_value,
        )

    return sensitivities


def plot_parameter_sensitivity(
    sensitivity: ParameterSensitivity,
    output_path: Optional[Path] = None,
    figsize: Tuple[int, int] = (10, 6),
) -> Any:
    """Plot parameter sensitivity.

    Args:
        sensitivity: ParameterSensitivity to visualize
        output_path: Optional output path
        figsize: Figure size

    Returns:
        matplotlib figure
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available")
        return None

    fig, ax = plt.subplots(figsize=figsize)

    values = sensitivity.values_tested
    x = range(len(values))

    f1_vals = [sensitivity.f1_by_value.get(v, 0) for v in values]
    prec_vals = [sensitivity.precision_by_value.get(v, 0) for v in values]
    recall_vals = [sensitivity.recall_by_value.get(v, 0) for v in values]

    ax.plot(x, f1_vals, 'b-o', linewidth=2, markersize=8, label='F1')
    ax.plot(x, prec_vals, 'g--s', linewidth=2, markersize=6, label='Precision')
    ax.plot(x, recall_vals, 'r--^', linewidth=2, markersize=6, label='Recall')

    ax.set_xticks(x)
    ax.set_xticklabels([str(v) for v in values])
    ax.set_xlabel(sensitivity.parameter_name)
    ax.set_ylabel('Score')
    ax.legend()
    ax.grid(True, alpha=0.3)

    status = "Sensitive" if sensitivity.is_sensitive else "Not Sensitive"
    ax.set_title(f'Parameter Sensitivity: {sensitivity.parameter_name} ({status})')

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved sensitivity plot to {output_path}")

    return fig


def compare_profiles_grid(
    profiles: Dict[str, Dict[str, Any]],
    evaluator: Callable[[Dict[str, Any]], EvaluationResult],
) -> Dict[str, EvaluationResult]:
    """Evaluate multiple profile configurations.

    Args:
        profiles: Dictionary mapping profile name to parameters
        evaluator: Evaluation function

    Returns:
        Dictionary mapping profile name to evaluation result
    """
    results = {}
    for name, params in profiles.items():
        try:
            result = evaluator(params)
            result.config = ParameterConfig(parameters=params)
            results[name] = result
        except Exception as e:
            logger.warning(f"Evaluation failed for profile {name}: {e}")
    return results
