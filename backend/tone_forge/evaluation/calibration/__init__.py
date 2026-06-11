"""Confidence calibration module for MIDI extraction.

Provides calibration analysis tools:
- Confidence vs correctness analysis
- Reliability curves and ECE computation
- Per-pipeline calibration breakdown
- Post-hoc calibration adjustment with isotonic regression
"""

from .calibration_analyzer import (
    CalibrationBucket,
    CalibrationAnalysis,
    CalibrationAnalyzer,
    analyze_calibration,
)
from .reliability_curves import (
    ReliabilityCurve,
    plot_reliability_curve,
    plot_calibration_comparison,
)
from .calibration_adjustment import (
    CalibrationAdjuster,
    IsotonicCalibrator,
    PlattCalibrator,
    calibrate_notes,
)

__all__ = [
    # Analyzer
    "CalibrationBucket",
    "CalibrationAnalysis",
    "CalibrationAnalyzer",
    "analyze_calibration",
    # Reliability curves
    "ReliabilityCurve",
    "plot_reliability_curve",
    "plot_calibration_comparison",
    # Adjustment
    "CalibrationAdjuster",
    "IsotonicCalibrator",
    "PlattCalibrator",
    "calibrate_notes",
]
