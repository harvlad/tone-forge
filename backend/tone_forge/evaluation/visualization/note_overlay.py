"""Piano roll note overlay visualization.

Provides:
- Piano roll with color-coded match status
- True positives, false positives, false negatives highlighted
- Zoomable time ranges
- Interactive/static rendering options
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .heatmaps import Note, match_notes, NoteMatchResult
from .pitch_viz import NOTE_NAMES, pitch_to_name

logger = logging.getLogger(__name__)


@dataclass
class NoteOverlayConfig:
    """Configuration for note overlay rendering."""

    # Colors (RGBA tuples)
    true_positive_color: Tuple[float, ...] = (0.2, 0.8, 0.2, 0.8)  # Green
    false_positive_color: Tuple[float, ...] = (0.8, 0.2, 0.2, 0.8)  # Red
    false_negative_color: Tuple[float, ...] = (0.2, 0.2, 0.8, 0.8)  # Blue
    ground_truth_outline: Tuple[float, ...] = (0.0, 0.0, 0.0, 0.5)  # Black outline

    # Sizing
    note_height: float = 0.8  # As fraction of pitch grid
    min_note_width_px: float = 2.0  # Minimum pixel width for visibility

    # Grid
    show_grid: bool = True
    grid_color: Tuple[float, ...] = (0.9, 0.9, 0.9, 1.0)

    # Labels
    show_pitch_labels: bool = True
    show_time_labels: bool = True
    pitch_label_interval: int = 12  # Every octave

    # Legend
    show_legend: bool = True


class NoteOverlayRenderer:
    """Renders piano roll with note matching overlay."""

    def __init__(self, config: Optional[NoteOverlayConfig] = None):
        """Initialize renderer.

        Args:
            config: Rendering configuration
        """
        self.config = config or NoteOverlayConfig()

    def render(
        self,
        extracted: List[Note],
        ground_truth: List[Note],
        output_path: Optional[Path] = None,
        title: Optional[str] = None,
        time_range: Optional[Tuple[float, float]] = None,
        pitch_range: Optional[Tuple[int, int]] = None,
        figsize: Tuple[int, int] = (16, 8),
        onset_tolerance: float = 0.05,
    ) -> Any:
        """Render piano roll with note overlay.

        Args:
            extracted: Extracted notes
            ground_truth: Ground truth notes
            output_path: Optional path to save figure
            title: Optional title
            time_range: Optional (start, end) time range to display
            pitch_range: Optional (min, max) pitch range
            figsize: Figure size in inches
            onset_tolerance: Tolerance for note matching

        Returns:
            matplotlib figure object
        """
        try:
            import matplotlib.pyplot as plt
            import matplotlib.patches as patches
        except ImportError:
            logger.warning("matplotlib not available")
            return None

        # Match notes
        match_result = match_notes(
            extracted, ground_truth,
            onset_tolerance=onset_tolerance,
        )

        # Determine ranges
        all_notes = extracted + ground_truth
        if not all_notes:
            logger.warning("No notes to render")
            return None

        if time_range is None:
            min_time = min(n.start for n in all_notes)
            max_time = max(n.end for n in all_notes)
            time_range = (max(0, min_time - 0.5), max_time + 0.5)

        if pitch_range is None:
            pitches = [n.pitch for n in all_notes]
            pitch_range = (min(pitches) - 2, max(pitches) + 2)

        # Create figure
        fig, ax = plt.subplots(figsize=figsize)

        # Draw grid
        if self.config.show_grid:
            self._draw_grid(ax, time_range, pitch_range)

        # Draw notes
        self._draw_notes(ax, match_result, time_range, pitch_range)

        # Set up axes
        ax.set_xlim(time_range)
        ax.set_ylim(pitch_range[0] - 0.5, pitch_range[1] + 0.5)
        ax.set_xlabel('Time (seconds)')
        ax.set_ylabel('MIDI Pitch')

        # Pitch labels
        if self.config.show_pitch_labels:
            pitches_to_label = range(
                ((pitch_range[0] // 12) + 1) * 12,
                pitch_range[1] + 1,
                self.config.pitch_label_interval
            )
            ax.set_yticks(list(pitches_to_label))
            ax.set_yticklabels([pitch_to_name(p) for p in pitches_to_label])

        # Legend
        if self.config.show_legend:
            self._add_legend(ax, match_result)

        # Title
        if title:
            ax.set_title(title)
        else:
            ax.set_title(f'Note Overlay (F1: {match_result.f1:.1%})')

        plt.tight_layout()

        # Save
        if output_path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(output_path, dpi=150, bbox_inches='tight')
            logger.info(f"Saved note overlay to {output_path}")

        return fig

    def _draw_grid(
        self,
        ax: Any,
        time_range: Tuple[float, float],
        pitch_range: Tuple[int, int],
    ) -> None:
        """Draw background grid."""
        # Horizontal lines at each pitch
        for pitch in range(pitch_range[0], pitch_range[1] + 1):
            # Darker line at C notes (octave boundaries)
            if pitch % 12 == 0:
                ax.axhline(y=pitch, color='lightgray', linewidth=0.8, alpha=0.8)
            else:
                ax.axhline(y=pitch, color='whitesmoke', linewidth=0.3, alpha=0.5)

        # Vertical lines at regular intervals
        duration = time_range[1] - time_range[0]
        if duration <= 5:
            interval = 0.5
        elif duration <= 20:
            interval = 1.0
        elif duration <= 60:
            interval = 5.0
        else:
            interval = 10.0

        t = time_range[0]
        while t <= time_range[1]:
            ax.axvline(x=t, color='whitesmoke', linewidth=0.3, alpha=0.5)
            t += interval

    def _draw_notes(
        self,
        ax: Any,
        match_result: NoteMatchResult,
        time_range: Tuple[float, float],
        pitch_range: Tuple[int, int],
    ) -> None:
        """Draw all notes with appropriate colors."""
        import matplotlib.patches as mpatches

        height = self.config.note_height

        # Draw false negatives first (ground truth not detected)
        for note in match_result.false_negatives:
            if not self._in_range(note, time_range, pitch_range):
                continue
            rect = mpatches.Rectangle(
                (note.start, note.pitch - height / 2),
                note.duration,
                height,
                facecolor=self.config.false_negative_color,
                edgecolor='darkblue',
                linewidth=0.5,
            )
            ax.add_patch(rect)

        # Draw true positives (matched notes)
        for ext_note, gt_note in match_result.true_positives:
            if not self._in_range(gt_note, time_range, pitch_range):
                continue

            # Draw ground truth outline
            rect_gt = mpatches.Rectangle(
                (gt_note.start, gt_note.pitch - height / 2),
                gt_note.duration,
                height,
                facecolor='none',
                edgecolor=self.config.ground_truth_outline,
                linewidth=1.5,
                linestyle='--',
            )
            ax.add_patch(rect_gt)

            # Draw extracted note
            rect_ext = mpatches.Rectangle(
                (ext_note.start, ext_note.pitch - height / 2),
                ext_note.duration,
                height,
                facecolor=self.config.true_positive_color,
                edgecolor='darkgreen',
                linewidth=0.5,
            )
            ax.add_patch(rect_ext)

        # Draw false positives (detected but not in ground truth)
        for note in match_result.false_positives:
            if not self._in_range(note, time_range, pitch_range):
                continue
            rect = mpatches.Rectangle(
                (note.start, note.pitch - height / 2),
                note.duration,
                height,
                facecolor=self.config.false_positive_color,
                edgecolor='darkred',
                linewidth=0.5,
            )
            ax.add_patch(rect)

    def _in_range(
        self,
        note: Note,
        time_range: Tuple[float, float],
        pitch_range: Tuple[int, int],
    ) -> bool:
        """Check if note is within display range."""
        return (
            note.end >= time_range[0] and
            note.start <= time_range[1] and
            pitch_range[0] <= note.pitch <= pitch_range[1]
        )

    def _add_legend(self, ax: Any, match_result: NoteMatchResult) -> None:
        """Add legend to plot."""
        import matplotlib.patches as mpatches

        legend_elements = [
            mpatches.Patch(
                facecolor=self.config.true_positive_color,
                edgecolor='darkgreen',
                label=f'True Positives ({len(match_result.true_positives)})'
            ),
            mpatches.Patch(
                facecolor=self.config.false_positive_color,
                edgecolor='darkred',
                label=f'False Positives ({len(match_result.false_positives)})'
            ),
            mpatches.Patch(
                facecolor=self.config.false_negative_color,
                edgecolor='darkblue',
                label=f'False Negatives ({len(match_result.false_negatives)})'
            ),
        ]
        ax.legend(handles=legend_elements, loc='upper right')

    def render_multi_window(
        self,
        extracted: List[Note],
        ground_truth: List[Note],
        output_dir: Path,
        window_size: float = 10.0,
        overlap: float = 2.0,
        prefix: str = "overlay",
        **kwargs,
    ) -> List[Path]:
        """Render multiple overlapping windows for long samples.

        Args:
            extracted: Extracted notes
            ground_truth: Ground truth notes
            output_dir: Directory to save figures
            window_size: Window duration in seconds
            overlap: Overlap between windows
            prefix: Filename prefix
            **kwargs: Additional args for render()

        Returns:
            List of saved file paths
        """
        all_notes = extracted + ground_truth
        if not all_notes:
            return []

        max_time = max(n.end for n in all_notes)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        saved_paths = []
        window_start = 0
        window_idx = 0

        while window_start < max_time:
            window_end = window_start + window_size
            time_range = (window_start, window_end)

            output_path = output_dir / f"{prefix}_{window_idx:03d}.png"
            title = kwargs.get("title", "")
            if title:
                title = f"{title} [{window_start:.1f}s - {window_end:.1f}s]"
            else:
                title = f"Window {window_idx} [{window_start:.1f}s - {window_end:.1f}s]"

            self.render(
                extracted, ground_truth,
                output_path=output_path,
                time_range=time_range,
                title=title,
                **kwargs,
            )
            saved_paths.append(output_path)

            window_start += window_size - overlap
            window_idx += 1

        return saved_paths


def render_piano_roll(
    extracted: List[Note],
    ground_truth: List[Note],
    output_path: Optional[Path] = None,
    **kwargs,
) -> Any:
    """Convenience function to render piano roll overlay.

    Args:
        extracted: Extracted notes
        ground_truth: Ground truth notes
        output_path: Optional output path
        **kwargs: Additional arguments for NoteOverlayRenderer.render()

    Returns:
        matplotlib figure
    """
    renderer = NoteOverlayRenderer()
    return renderer.render(extracted, ground_truth, output_path=output_path, **kwargs)


def render_extraction_comparison(
    extractions: Dict[str, List[Note]],
    ground_truth: List[Note],
    output_path: Optional[Path] = None,
    title: Optional[str] = None,
    figsize: Tuple[int, int] = (16, 10),
) -> Any:
    """Render comparison of multiple extraction methods.

    Args:
        extractions: Dictionary mapping method name to extracted notes
        ground_truth: Ground truth notes
        output_path: Optional output path
        title: Optional title
        figsize: Figure size

    Returns:
        matplotlib figure
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        logger.warning("matplotlib not available")
        return None

    n_methods = len(extractions)
    if n_methods == 0:
        return None

    fig, axes = plt.subplots(n_methods, 1, figsize=figsize, sharex=True, sharey=True)
    if n_methods == 1:
        axes = [axes]

    # Determine common ranges
    all_notes = ground_truth.copy()
    for notes in extractions.values():
        all_notes.extend(notes)

    if not all_notes:
        return None

    time_range = (0, max(n.end for n in all_notes) + 0.5)
    pitch_range = (min(n.pitch for n in all_notes) - 2, max(n.pitch for n in all_notes) + 2)

    renderer = NoteOverlayRenderer()

    for ax, (method_name, extracted) in zip(axes, extractions.items()):
        match_result = match_notes(extracted, ground_truth)

        # Draw notes
        renderer._draw_notes(ax, match_result, time_range, pitch_range)

        # Labels
        ax.set_ylabel(method_name)
        ax.set_xlim(time_range)
        ax.set_ylim(pitch_range[0] - 0.5, pitch_range[1] + 0.5)

        # Add F1 score
        f1 = match_result.f1
        ax.text(
            0.02, 0.98, f'F1: {f1:.1%}',
            transform=ax.transAxes,
            verticalalignment='top',
            fontsize=10,
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.8)
        )

    axes[-1].set_xlabel('Time (seconds)')

    if title:
        fig.suptitle(title)

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        logger.info(f"Saved comparison to {output_path}")

    return fig
