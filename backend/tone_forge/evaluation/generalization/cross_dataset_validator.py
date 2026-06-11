"""Cross-dataset validation for generalization testing.

Implements leave-one-out validation to measure how well
extraction generalizes across different genres/datasets.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class DatasetMetrics:
    """Metrics for a single dataset/genre."""

    name: str
    f1: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    sample_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "f1": self.f1,
            "precision": self.precision,
            "recall": self.recall,
            "sample_count": self.sample_count,
        }


@dataclass
class LeaveOneOutResult:
    """Result from leave-one-out validation."""

    held_out_genre: str

    # Metrics on training set (all genres except held-out)
    train_f1: float = 0.0
    train_precision: float = 0.0
    train_recall: float = 0.0
    train_sample_count: int = 0

    # Metrics on held-out genre (test set)
    test_f1: float = 0.0
    test_precision: float = 0.0
    test_recall: float = 0.0
    test_sample_count: int = 0

    # Generalization gap (train - test)
    @property
    def f1_gap(self) -> float:
        """Generalization gap for F1."""
        return self.train_f1 - self.test_f1

    @property
    def precision_gap(self) -> float:
        """Generalization gap for precision."""
        return self.train_precision - self.test_precision

    @property
    def recall_gap(self) -> float:
        """Generalization gap for recall."""
        return self.train_recall - self.test_recall

    def to_dict(self) -> Dict[str, Any]:
        return {
            "held_out_genre": self.held_out_genre,
            "train": {
                "f1": self.train_f1,
                "precision": self.train_precision,
                "recall": self.train_recall,
                "sample_count": self.train_sample_count,
            },
            "test": {
                "f1": self.test_f1,
                "precision": self.test_precision,
                "recall": self.test_recall,
                "sample_count": self.test_sample_count,
            },
            "gaps": {
                "f1": self.f1_gap,
                "precision": self.precision_gap,
                "recall": self.recall_gap,
            },
        }


@dataclass
class GeneralizationGap:
    """Summary of generalization gaps across leave-one-out experiments."""

    # Per-genre results
    per_genre_results: Dict[str, LeaveOneOutResult] = field(default_factory=dict)

    # Aggregate statistics
    mean_f1_gap: float = 0.0
    std_f1_gap: float = 0.0
    max_f1_gap: float = 0.0
    worst_genre: str = ""

    # Is the model generalizing well?
    @property
    def is_generalizing(self) -> bool:
        """Check if average gap is acceptable (<10%)."""
        return self.mean_f1_gap < 0.1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "per_genre_results": {
                k: v.to_dict() for k, v in self.per_genre_results.items()
            },
            "aggregate": {
                "mean_f1_gap": self.mean_f1_gap,
                "std_f1_gap": self.std_f1_gap,
                "max_f1_gap": self.max_f1_gap,
                "worst_genre": self.worst_genre,
                "is_generalizing": self.is_generalizing,
            },
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        status = "GENERALIZING WELL" if self.is_generalizing else "OVERFITTING"
        lines = [
            f"Generalization Analysis ({status})",
            "=" * 50,
            "",
            f"Mean F1 Gap:  {self.mean_f1_gap:.1%} (std: {self.std_f1_gap:.1%})",
            f"Max F1 Gap:   {self.max_f1_gap:.1%}",
            f"Worst Genre:  {self.worst_genre}",
            "",
            "Per-Genre Breakdown:",
        ]

        for genre, result in sorted(
            self.per_genre_results.items(),
            key=lambda x: x[1].f1_gap,
            reverse=True,
        ):
            gap_indicator = "!!" if result.f1_gap > 0.1 else ""
            lines.append(
                f"  {genre:15s}: gap={result.f1_gap:+.1%} "
                f"(train={result.train_f1:.1%}, test={result.test_f1:.1%}) {gap_indicator}"
            )

        return "\n".join(lines)


class CrossDatasetValidator:
    """Validates extraction generalization across datasets/genres."""

    def __init__(
        self,
        evaluator: Callable[[List[str]], Dict[str, DatasetMetrics]],
    ):
        """Initialize validator.

        Args:
            evaluator: Function that takes list of sample IDs and returns
                      dictionary mapping dataset/genre to metrics
        """
        self.evaluator = evaluator

    def run_leave_one_out(
        self,
        samples_by_genre: Dict[str, List[str]],
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> GeneralizationGap:
        """Run leave-one-genre-out cross-validation.

        Args:
            samples_by_genre: Dictionary mapping genre to list of sample IDs
            progress_callback: Optional callback with held-out genre name

        Returns:
            GeneralizationGap summary
        """
        genres = list(samples_by_genre.keys())
        results: Dict[str, LeaveOneOutResult] = {}

        for held_out in genres:
            if progress_callback:
                progress_callback(held_out)

            logger.info(f"Leave-one-out: holding out {held_out}")

            # Get training samples (all except held-out)
            train_samples = []
            for genre, samples in samples_by_genre.items():
                if genre != held_out:
                    train_samples.extend(samples)

            # Get test samples (held-out genre)
            test_samples = samples_by_genre[held_out]

            # Evaluate
            try:
                train_metrics = self.evaluator(train_samples)
                test_metrics = self.evaluator(test_samples)

                # Aggregate train metrics
                train_f1 = np.mean([m.f1 for m in train_metrics.values()])
                train_prec = np.mean([m.precision for m in train_metrics.values()])
                train_recall = np.mean([m.recall for m in train_metrics.values()])
                train_count = sum(m.sample_count for m in train_metrics.values())

                # Get test metrics (should be single genre)
                test_m = test_metrics.get(held_out, DatasetMetrics(name=held_out))

                results[held_out] = LeaveOneOutResult(
                    held_out_genre=held_out,
                    train_f1=train_f1,
                    train_precision=train_prec,
                    train_recall=train_recall,
                    train_sample_count=train_count,
                    test_f1=test_m.f1,
                    test_precision=test_m.precision,
                    test_recall=test_m.recall,
                    test_sample_count=test_m.sample_count,
                )

            except Exception as e:
                logger.error(f"Failed to evaluate with {held_out} held out: {e}")

        # Compute aggregate statistics
        if results:
            gaps = [r.f1_gap for r in results.values()]
            mean_gap = np.mean(gaps)
            std_gap = np.std(gaps)
            max_gap = max(gaps)
            worst = max(results.items(), key=lambda x: x[1].f1_gap)
            worst_genre = worst[0]
        else:
            mean_gap = std_gap = max_gap = 0.0
            worst_genre = ""

        return GeneralizationGap(
            per_genre_results=results,
            mean_f1_gap=mean_gap,
            std_f1_gap=std_gap,
            max_f1_gap=max_gap,
            worst_genre=worst_genre,
        )

    def validate_new_genre(
        self,
        existing_genres_samples: Dict[str, List[str]],
        new_genre_samples: List[str],
        new_genre_name: str,
    ) -> LeaveOneOutResult:
        """Validate how well extraction generalizes to a new genre.

        Args:
            existing_genres_samples: Samples from existing genres
            new_genre_samples: Samples from new genre to test
            new_genre_name: Name of new genre

        Returns:
            LeaveOneOutResult for the new genre
        """
        # Train on existing genres
        train_samples = []
        for samples in existing_genres_samples.values():
            train_samples.extend(samples)

        train_metrics = self.evaluator(train_samples)
        test_metrics = self.evaluator(new_genre_samples)

        train_f1 = np.mean([m.f1 for m in train_metrics.values()])
        train_prec = np.mean([m.precision for m in train_metrics.values()])
        train_recall = np.mean([m.recall for m in train_metrics.values()])
        train_count = sum(m.sample_count for m in train_metrics.values())

        test_m = test_metrics.get(new_genre_name, DatasetMetrics(name=new_genre_name))

        return LeaveOneOutResult(
            held_out_genre=new_genre_name,
            train_f1=train_f1,
            train_precision=train_prec,
            train_recall=train_recall,
            train_sample_count=train_count,
            test_f1=test_m.f1,
            test_precision=test_m.precision,
            test_recall=test_m.recall,
            test_sample_count=test_m.sample_count,
        )


def run_leave_one_out(
    samples_by_genre: Dict[str, List[str]],
    evaluator: Callable[[List[str]], Dict[str, DatasetMetrics]],
) -> GeneralizationGap:
    """Convenience function to run leave-one-out validation.

    Args:
        samples_by_genre: Dictionary mapping genre to sample IDs
        evaluator: Evaluation function

    Returns:
        GeneralizationGap summary
    """
    validator = CrossDatasetValidator(evaluator)
    return validator.run_leave_one_out(samples_by_genre)


def plot_generalization_gaps(
    gap: GeneralizationGap,
    output_path: Optional[str] = None,
    figsize: Tuple[int, int] = (12, 6),
) -> Any:
    """Plot generalization gaps by genre.

    Args:
        gap: GeneralizationGap to visualize
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

    from pathlib import Path

    fig, ax = plt.subplots(figsize=figsize)

    genres = list(gap.per_genre_results.keys())
    train_f1 = [gap.per_genre_results[g].train_f1 for g in genres]
    test_f1 = [gap.per_genre_results[g].test_f1 for g in genres]
    gaps = [gap.per_genre_results[g].f1_gap for g in genres]

    x = np.arange(len(genres))
    width = 0.35

    bars1 = ax.bar(x - width/2, train_f1, width, label='Train F1', color='steelblue', alpha=0.8)
    bars2 = ax.bar(x + width/2, test_f1, width, label='Test F1', color='coral', alpha=0.8)

    # Add gap indicators
    for i, (x_pos, g) in enumerate(zip(x, gaps)):
        color = 'red' if g > 0.1 else 'green'
        ax.annotate(
            f'{g:+.1%}',
            xy=(x_pos, max(train_f1[i], test_f1[i]) + 0.02),
            ha='center',
            fontsize=9,
            color=color,
        )

    ax.set_ylabel('F1 Score')
    ax.set_xlabel('Genre')
    ax.set_title('Generalization: Train vs Test F1 by Held-Out Genre')
    ax.set_xticks(x)
    ax.set_xticklabels(genres, rotation=45, ha='right')
    ax.legend()
    ax.set_ylim(0, 1.1)

    # Add threshold line
    ax.axhline(y=gap.mean_f1_gap, color='red', linestyle='--',
               alpha=0.5, label=f'Mean gap: {gap.mean_f1_gap:.1%}')

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved generalization plot to {output_path}")

    return fig
