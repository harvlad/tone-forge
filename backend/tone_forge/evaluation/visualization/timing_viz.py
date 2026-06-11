"""Timing visualization for MIDI extraction analysis.

Provides:
- Onset timing error histograms
- Offset timing error histograms
- Timing scatter plots
- Quantization damage detection
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .heatmaps import Note, match_notes

logger = logging.getLogger(__name__)


@dataclass
class TimingAnalysis:
    """Analysis of timing accuracy."""

    # Onset timing errors (extracted - ground_truth) in seconds
    onset_errors: List[float] = field(default_factory=list)

    # Offset timing errors in seconds
    offset_errors: List[float] = field(default_factory=list)

    # Duration errors (as ratio: extracted/ground_truth)
    duration_ratios: List[float] = field(default_factory=list)

    # Per-note data for scatter plots
    note_data: List[Dict[str, float]] = field(default_factory=list)

    # Summary statistics
    @property
    def onset_mean(self) -> float:
        return float(np.mean(self.onset_errors)) if self.onset_errors else 0.0

    @property
    def onset_std(self) -> float:
        return float(np.std(self.onset_errors)) if self.onset_errors else 0.0

    @property
    def onset_median(self) -> float:
        return float(np.median(self.onset_errors)) if self.onset_errors else 0.0

    @property
    def offset_mean(self) -> float:
        return float(np.mean(self.offset_errors)) if self.offset_errors else 0.0

    @property
    def offset_std(self) -> float:
        return float(np.std(self.offset_errors)) if self.offset_errors else 0.0

    @property
    def offset_median(self) -> float:
        return float(np.median(self.offset_errors)) if self.offset_errors else 0.0

    @property
    def num_notes(self) -> int:
        return len(self.onset_errors)

    def get_percentiles(
        self,
        error_type: str = "onset",
    ) -> Dict[str, float]:
        """Get percentile statistics for timing errors.

        Args:
            error_type: "onset" or "offset"

        Returns:
            Dictionary with percentile values
        """
        errors = self.onset_errors if error_type == "onset" else self.offset_errors
        if not errors:
            return {}

        arr = np.array(errors)
        return {
            "p5": float(np.percentile(arr, 5)),
            "p25": float(np.percentile(arr, 25)),
            "p50": float(np.percentile(arr, 50)),
            "p75": float(np.percentile(arr, 75)),
            "p95": float(np.percentile(arr, 95)),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "onset_errors": self.onset_errors,
            "offset_errors": self.offset_errors,
            "duration_ratios": self.duration_ratios,
            "stats": {
                "onset_mean_ms": self.onset_mean * 1000,
                "onset_std_ms": self.onset_std * 1000,
                "onset_median_ms": self.onset_median * 1000,
                "offset_mean_ms": self.offset_mean * 1000,
                "offset_std_ms": self.offset_std * 1000,
                "offset_median_ms": self.offset_median * 1000,
                "num_notes": self.num_notes,
            },
            "onset_percentiles": self.get_percentiles("onset"),
            "offset_percentiles": self.get_percentiles("offset"),
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            "Timing Analysis",
            "=" * 40,
            f"Matched notes: {self.num_notes}",
            "",
            "Onset Timing Errors:",
            f"  Mean:   {self.onset_mean * 1000:+.1f} ms",
            f"  Std:    {self.onset_std * 1000:.1f} ms",
            f"  Median: {self.onset_median * 1000:+.1f} ms",
            "",
            "Offset Timing Errors:",
            f"  Mean:   {self.offset_mean * 1000:+.1f} ms",
            f"  Std:    {self.offset_std * 1000:.1f} ms",
            f"  Median: {self.offset_median * 1000:+.1f} ms",
        ]

        # Add percentiles
        onset_pct = self.get_percentiles("onset")
        if onset_pct:
            lines.extend([
                "",
                "Onset Error Percentiles (ms):",
                f"  5th:  {onset_pct['p5'] * 1000:+.1f}",
                f"  25th: {onset_pct['p25'] * 1000:+.1f}",
                f"  50th: {onset_pct['p50'] * 1000:+.1f}",
                f"  75th: {onset_pct['p75'] * 1000:+.1f}",
                f"  95th: {onset_pct['p95'] * 1000:+.1f}",
            ])

        return "\n".join(lines)


def generate_timing_analysis(
    extracted: List[Note],
    ground_truth: List[Note],
    onset_tolerance: float = 0.1,
    pitch_tolerance: int = 0,
) -> TimingAnalysis:
    """Generate timing analysis from note comparison.

    Args:
        extracted: List of extracted notes
        ground_truth: List of ground truth notes
        onset_tolerance: Onset matching tolerance
        pitch_tolerance: Pitch matching tolerance

    Returns:
        TimingAnalysis
    """
    match_result = match_notes(
        extracted, ground_truth,
        onset_tolerance=onset_tolerance,
        offset_tolerance=1.0,  # Lenient on offset - we're measuring it
        pitch_tolerance=pitch_tolerance,
    )

    onset_errors = []
    offset_errors = []
    duration_ratios = []
    note_data = []

    for ext_note, gt_note in match_result.true_positives:
        onset_err = ext_note.start - gt_note.start
        offset_err = ext_note.end - gt_note.end

        onset_errors.append(onset_err)
        offset_errors.append(offset_err)

        # Duration ratio (avoid division by zero)
        if gt_note.duration > 0:
            ratio = ext_note.duration / gt_note.duration
            duration_ratios.append(ratio)

        note_data.append({
            "pitch": gt_note.pitch,
            "gt_onset": gt_note.start,
            "ext_onset": ext_note.start,
            "onset_error": onset_err,
            "gt_duration": gt_note.duration,
            "ext_duration": ext_note.duration,
            "offset_error": offset_err,
        })

    return TimingAnalysis(
        onset_errors=onset_errors,
        offset_errors=offset_errors,
        duration_ratios=duration_ratios,
        note_data=note_data,
    )


def generate_onset_histogram(
    analysis: TimingAnalysis,
    bin_width_ms: float = 5.0,
    range_ms: Tuple[float, float] = (-100, 100),
) -> Tuple[np.ndarray, np.ndarray]:
    """Generate onset error histogram data.

    Args:
        analysis: TimingAnalysis
        bin_width_ms: Bin width in milliseconds
        range_ms: Range in milliseconds (min, max)

    Returns:
        (bin_edges, counts) arrays
    """
    if not analysis.onset_errors:
        return np.array([]), np.array([])

    errors_ms = np.array(analysis.onset_errors) * 1000
    bins = np.arange(range_ms[0], range_ms[1] + bin_width_ms, bin_width_ms)
    counts, bin_edges = np.histogram(errors_ms, bins=bins)

    return bin_edges, counts


def generate_offset_histogram(
    analysis: TimingAnalysis,
    bin_width_ms: float = 10.0,
    range_ms: Tuple[float, float] = (-200, 200),
) -> Tuple[np.ndarray, np.ndarray]:
    """Generate offset error histogram data.

    Args:
        analysis: TimingAnalysis
        bin_width_ms: Bin width in milliseconds
        range_ms: Range in milliseconds

    Returns:
        (bin_edges, counts) arrays
    """
    if not analysis.offset_errors:
        return np.array([]), np.array([])

    errors_ms = np.array(analysis.offset_errors) * 1000
    bins = np.arange(range_ms[0], range_ms[1] + bin_width_ms, bin_width_ms)
    counts, bin_edges = np.histogram(errors_ms, bins=bins)

    return bin_edges, counts


def generate_timing_scatter(
    analysis: TimingAnalysis,
) -> Tuple[np.ndarray, np.ndarray]:
    """Generate scatter plot data (onset time vs onset error).

    Returns:
        (onset_times, onset_errors) arrays
    """
    if not analysis.note_data:
        return np.array([]), np.array([])

    times = np.array([d["gt_onset"] for d in analysis.note_data])
    errors = np.array([d["onset_error"] * 1000 for d in analysis.note_data])

    return times, errors


def plot_onset_histogram(
    analysis: TimingAnalysis,
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    figsize: Tuple[int, int] = (10, 6),
    bin_width_ms: float = 5.0,
) -> Any:
    """Plot onset timing error histogram.

    Args:
        analysis: TimingAnalysis
        output_path: Optional output path
        title: Optional title
        figsize: Figure size
        bin_width_ms: Bin width in milliseconds

    Returns:
        matplotlib figure
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available")
        return None

    if not analysis.onset_errors:
        logger.warning("No onset errors to plot")
        return None

    fig, ax = plt.subplots(figsize=figsize)

    errors_ms = np.array(analysis.onset_errors) * 1000

    # Determine range based on data
    max_abs = max(abs(errors_ms.min()), abs(errors_ms.max()), 50)
    range_ms = (-max_abs, max_abs)

    bins = np.arange(range_ms[0], range_ms[1] + bin_width_ms, bin_width_ms)

    # Plot histogram
    n, bins_out, patches = ax.hist(errors_ms, bins=bins, color='steelblue', edgecolor='white', alpha=0.8)

    # Color bars based on error magnitude
    for i, (patch, left_edge) in enumerate(zip(patches, bins_out[:-1])):
        right_edge = bins_out[i + 1]
        center = (left_edge + right_edge) / 2
        if abs(center) < 10:
            patch.set_facecolor('green')
        elif abs(center) < 25:
            patch.set_facecolor('yellow')
        elif abs(center) < 50:
            patch.set_facecolor('orange')
        else:
            patch.set_facecolor('red')

    # Add vertical line at zero
    ax.axvline(x=0, color='black', linestyle='--', linewidth=1.5, label='Perfect')

    # Add mean line
    mean_ms = analysis.onset_mean * 1000
    ax.axvline(x=mean_ms, color='red', linestyle='-', linewidth=1.5,
               label=f'Mean: {mean_ms:+.1f}ms')

    ax.set_xlabel('Onset Error (ms)')
    ax.set_ylabel('Count')
    ax.legend()

    # Add statistics text box
    stats_text = (
        f"N = {analysis.num_notes}\n"
        f"Mean: {mean_ms:+.1f} ms\n"
        f"Std: {analysis.onset_std * 1000:.1f} ms\n"
        f"Median: {analysis.onset_median * 1000:+.1f} ms"
    )
    ax.text(0.98, 0.98, stats_text, transform=ax.transAxes, fontsize=9,
            verticalalignment='top', horizontalalignment='right',
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

    if title:
        ax.set_title(title)
    else:
        ax.set_title('Onset Timing Error Distribution')

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved onset histogram to {output_path}")

    return fig


def plot_offset_histogram(
    analysis: TimingAnalysis,
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    figsize: Tuple[int, int] = (10, 6),
    bin_width_ms: float = 10.0,
) -> Any:
    """Plot offset timing error histogram.

    Args:
        analysis: TimingAnalysis
        output_path: Optional output path
        title: Optional title
        figsize: Figure size
        bin_width_ms: Bin width

    Returns:
        matplotlib figure
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available")
        return None

    if not analysis.offset_errors:
        logger.warning("No offset errors to plot")
        return None

    fig, ax = plt.subplots(figsize=figsize)

    errors_ms = np.array(analysis.offset_errors) * 1000

    # Determine range
    max_abs = max(abs(errors_ms.min()), abs(errors_ms.max()), 100)
    range_ms = (-max_abs, max_abs)

    bins = np.arange(range_ms[0], range_ms[1] + bin_width_ms, bin_width_ms)

    ax.hist(errors_ms, bins=bins, color='coral', edgecolor='white', alpha=0.8)

    # Add reference lines
    ax.axvline(x=0, color='black', linestyle='--', linewidth=1.5, label='Perfect')
    mean_ms = analysis.offset_mean * 1000
    ax.axvline(x=mean_ms, color='red', linestyle='-', linewidth=1.5,
               label=f'Mean: {mean_ms:+.1f}ms')

    ax.set_xlabel('Offset Error (ms)')
    ax.set_ylabel('Count')
    ax.legend()

    # Stats text
    stats_text = (
        f"N = {analysis.num_notes}\n"
        f"Mean: {mean_ms:+.1f} ms\n"
        f"Std: {analysis.offset_std * 1000:.1f} ms"
    )
    ax.text(0.98, 0.98, stats_text, transform=ax.transAxes, fontsize=9,
            verticalalignment='top', horizontalalignment='right',
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

    if title:
        ax.set_title(title)
    else:
        ax.set_title('Offset Timing Error Distribution')

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved offset histogram to {output_path}")

    return fig


def plot_timing_scatter(
    analysis: TimingAnalysis,
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    figsize: Tuple[int, int] = (12, 6),
) -> Any:
    """Plot timing error scatter plot over time.

    Args:
        analysis: TimingAnalysis
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

    if not analysis.note_data:
        logger.warning("No note data for scatter plot")
        return None

    fig, axes = plt.subplots(2, 1, figsize=figsize, sharex=True)

    times = np.array([d["gt_onset"] for d in analysis.note_data])
    onset_errors = np.array([d["onset_error"] * 1000 for d in analysis.note_data])
    offset_errors = np.array([d["offset_error"] * 1000 for d in analysis.note_data])

    # Onset errors over time
    ax1 = axes[0]
    ax1.scatter(times, onset_errors, alpha=0.5, s=20, c='steelblue')
    ax1.axhline(y=0, color='black', linestyle='--', linewidth=1)
    ax1.set_ylabel('Onset Error (ms)')
    ax1.set_title('Onset Timing Error Over Time')

    # Add rolling mean
    if len(times) > 10:
        sort_idx = np.argsort(times)
        sorted_times = times[sort_idx]
        sorted_onset = onset_errors[sort_idx]

        window = min(20, len(times) // 5)
        if window > 1:
            rolling_mean = np.convolve(sorted_onset, np.ones(window)/window, mode='valid')
            rolling_times = sorted_times[window-1:]
            ax1.plot(rolling_times, rolling_mean, color='red', linewidth=2, label=f'Rolling mean (n={window})')
            ax1.legend()

    # Offset errors over time
    ax2 = axes[1]
    ax2.scatter(times, offset_errors, alpha=0.5, s=20, c='coral')
    ax2.axhline(y=0, color='black', linestyle='--', linewidth=1)
    ax2.set_xlabel('Time (seconds)')
    ax2.set_ylabel('Offset Error (ms)')
    ax2.set_title('Offset Timing Error Over Time')

    if title:
        fig.suptitle(title, y=1.02)

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved timing scatter to {output_path}")

    return fig


def plot_duration_analysis(
    analysis: TimingAnalysis,
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    figsize: Tuple[int, int] = (10, 6),
) -> Any:
    """Plot duration ratio analysis.

    Args:
        analysis: TimingAnalysis
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

    if not analysis.duration_ratios:
        logger.warning("No duration ratios to plot")
        return None

    fig, axes = plt.subplots(1, 2, figsize=figsize)

    ratios = np.array(analysis.duration_ratios)

    # Histogram of duration ratios
    ax1 = axes[0]
    bins = np.linspace(0, 2, 41)  # 0 to 2x with 0.05 bins
    ax1.hist(ratios, bins=bins, color='purple', edgecolor='white', alpha=0.8)
    ax1.axvline(x=1.0, color='black', linestyle='--', linewidth=1.5, label='Perfect (1.0)')
    ax1.axvline(x=np.mean(ratios), color='red', linestyle='-', linewidth=1.5,
                label=f'Mean: {np.mean(ratios):.2f}')
    ax1.set_xlabel('Duration Ratio (Extracted / Ground Truth)')
    ax1.set_ylabel('Count')
    ax1.set_title('Duration Ratio Distribution')
    ax1.legend()

    # Duration error vs ground truth duration
    ax2 = axes[1]
    if analysis.note_data:
        gt_durations = np.array([d["gt_duration"] * 1000 for d in analysis.note_data])
        ext_durations = np.array([d["ext_duration"] * 1000 for d in analysis.note_data])

        ax2.scatter(gt_durations, ext_durations, alpha=0.5, s=20, c='purple')

        # Add perfect line
        max_dur = max(gt_durations.max(), ext_durations.max())
        ax2.plot([0, max_dur], [0, max_dur], 'k--', label='Perfect')

        ax2.set_xlabel('Ground Truth Duration (ms)')
        ax2.set_ylabel('Extracted Duration (ms)')
        ax2.set_title('Duration: Extracted vs Ground Truth')
        ax2.legend()

    if title:
        fig.suptitle(title)

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved duration analysis to {output_path}")

    return fig


def detect_quantization_damage(
    analysis: TimingAnalysis,
    tempo_bpm: Optional[float] = None,
) -> Dict[str, Any]:
    """Detect signs of quantization artifacts in timing.

    Quantization damage shows up as:
    - Clustered onset errors at grid positions
    - Loss of micro-timing variation
    - Duration snapping to grid multiples

    Args:
        analysis: TimingAnalysis
        tempo_bpm: Optional tempo for grid analysis

    Returns:
        Dictionary with quantization damage indicators
    """
    if not analysis.onset_errors:
        return {"detected": False, "reason": "No data"}

    onset_errors_ms = np.array(analysis.onset_errors) * 1000

    # Check for clustering at specific intervals
    # If heavily quantized, errors will cluster around 0 or specific grid positions
    error_std = np.std(onset_errors_ms)

    # Check for reduced timing variation
    # Natural timing has variation; heavy quantization removes it
    variation_ratio = error_std / 50.0  # Normalize to typical range

    result = {
        "detected": False,
        "indicators": [],
        "onset_error_std_ms": float(error_std),
        "variation_ratio": float(variation_ratio),
    }

    # Low variation suggests over-quantization
    if variation_ratio < 0.2:
        result["detected"] = True
        result["indicators"].append("Very low timing variation - possible over-quantization")

    # Check for grid clustering if tempo known
    if tempo_bpm:
        beat_ms = 60000 / tempo_bpm
        sixteenth_ms = beat_ms / 4

        # Check if errors cluster at 16th note intervals
        grid_aligned = sum(1 for e in onset_errors_ms if abs(e % sixteenth_ms) < 5 or abs(e % sixteenth_ms - sixteenth_ms) < 5)
        grid_ratio = grid_aligned / len(onset_errors_ms)

        result["grid_aligned_ratio"] = float(grid_ratio)

        if grid_ratio > 0.7:
            result["detected"] = True
            result["indicators"].append(f"High grid alignment ({grid_ratio:.1%}) suggests quantization")

    return result
