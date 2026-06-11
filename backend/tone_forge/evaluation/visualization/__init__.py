"""Visualization module for MIDI extraction analysis.

Provides failure visualization tools:
- Heatmaps for false positive/negative patterns
- Pitch confusion matrices and octave error analysis
- Timing offset histograms
- Piano roll note overlay
- HTML report generation
"""

from .heatmaps import (
    ErrorHeatmap,
    generate_error_heatmap,
    generate_comparison_heatmap,
)
from .pitch_viz import (
    PitchConfusionMatrix,
    OctaveErrorAnalysis,
    generate_pitch_confusion,
    generate_octave_histogram,
)
from .timing_viz import (
    TimingAnalysis,
    generate_onset_histogram,
    generate_offset_histogram,
    generate_timing_scatter,
)
from .note_overlay import (
    NoteOverlayRenderer,
    render_piano_roll,
)
from .report_generator import (
    ReportConfig,
    BenchmarkReport,
    generate_html_report,
)

__all__ = [
    # Heatmaps
    "ErrorHeatmap",
    "generate_error_heatmap",
    "generate_comparison_heatmap",
    # Pitch visualization
    "PitchConfusionMatrix",
    "OctaveErrorAnalysis",
    "generate_pitch_confusion",
    "generate_octave_histogram",
    # Timing visualization
    "TimingAnalysis",
    "generate_onset_histogram",
    "generate_offset_histogram",
    "generate_timing_scatter",
    # Note overlay
    "NoteOverlayRenderer",
    "render_piano_roll",
    # Reports
    "ReportConfig",
    "BenchmarkReport",
    "generate_html_report",
]
