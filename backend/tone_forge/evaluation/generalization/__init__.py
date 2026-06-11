"""Generalization validation module for MIDI extraction.

Provides tools to validate that extraction generalizes across genres:
- CrossDatasetValidator: Leave-one-genre-out evaluation
- OverfitDetector: Identify overfit parameters/passes
- FailureAnalyzer: Analyze failure patterns by genre
- ParameterStability: Track stable vs unstable parameters
"""

from .cross_dataset_validator import (
    LeaveOneOutResult,
    GeneralizationGap,
    CrossDatasetValidator,
    run_leave_one_out,
)
from .overfit_detector import (
    OverfitHeuristic,
    OverfitReport,
    OverfitDetector,
    detect_overfit,
)
from .failure_analyzer import (
    FailurePattern,
    GenreFailureProfile,
    FailureAnalyzer,
    analyze_failures,
)

__all__ = [
    # Cross-dataset validation
    "LeaveOneOutResult",
    "GeneralizationGap",
    "CrossDatasetValidator",
    "run_leave_one_out",
    # Overfit detection
    "OverfitHeuristic",
    "OverfitReport",
    "OverfitDetector",
    "detect_overfit",
    # Failure analysis
    "FailurePattern",
    "GenreFailureProfile",
    "FailureAnalyzer",
    "analyze_failures",
]
