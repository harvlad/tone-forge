"""Precision recovery filters for MIDI extraction.

These filters operate AFTER note generation to selectively remove
artifacts while preserving real musical notes.

The key insight: recall is now good, precision is the bottleneck.
These filters implement "safe suppression" - removing false positives
without destroying musical structure.
"""
from __future__ import annotations

from .octave_false_positive import OctaveFalsePositiveFilter
from .harmonic_duplicate import HarmonicDuplicateFilter
from .subharmonic_cleanup import SubharmonicCleanupFilter
from .transient_validator import TransientNoteValidator
from .repeated_pattern import RepeatedPatternValidator
from .sustain_overlap import SustainOverlapCleanup
from .base import PrecisionFilter, FilterResult, FilterContext

__all__ = [
    # Base classes
    "PrecisionFilter",
    "FilterResult",
    "FilterContext",
    # Filters
    "OctaveFalsePositiveFilter",
    "HarmonicDuplicateFilter",
    "SubharmonicCleanupFilter",
    "TransientNoteValidator",
    "RepeatedPatternValidator",
    "SustainOverlapCleanup",
]
