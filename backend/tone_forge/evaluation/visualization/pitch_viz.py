"""Pitch visualization for MIDI extraction analysis.

Provides:
- Pitch confusion matrices
- Octave error analysis
- Pitch distribution comparisons
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .heatmaps import Note, match_notes

logger = logging.getLogger(__name__)

# Note names for display
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def pitch_to_name(pitch: int) -> str:
    """Convert MIDI pitch to note name."""
    octave = (pitch // 12) - 1
    note = NOTE_NAMES[pitch % 12]
    return f"{note}{octave}"


@dataclass
class PitchConfusionMatrix:
    """Confusion matrix for pitch detection errors."""

    # Matrix data: [true_pitch, predicted_pitch] = count
    matrix: np.ndarray
    pitch_range: Tuple[int, int]

    # Aggregate stats
    total_notes: int = 0
    correct_pitch: int = 0
    octave_errors: int = 0
    other_errors: int = 0

    @property
    def accuracy(self) -> float:
        """Overall pitch accuracy."""
        return self.correct_pitch / self.total_notes if self.total_notes > 0 else 0.0

    @property
    def octave_error_rate(self) -> float:
        """Rate of octave-related errors."""
        return self.octave_errors / self.total_notes if self.total_notes > 0 else 0.0

    def get_common_confusions(
        self,
        top_n: int = 10,
    ) -> List[Tuple[int, int, int]]:
        """Get most common pitch confusions.

        Returns:
            List of (true_pitch, predicted_pitch, count) tuples
        """
        confusions = []
        min_pitch = self.pitch_range[0]

        for i in range(self.matrix.shape[0]):
            for j in range(self.matrix.shape[1]):
                if i != j and self.matrix[i, j] > 0:
                    true_pitch = min_pitch + i
                    pred_pitch = min_pitch + j
                    confusions.append((true_pitch, pred_pitch, int(self.matrix[i, j])))

        return sorted(confusions, key=lambda x: -x[2])[:top_n]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "matrix": self.matrix.tolist(),
            "pitch_range": self.pitch_range,
            "total_notes": self.total_notes,
            "correct_pitch": self.correct_pitch,
            "octave_errors": self.octave_errors,
            "other_errors": self.other_errors,
            "accuracy": self.accuracy,
            "octave_error_rate": self.octave_error_rate,
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            "Pitch Confusion Analysis",
            "=" * 40,
            f"Total notes:       {self.total_notes}",
            f"Correct pitch:     {self.correct_pitch} ({self.accuracy:.1%})",
            f"Octave errors:     {self.octave_errors} ({self.octave_error_rate:.1%})",
            f"Other errors:      {self.other_errors}",
            "",
            "Most Common Confusions:",
        ]

        for true_p, pred_p, count in self.get_common_confusions(5):
            true_name = pitch_to_name(true_p)
            pred_name = pitch_to_name(pred_p)
            interval = pred_p - true_p
            lines.append(f"  {true_name} -> {pred_name} ({interval:+d} semitones): {count}")

        return "\n".join(lines)


@dataclass
class OctaveErrorAnalysis:
    """Analysis of octave-related errors."""

    # Distribution of octave offsets
    octave_offset_counts: Dict[int, int] = field(default_factory=dict)

    # Per-pitch class octave errors
    per_pitch_class_errors: Dict[int, Dict[int, int]] = field(default_factory=dict)

    # Summary stats
    total_notes: int = 0
    correct_octave: int = 0
    octave_up: int = 0  # Detected one octave too high
    octave_down: int = 0  # Detected one octave too low
    multi_octave: int = 0  # More than one octave error

    @property
    def octave_accuracy(self) -> float:
        """Percentage of notes with correct octave."""
        return self.correct_octave / self.total_notes if self.total_notes > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "octave_offset_counts": self.octave_offset_counts,
            "per_pitch_class_errors": self.per_pitch_class_errors,
            "total_notes": self.total_notes,
            "correct_octave": self.correct_octave,
            "octave_up": self.octave_up,
            "octave_down": self.octave_down,
            "multi_octave": self.multi_octave,
            "octave_accuracy": self.octave_accuracy,
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            "Octave Error Analysis",
            "=" * 40,
            f"Total notes:       {self.total_notes}",
            f"Correct octave:    {self.correct_octave} ({self.octave_accuracy:.1%})",
            f"One octave up:     {self.octave_up}",
            f"One octave down:   {self.octave_down}",
            f"Multi-octave:      {self.multi_octave}",
            "",
            "Octave Offset Distribution:",
        ]

        for offset in sorted(self.octave_offset_counts.keys()):
            count = self.octave_offset_counts[offset]
            pct = count / self.total_notes * 100 if self.total_notes > 0 else 0
            bar = "#" * int(pct / 2)
            lines.append(f"  {offset:+2d} octaves: {count:4d} ({pct:5.1f}%) {bar}")

        return "\n".join(lines)


def generate_pitch_confusion(
    extracted: List[Note],
    ground_truth: List[Note],
    pitch_range: Tuple[int, int] = (24, 96),
    onset_tolerance: float = 0.05,
) -> PitchConfusionMatrix:
    """Generate pitch confusion matrix from note comparison.

    Args:
        extracted: List of extracted notes
        ground_truth: List of ground truth notes
        pitch_range: MIDI pitch range to analyze
        onset_tolerance: Onset matching tolerance in seconds

    Returns:
        PitchConfusionMatrix
    """
    # Match notes (with any pitch - we want to find pitch confusions)
    match_result = match_notes(
        extracted, ground_truth,
        onset_tolerance=onset_tolerance,
        offset_tolerance=1.0,  # Lenient on offset for pitch analysis
        pitch_tolerance=24,  # Allow up to 2 octave difference
    )

    min_pitch, max_pitch = pitch_range
    size = max_pitch - min_pitch + 1
    matrix = np.zeros((size, size), dtype=np.int32)

    total_notes = 0
    correct_pitch = 0
    octave_errors = 0
    other_errors = 0

    for ext_note, gt_note in match_result.true_positives:
        if min_pitch <= gt_note.pitch <= max_pitch and min_pitch <= ext_note.pitch <= max_pitch:
            gt_idx = gt_note.pitch - min_pitch
            ext_idx = ext_note.pitch - min_pitch
            matrix[gt_idx, ext_idx] += 1
            total_notes += 1

            if ext_note.pitch == gt_note.pitch:
                correct_pitch += 1
            elif (ext_note.pitch - gt_note.pitch) % 12 == 0:
                octave_errors += 1
            else:
                other_errors += 1

    return PitchConfusionMatrix(
        matrix=matrix,
        pitch_range=pitch_range,
        total_notes=total_notes,
        correct_pitch=correct_pitch,
        octave_errors=octave_errors,
        other_errors=other_errors,
    )


def generate_octave_histogram(
    extracted: List[Note],
    ground_truth: List[Note],
    onset_tolerance: float = 0.05,
) -> OctaveErrorAnalysis:
    """Analyze octave errors in extracted notes.

    Args:
        extracted: List of extracted notes
        ground_truth: List of ground truth notes
        onset_tolerance: Onset matching tolerance

    Returns:
        OctaveErrorAnalysis
    """
    # Match notes with wide pitch tolerance
    match_result = match_notes(
        extracted, ground_truth,
        onset_tolerance=onset_tolerance,
        offset_tolerance=1.0,
        pitch_tolerance=36,  # 3 octaves
    )

    octave_offset_counts: Dict[int, int] = {}
    per_pitch_class_errors: Dict[int, Dict[int, int]] = {i: {} for i in range(12)}

    total_notes = 0
    correct_octave = 0
    octave_up = 0
    octave_down = 0
    multi_octave = 0

    for ext_note, gt_note in match_result.true_positives:
        total_notes += 1

        # Calculate octave offset
        pitch_diff = ext_note.pitch - gt_note.pitch
        octave_offset = pitch_diff // 12

        # Update counts
        octave_offset_counts[octave_offset] = octave_offset_counts.get(octave_offset, 0) + 1

        # Track per pitch class
        pitch_class = gt_note.pitch % 12
        per_pitch_class_errors[pitch_class][octave_offset] = \
            per_pitch_class_errors[pitch_class].get(octave_offset, 0) + 1

        # Categorize
        if octave_offset == 0:
            correct_octave += 1
        elif octave_offset == 1:
            octave_up += 1
        elif octave_offset == -1:
            octave_down += 1
        else:
            multi_octave += 1

    return OctaveErrorAnalysis(
        octave_offset_counts=octave_offset_counts,
        per_pitch_class_errors=per_pitch_class_errors,
        total_notes=total_notes,
        correct_octave=correct_octave,
        octave_up=octave_up,
        octave_down=octave_down,
        multi_octave=multi_octave,
    )


def plot_pitch_confusion(
    confusion: PitchConfusionMatrix,
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    figsize: Tuple[int, int] = (10, 8),
    compress_empty: bool = True,
) -> Any:
    """Plot pitch confusion matrix.

    Args:
        confusion: PitchConfusionMatrix to visualize
        output_path: Optional path to save figure
        title: Optional title
        figsize: Figure size
        compress_empty: Whether to hide rows/columns with no data

    Returns:
        matplotlib figure object
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available for plotting")
        return None

    matrix = confusion.matrix

    if compress_empty:
        # Find non-empty rows and columns
        row_sums = matrix.sum(axis=1)
        col_sums = matrix.sum(axis=0)
        non_empty_rows = np.where(row_sums > 0)[0]
        non_empty_cols = np.where(col_sums > 0)[0]

        if len(non_empty_rows) > 0 and len(non_empty_cols) > 0:
            # Get range covering all non-empty
            min_idx = min(non_empty_rows.min(), non_empty_cols.min())
            max_idx = max(non_empty_rows.max(), non_empty_cols.max())
            matrix = matrix[min_idx:max_idx+1, min_idx:max_idx+1]
            pitch_start = confusion.pitch_range[0] + min_idx
        else:
            pitch_start = confusion.pitch_range[0]
    else:
        pitch_start = confusion.pitch_range[0]

    fig, ax = plt.subplots(figsize=figsize)

    # Plot heatmap
    im = ax.imshow(matrix, cmap='YlOrRd', aspect='equal')
    plt.colorbar(im, ax=ax, label='Count')

    # Add labels
    n_pitches = matrix.shape[0]
    tick_step = max(1, n_pitches // 12)

    tick_positions = range(0, n_pitches, tick_step)
    tick_labels = [pitch_to_name(pitch_start + i) for i in tick_positions]

    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, rotation=45, ha='right')
    ax.set_yticks(tick_positions)
    ax.set_yticklabels(tick_labels)

    ax.set_xlabel('Predicted Pitch')
    ax.set_ylabel('True Pitch')

    if title:
        ax.set_title(title)
    else:
        ax.set_title(f'Pitch Confusion Matrix (Accuracy: {confusion.accuracy:.1%})')

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved confusion matrix to {output_path}")

    return fig


def plot_octave_histogram(
    analysis: OctaveErrorAnalysis,
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    figsize: Tuple[int, int] = (10, 6),
) -> Any:
    """Plot octave error histogram.

    Args:
        analysis: OctaveErrorAnalysis to visualize
        output_path: Optional path to save figure
        title: Optional title
        figsize: Figure size

    Returns:
        matplotlib figure object
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available for plotting")
        return None

    fig, axes = plt.subplots(1, 2, figsize=figsize)

    # Main histogram
    ax1 = axes[0]
    offsets = sorted(analysis.octave_offset_counts.keys())
    counts = [analysis.octave_offset_counts[o] for o in offsets]

    colors = ['green' if o == 0 else 'red' if abs(o) == 1 else 'darkred' for o in offsets]

    bars = ax1.bar(offsets, counts, color=colors)
    ax1.set_xlabel('Octave Offset')
    ax1.set_ylabel('Count')
    ax1.set_xticks(offsets)
    ax1.set_xticklabels([f'{o:+d}' for o in offsets])

    # Add percentage labels
    total = analysis.total_notes
    for bar, count in zip(bars, counts):
        if count > 0:
            pct = count / total * 100
            ax1.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height(),
                f'{pct:.1f}%',
                ha='center', va='bottom', fontsize=9
            )

    ax1.set_title('Octave Offset Distribution')

    # Per pitch class breakdown
    ax2 = axes[1]

    # Prepare data for stacked bar
    pitch_classes = list(range(12))
    correct = []
    errors = []

    for pc in pitch_classes:
        pc_data = analysis.per_pitch_class_errors.get(pc, {})
        correct.append(pc_data.get(0, 0))
        errors.append(sum(v for k, v in pc_data.items() if k != 0))

    x = np.arange(12)
    width = 0.6

    ax2.bar(x, correct, width, label='Correct Octave', color='green', alpha=0.7)
    ax2.bar(x, errors, width, bottom=correct, label='Wrong Octave', color='red', alpha=0.7)

    ax2.set_xlabel('Pitch Class')
    ax2.set_ylabel('Count')
    ax2.set_xticks(x)
    ax2.set_xticklabels(NOTE_NAMES)
    ax2.legend()
    ax2.set_title('Octave Errors by Pitch Class')

    if title:
        fig.suptitle(title)
    else:
        fig.suptitle(f'Octave Analysis (Accuracy: {analysis.octave_accuracy:.1%})')

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved octave histogram to {output_path}")

    return fig


def generate_pitch_distribution(
    notes: List[Note],
    pitch_range: Tuple[int, int] = (24, 96),
) -> Dict[int, int]:
    """Generate pitch distribution histogram.

    Args:
        notes: List of notes
        pitch_range: MIDI pitch range

    Returns:
        Dictionary mapping pitch to count
    """
    distribution: Dict[int, int] = {}
    for note in notes:
        if pitch_range[0] <= note.pitch <= pitch_range[1]:
            distribution[note.pitch] = distribution.get(note.pitch, 0) + 1
    return distribution


def plot_pitch_comparison(
    extracted: List[Note],
    ground_truth: List[Note],
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    figsize: Tuple[int, int] = (12, 6),
) -> Any:
    """Plot pitch distribution comparison.

    Args:
        extracted: Extracted notes
        ground_truth: Ground truth notes
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

    ext_dist = generate_pitch_distribution(extracted)
    gt_dist = generate_pitch_distribution(ground_truth)

    # Get all pitches
    all_pitches = sorted(set(ext_dist.keys()) | set(gt_dist.keys()))
    if not all_pitches:
        return None

    fig, ax = plt.subplots(figsize=figsize)

    x = np.arange(len(all_pitches))
    width = 0.35

    ext_counts = [ext_dist.get(p, 0) for p in all_pitches]
    gt_counts = [gt_dist.get(p, 0) for p in all_pitches]

    ax.bar(x - width/2, ext_counts, width, label='Extracted', color='blue', alpha=0.7)
    ax.bar(x + width/2, gt_counts, width, label='Ground Truth', color='green', alpha=0.7)

    # Labels
    ax.set_xlabel('Pitch')
    ax.set_ylabel('Count')

    # Show note names for sparse data, or every N pitches for dense
    if len(all_pitches) <= 24:
        ax.set_xticks(x)
        ax.set_xticklabels([pitch_to_name(p) for p in all_pitches], rotation=45, ha='right')
    else:
        step = max(1, len(all_pitches) // 12)
        ax.set_xticks(x[::step])
        ax.set_xticklabels([pitch_to_name(all_pitches[i]) for i in range(0, len(all_pitches), step)], rotation=45)

    ax.legend()

    if title:
        ax.set_title(title)
    else:
        ax.set_title('Pitch Distribution Comparison')

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved pitch comparison to {output_path}")

    return fig
