"""Error heatmap visualization for MIDI extraction analysis.

Generates time x pitch heatmaps showing:
- False positive patterns (detected but not in ground truth)
- False negative patterns (in ground truth but not detected)
- Error density over time
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Note:
    """Simple note representation for visualization."""
    pitch: int
    start: float
    end: float
    velocity: int = 100
    confidence: float = 1.0

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class NoteMatchResult:
    """Result of matching extracted notes to ground truth."""
    true_positives: List[Tuple[Note, Note]]  # (extracted, ground_truth)
    false_positives: List[Note]  # Extracted but no match
    false_negatives: List[Note]  # Ground truth but not extracted

    @property
    def precision(self) -> float:
        tp = len(self.true_positives)
        fp = len(self.false_positives)
        return tp / (tp + fp) if (tp + fp) > 0 else 0.0

    @property
    def recall(self) -> float:
        tp = len(self.true_positives)
        fn = len(self.false_negatives)
        return tp / (tp + fn) if (tp + fn) > 0 else 0.0

    @property
    def f1(self) -> float:
        p = self.precision
        r = self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


@dataclass
class ErrorHeatmap:
    """Error heatmap data for visualization."""

    # Grid data
    time_bins: np.ndarray  # Time bin edges
    pitch_bins: np.ndarray  # Pitch bin edges (MIDI note numbers)
    false_positive_grid: np.ndarray  # 2D array [time, pitch]
    false_negative_grid: np.ndarray  # 2D array [time, pitch]

    # Metadata
    total_duration: float
    time_resolution_ms: float
    sample_id: str = ""

    # Aggregate stats
    total_fp: int = 0
    total_fn: int = 0

    def get_error_density(self) -> np.ndarray:
        """Get combined error density (FP + FN)."""
        return self.false_positive_grid + self.false_negative_grid

    def get_hotspots(
        self,
        threshold: float = 0.5,
        error_type: str = "both",
    ) -> List[Tuple[float, int, float]]:
        """Find time/pitch regions with high error concentration.

        Args:
            threshold: Minimum density to be considered hotspot
            error_type: "fp", "fn", or "both"

        Returns:
            List of (time, pitch, density) tuples
        """
        if error_type == "fp":
            grid = self.false_positive_grid
        elif error_type == "fn":
            grid = self.false_negative_grid
        else:
            grid = self.get_error_density()

        # Normalize
        max_val = grid.max()
        if max_val > 0:
            normalized = grid / max_val
        else:
            return []

        hotspots = []
        for t_idx in range(len(self.time_bins) - 1):
            for p_idx in range(len(self.pitch_bins) - 1):
                density = normalized[t_idx, p_idx]
                if density >= threshold:
                    time_center = (self.time_bins[t_idx] + self.time_bins[t_idx + 1]) / 2
                    pitch = int(self.pitch_bins[p_idx])
                    hotspots.append((time_center, pitch, float(density)))

        return sorted(hotspots, key=lambda x: -x[2])

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "time_bins": self.time_bins.tolist(),
            "pitch_bins": self.pitch_bins.tolist(),
            "false_positive_grid": self.false_positive_grid.tolist(),
            "false_negative_grid": self.false_negative_grid.tolist(),
            "total_duration": self.total_duration,
            "time_resolution_ms": self.time_resolution_ms,
            "sample_id": self.sample_id,
            "total_fp": self.total_fp,
            "total_fn": self.total_fn,
        }


def match_notes(
    extracted: List[Note],
    ground_truth: List[Note],
    onset_tolerance: float = 0.05,
    offset_tolerance: float = 0.1,
    pitch_tolerance: int = 0,
) -> NoteMatchResult:
    """Match extracted notes to ground truth notes.

    Args:
        extracted: List of extracted notes
        ground_truth: List of ground truth notes
        onset_tolerance: Maximum onset time difference (seconds)
        offset_tolerance: Maximum offset time difference (seconds)
        pitch_tolerance: Maximum pitch difference (semitones)

    Returns:
        NoteMatchResult with matched and unmatched notes
    """
    true_positives = []
    matched_gt_indices = set()

    # Sort by onset time
    extracted_sorted = sorted(extracted, key=lambda n: n.start)
    gt_sorted = sorted(ground_truth, key=lambda n: n.start)

    for ext_note in extracted_sorted:
        best_match = None
        best_match_idx = -1
        best_score = float('inf')

        for gt_idx, gt_note in enumerate(gt_sorted):
            if gt_idx in matched_gt_indices:
                continue

            # Check pitch
            pitch_diff = abs(ext_note.pitch - gt_note.pitch)
            if pitch_diff > pitch_tolerance:
                continue

            # Check onset
            onset_diff = abs(ext_note.start - gt_note.start)
            if onset_diff > onset_tolerance:
                continue

            # Check offset
            offset_diff = abs(ext_note.end - gt_note.end)
            if offset_diff > offset_tolerance:
                continue

            # Score is sum of differences
            score = onset_diff + offset_diff * 0.5 + pitch_diff * 0.1
            if score < best_score:
                best_score = score
                best_match = gt_note
                best_match_idx = gt_idx

        if best_match is not None:
            true_positives.append((ext_note, best_match))
            matched_gt_indices.add(best_match_idx)

    # Collect false positives (extracted but not matched)
    matched_ext = {id(tp[0]) for tp in true_positives}
    false_positives = [n for n in extracted if id(n) not in matched_ext]

    # Collect false negatives (ground truth but not matched)
    false_negatives = [
        gt_sorted[i] for i in range(len(gt_sorted))
        if i not in matched_gt_indices
    ]

    return NoteMatchResult(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
    )


def generate_error_heatmap(
    extracted: List[Note],
    ground_truth: List[Note],
    time_resolution_ms: float = 100.0,
    pitch_range: Tuple[int, int] = (24, 96),
    onset_tolerance: float = 0.05,
    sample_id: str = "",
) -> ErrorHeatmap:
    """Generate error heatmap from extracted vs ground truth notes.

    Args:
        extracted: List of extracted notes
        ground_truth: List of ground truth notes
        time_resolution_ms: Time bin size in milliseconds
        pitch_range: (min_pitch, max_pitch) MIDI note range
        onset_tolerance: Tolerance for note matching
        sample_id: Optional sample identifier

    Returns:
        ErrorHeatmap with false positive/negative grids
    """
    # Match notes
    match_result = match_notes(
        extracted, ground_truth,
        onset_tolerance=onset_tolerance,
    )

    # Determine time range
    all_notes = extracted + ground_truth
    if not all_notes:
        # Empty - return empty heatmap
        time_bins = np.array([0.0, 1.0])
        pitch_bins = np.arange(pitch_range[0], pitch_range[1] + 1)
        return ErrorHeatmap(
            time_bins=time_bins,
            pitch_bins=pitch_bins,
            false_positive_grid=np.zeros((1, pitch_range[1] - pitch_range[0])),
            false_negative_grid=np.zeros((1, pitch_range[1] - pitch_range[0])),
            total_duration=1.0,
            time_resolution_ms=time_resolution_ms,
            sample_id=sample_id,
        )

    max_time = max(n.end for n in all_notes)
    time_resolution_sec = time_resolution_ms / 1000.0

    # Create bins
    num_time_bins = int(np.ceil(max_time / time_resolution_sec))
    time_bins = np.linspace(0, max_time, num_time_bins + 1)
    pitch_bins = np.arange(pitch_range[0], pitch_range[1] + 2)  # +2 for edges

    # Initialize grids
    fp_grid = np.zeros((num_time_bins, pitch_range[1] - pitch_range[0] + 1))
    fn_grid = np.zeros((num_time_bins, pitch_range[1] - pitch_range[0] + 1))

    # Populate false positive grid
    for note in match_result.false_positives:
        if pitch_range[0] <= note.pitch <= pitch_range[1]:
            pitch_idx = note.pitch - pitch_range[0]
            start_bin = int(note.start / time_resolution_sec)
            end_bin = min(int(np.ceil(note.end / time_resolution_sec)), num_time_bins)
            for t_idx in range(start_bin, end_bin):
                fp_grid[t_idx, pitch_idx] += 1

    # Populate false negative grid
    for note in match_result.false_negatives:
        if pitch_range[0] <= note.pitch <= pitch_range[1]:
            pitch_idx = note.pitch - pitch_range[0]
            start_bin = int(note.start / time_resolution_sec)
            end_bin = min(int(np.ceil(note.end / time_resolution_sec)), num_time_bins)
            for t_idx in range(start_bin, end_bin):
                fn_grid[t_idx, pitch_idx] += 1

    return ErrorHeatmap(
        time_bins=time_bins,
        pitch_bins=pitch_bins,
        false_positive_grid=fp_grid,
        false_negative_grid=fn_grid,
        total_duration=max_time,
        time_resolution_ms=time_resolution_ms,
        sample_id=sample_id,
        total_fp=len(match_result.false_positives),
        total_fn=len(match_result.false_negatives),
    )


def generate_comparison_heatmap(
    heatmap_a: ErrorHeatmap,
    heatmap_b: ErrorHeatmap,
) -> ErrorHeatmap:
    """Generate comparison heatmap showing improvement/regression.

    Args:
        heatmap_a: Baseline heatmap
        heatmap_b: Comparison heatmap

    Returns:
        ErrorHeatmap where positive = improvement, negative = regression
    """
    # Ensure same dimensions
    if heatmap_a.false_positive_grid.shape != heatmap_b.false_positive_grid.shape:
        raise ValueError("Heatmaps must have same dimensions for comparison")

    # Compute difference (negative means improvement - fewer errors)
    fp_diff = heatmap_a.false_positive_grid - heatmap_b.false_positive_grid
    fn_diff = heatmap_a.false_negative_grid - heatmap_b.false_negative_grid

    return ErrorHeatmap(
        time_bins=heatmap_a.time_bins,
        pitch_bins=heatmap_a.pitch_bins,
        false_positive_grid=fp_diff,
        false_negative_grid=fn_diff,
        total_duration=heatmap_a.total_duration,
        time_resolution_ms=heatmap_a.time_resolution_ms,
        sample_id=f"{heatmap_a.sample_id}_vs_{heatmap_b.sample_id}",
        total_fp=heatmap_a.total_fp - heatmap_b.total_fp,
        total_fn=heatmap_a.total_fn - heatmap_b.total_fn,
    )


def plot_error_heatmap(
    heatmap: ErrorHeatmap,
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    show_colorbar: bool = True,
    figsize: Tuple[int, int] = (12, 6),
) -> Any:
    """Plot error heatmap using matplotlib.

    Args:
        heatmap: ErrorHeatmap to visualize
        output_path: Optional path to save figure
        title: Optional title for the plot
        show_colorbar: Whether to show colorbar
        figsize: Figure size in inches

    Returns:
        matplotlib figure object
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.colors as mcolors
    except ImportError:
        logger.warning("matplotlib not available for plotting")
        return None

    fig, axes = plt.subplots(1, 2, figsize=figsize)

    # False positive heatmap
    ax1 = axes[0]
    fp_data = heatmap.false_positive_grid.T  # Transpose for pitch on y-axis
    im1 = ax1.imshow(
        fp_data,
        aspect='auto',
        origin='lower',
        extent=[0, heatmap.total_duration, heatmap.pitch_bins[0], heatmap.pitch_bins[-1]],
        cmap='Reds',
        interpolation='nearest',
    )
    ax1.set_xlabel('Time (seconds)')
    ax1.set_ylabel('MIDI Pitch')
    ax1.set_title(f'False Positives (n={heatmap.total_fp})')
    if show_colorbar:
        plt.colorbar(im1, ax=ax1, label='Count')

    # False negative heatmap
    ax2 = axes[1]
    fn_data = heatmap.false_negative_grid.T
    im2 = ax2.imshow(
        fn_data,
        aspect='auto',
        origin='lower',
        extent=[0, heatmap.total_duration, heatmap.pitch_bins[0], heatmap.pitch_bins[-1]],
        cmap='Blues',
        interpolation='nearest',
    )
    ax2.set_xlabel('Time (seconds)')
    ax2.set_ylabel('MIDI Pitch')
    ax2.set_title(f'False Negatives (n={heatmap.total_fn})')
    if show_colorbar:
        plt.colorbar(im2, ax=ax2, label='Count')

    if title:
        fig.suptitle(title)
    else:
        fig.suptitle(f'Error Heatmap: {heatmap.sample_id}' if heatmap.sample_id else 'Error Heatmap')

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved heatmap to {output_path}")

    return fig


def plot_combined_heatmap(
    heatmap: ErrorHeatmap,
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    figsize: Tuple[int, int] = (12, 8),
) -> Any:
    """Plot combined error heatmap with FP in red, FN in blue.

    Args:
        heatmap: ErrorHeatmap to visualize
        output_path: Optional path to save figure
        title: Optional title
        figsize: Figure size

    Returns:
        matplotlib figure object
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.colors as mcolors
    except ImportError:
        logger.warning("matplotlib not available for plotting")
        return None

    fig, ax = plt.subplots(figsize=figsize)

    # Create RGB image: red for FP, blue for FN
    fp_norm = heatmap.false_positive_grid.T
    fn_norm = heatmap.false_negative_grid.T

    # Normalize
    fp_max = fp_norm.max() if fp_norm.max() > 0 else 1
    fn_max = fn_norm.max() if fn_norm.max() > 0 else 1

    fp_norm = fp_norm / fp_max
    fn_norm = fn_norm / fn_max

    # Create RGB image
    h, w = fp_norm.shape
    rgb = np.zeros((h, w, 3))
    rgb[:, :, 0] = fp_norm  # Red channel for FP
    rgb[:, :, 2] = fn_norm  # Blue channel for FN

    ax.imshow(
        rgb,
        aspect='auto',
        origin='lower',
        extent=[0, heatmap.total_duration, heatmap.pitch_bins[0], heatmap.pitch_bins[-1]],
        interpolation='nearest',
    )

    ax.set_xlabel('Time (seconds)')
    ax.set_ylabel('MIDI Pitch')

    # Add legend
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor='red', label=f'False Positives ({heatmap.total_fp})'),
        Patch(facecolor='blue', label=f'False Negatives ({heatmap.total_fn})'),
        Patch(facecolor='purple', label='Both'),
    ]
    ax.legend(handles=legend_elements, loc='upper right')

    if title:
        ax.set_title(title)
    else:
        ax.set_title(f'Error Heatmap: {heatmap.sample_id}' if heatmap.sample_id else 'Combined Error Heatmap')

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved combined heatmap to {output_path}")

    return fig


def aggregate_heatmaps(
    heatmaps: List[ErrorHeatmap],
) -> ErrorHeatmap:
    """Aggregate multiple heatmaps into a single summary heatmap.

    All heatmaps must have the same dimensions.

    Args:
        heatmaps: List of heatmaps to aggregate

    Returns:
        Aggregated ErrorHeatmap
    """
    if not heatmaps:
        raise ValueError("No heatmaps to aggregate")

    if len(heatmaps) == 1:
        return heatmaps[0]

    # Check dimensions match
    shape = heatmaps[0].false_positive_grid.shape
    for h in heatmaps[1:]:
        if h.false_positive_grid.shape != shape:
            raise ValueError("All heatmaps must have same dimensions")

    # Sum grids
    fp_sum = sum(h.false_positive_grid for h in heatmaps)
    fn_sum = sum(h.false_negative_grid for h in heatmaps)

    return ErrorHeatmap(
        time_bins=heatmaps[0].time_bins,
        pitch_bins=heatmaps[0].pitch_bins,
        false_positive_grid=fp_sum,
        false_negative_grid=fn_sum,
        total_duration=heatmaps[0].total_duration,
        time_resolution_ms=heatmaps[0].time_resolution_ms,
        sample_id=f"aggregate_{len(heatmaps)}_samples",
        total_fp=sum(h.total_fp for h in heatmaps),
        total_fn=sum(h.total_fn for h in heatmaps),
    )
