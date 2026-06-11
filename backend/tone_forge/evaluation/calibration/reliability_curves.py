"""Reliability curve visualization for calibration analysis.

Provides:
- Reliability diagrams (accuracy vs confidence)
- Perfect calibration reference line
- Per-pipeline comparison plots
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .calibration_analyzer import CalibrationAnalysis, CalibrationBucket

logger = logging.getLogger(__name__)


@dataclass
class ReliabilityCurve:
    """Data for a reliability curve."""

    name: str
    confidences: List[float]  # Bin centers
    accuracies: List[float]  # Actual accuracies
    counts: List[int]  # Sample counts per bin
    ece: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "confidences": self.confidences,
            "accuracies": self.accuracies,
            "counts": self.counts,
            "ece": self.ece,
        }


def extract_reliability_curve(
    analysis: CalibrationAnalysis,
    name: str = "default",
    min_count: int = 5,
) -> ReliabilityCurve:
    """Extract reliability curve data from calibration analysis.

    Args:
        analysis: CalibrationAnalysis
        name: Curve name
        min_count: Minimum bucket count to include

    Returns:
        ReliabilityCurve
    """
    confidences = []
    accuracies = []
    counts = []

    for bucket in analysis.buckets:
        if bucket.note_count >= min_count:
            confidences.append(bucket.expected_accuracy)
            accuracies.append(bucket.accuracy)
            counts.append(bucket.note_count)

    return ReliabilityCurve(
        name=name,
        confidences=confidences,
        accuracies=accuracies,
        counts=counts,
        ece=analysis.expected_calibration_error,
    )


def plot_reliability_curve(
    analysis: CalibrationAnalysis,
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    figsize: Tuple[int, int] = (10, 8),
    show_histogram: bool = True,
    show_gap: bool = True,
) -> Any:
    """Plot reliability diagram for calibration analysis.

    Args:
        analysis: CalibrationAnalysis to visualize
        output_path: Optional path to save figure
        title: Optional title
        figsize: Figure size
        show_histogram: Whether to show confidence histogram
        show_gap: Whether to shade the gap between actual and expected

    Returns:
        matplotlib figure
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available")
        return None

    # Create figure
    if show_histogram:
        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=figsize,
            gridspec_kw={'height_ratios': [3, 1]},
            sharex=True
        )
    else:
        fig, ax1 = plt.subplots(figsize=figsize)
        ax2 = None

    # Extract data
    confidences = []
    accuracies = []
    counts = []

    for bucket in analysis.buckets:
        if bucket.note_count > 0:
            confidences.append(bucket.expected_accuracy)
            accuracies.append(bucket.accuracy)
            counts.append(bucket.note_count)

    # Plot perfect calibration line
    ax1.plot([0, 1], [0, 1], 'k--', linewidth=2, label='Perfect calibration')

    # Plot actual calibration
    if confidences:
        ax1.plot(confidences, accuracies, 'b-o', linewidth=2, markersize=8, label='Model')

        # Shade gap
        if show_gap:
            for conf, acc in zip(confidences, accuracies):
                if acc < conf:
                    ax1.fill_between([conf - 0.04, conf + 0.04], [acc], [conf],
                                    color='red', alpha=0.2)
                else:
                    ax1.fill_between([conf - 0.04, conf + 0.04], [conf], [acc],
                                    color='blue', alpha=0.2)

    ax1.set_ylabel('Accuracy (Fraction of Positives)')
    ax1.set_xlim(0, 1)
    ax1.set_ylim(0, 1)
    ax1.legend(loc='lower right')
    ax1.grid(True, alpha=0.3)

    # Add ECE annotation
    ax1.text(
        0.05, 0.95,
        f'ECE: {analysis.expected_calibration_error:.3f}\n'
        f'MCE: {analysis.maximum_calibration_error:.3f}\n'
        f'N: {analysis.total_notes}',
        transform=ax1.transAxes,
        fontsize=10,
        verticalalignment='top',
        bbox=dict(boxstyle='round', facecolor='white', alpha=0.8)
    )

    if title:
        ax1.set_title(title)
    else:
        status = "Well Calibrated" if analysis.is_well_calibrated else "Needs Calibration"
        ax1.set_title(f'Reliability Diagram ({status})')

    # Histogram of confidence scores
    if ax2 is not None and counts:
        bar_width = 0.08
        ax2.bar(confidences, counts, width=bar_width, color='steelblue', alpha=0.7)
        ax2.set_xlabel('Mean Predicted Confidence')
        ax2.set_ylabel('Count')
        ax2.set_xlim(0, 1)
        ax2.grid(True, alpha=0.3)

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved reliability curve to {output_path}")

    return fig


def plot_calibration_comparison(
    analyses: Dict[str, CalibrationAnalysis],
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    figsize: Tuple[int, int] = (12, 8),
) -> Any:
    """Plot reliability curves for multiple analyses (comparison).

    Args:
        analyses: Dictionary mapping name to CalibrationAnalysis
        output_path: Optional output path
        title: Optional title
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

    # Color palette
    colors = plt.cm.tab10.colors

    # Plot perfect calibration
    ax.plot([0, 1], [0, 1], 'k--', linewidth=2, label='Perfect')

    # Plot each analysis
    for i, (name, analysis) in enumerate(analyses.items()):
        confidences = []
        accuracies = []

        for bucket in analysis.buckets:
            if bucket.note_count > 0:
                confidences.append(bucket.expected_accuracy)
                accuracies.append(bucket.accuracy)

        if confidences:
            color = colors[i % len(colors)]
            ax.plot(
                confidences, accuracies,
                '-o', linewidth=2, markersize=6, color=color,
                label=f'{name} (ECE={analysis.expected_calibration_error:.3f})'
            )

    ax.set_xlabel('Mean Predicted Confidence')
    ax.set_ylabel('Fraction of Positives')
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(loc='lower right')
    ax.grid(True, alpha=0.3)

    if title:
        ax.set_title(title)
    else:
        ax.set_title('Calibration Comparison')

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved calibration comparison to {output_path}")

    return fig


def plot_per_pipeline_calibration(
    analysis: CalibrationAnalysis,
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    figsize: Tuple[int, int] = (12, 6),
) -> Any:
    """Plot calibration breakdown by pipeline.

    Args:
        analysis: CalibrationAnalysis with per_pipeline data
        output_path: Optional output path
        title: Optional title
        figsize: Figure size

    Returns:
        matplotlib figure
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available")
        return None

    if not analysis.per_pipeline_ece:
        logger.warning("No per-pipeline data to plot")
        return None

    fig, ax = plt.subplots(figsize=figsize)

    pipelines = list(analysis.per_pipeline_ece.keys())
    eces = [analysis.per_pipeline_ece[p] for p in pipelines]

    # Sort by ECE
    sorted_indices = np.argsort(eces)
    pipelines = [pipelines[i] for i in sorted_indices]
    eces = [eces[i] for i in sorted_indices]

    # Color based on ECE
    colors = ['green' if e < 0.1 else 'orange' if e < 0.15 else 'red' for e in eces]

    bars = ax.barh(pipelines, eces, color=colors, alpha=0.8)

    # Add threshold line
    ax.axvline(x=0.1, color='red', linestyle='--', linewidth=1.5, label='ECE threshold (0.1)')

    ax.set_xlabel('Expected Calibration Error (ECE)')
    ax.set_ylabel('Pipeline')
    ax.legend()
    ax.grid(True, alpha=0.3, axis='x')

    # Add value labels
    for bar, ece in zip(bars, eces):
        ax.text(
            bar.get_width() + 0.005, bar.get_y() + bar.get_height() / 2,
            f'{ece:.3f}',
            va='center', fontsize=9
        )

    if title:
        ax.set_title(title)
    else:
        ax.set_title('Calibration by Pipeline')

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved per-pipeline calibration to {output_path}")

    return fig


def plot_calibration_over_time(
    confidences: List[float],
    labels: List[bool],
    timestamps: List[float],
    window_size: float = 5.0,
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    figsize: Tuple[int, int] = (12, 6),
) -> Any:
    """Plot calibration quality over time (rolling window).

    Args:
        confidences: Confidence scores
        labels: Correctness labels
        timestamps: Note onset times
        window_size: Rolling window size in seconds
        output_path: Optional output path
        title: Optional title
        figsize: Figure size

    Returns:
        matplotlib figure
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available")
        return None

    if not confidences or len(confidences) != len(labels) != len(timestamps):
        return None

    # Sort by timestamp
    sorted_idx = np.argsort(timestamps)
    conf_arr = np.array([confidences[i] for i in sorted_idx])
    label_arr = np.array([1.0 if labels[i] else 0.0 for i in sorted_idx])
    time_arr = np.array([timestamps[i] for i in sorted_idx])

    # Compute rolling metrics
    rolling_ece = []
    rolling_times = []

    t = time_arr[0]
    max_t = time_arr[-1]

    while t + window_size <= max_t:
        mask = (time_arr >= t) & (time_arr < t + window_size)
        window_conf = conf_arr[mask]
        window_labels = label_arr[mask]

        if len(window_conf) >= 10:
            # Simple ECE approximation for window
            mean_conf = np.mean(window_conf)
            mean_acc = np.mean(window_labels)
            window_ece = abs(mean_conf - mean_acc)

            rolling_ece.append(window_ece)
            rolling_times.append(t + window_size / 2)

        t += window_size / 2  # 50% overlap

    fig, ax = plt.subplots(figsize=figsize)

    ax.plot(rolling_times, rolling_ece, 'b-', linewidth=2)
    ax.axhline(y=0.1, color='red', linestyle='--', label='Target ECE (0.1)')

    ax.set_xlabel('Time (seconds)')
    ax.set_ylabel('Approximate ECE')
    ax.legend()
    ax.grid(True, alpha=0.3)

    if title:
        ax.set_title(title)
    else:
        ax.set_title(f'Calibration Over Time (window={window_size}s)')

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved calibration over time to {output_path}")

    return fig
