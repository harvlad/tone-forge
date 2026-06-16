"""Disagreement classification.

After alignment produces per-timestamp (jam_chord, tab_chord) pairs,
the classifier labels each mismatch with one of the categories from
the architecture directive. Aggregating these labels over the
corpus answers "what failure class dominates?", which drives the
engine improvement loop.

Public surface:

- :class:`DisagreementClass` -- the taxonomy enum.
- :func:`classify_disagreement` -- label one (jam, tab) pair.
- :func:`classify_alignment` -- batch-classify every row under one
  alignment_id, updating each row in-place.
"""

from __future__ import annotations

from enum import Enum


class DisagreementClass(str, Enum):
    """Classification labels for per-timestamp chord disagreements.

    Values mirror the directive's taxonomy. Subclassing ``str`` makes
    instances JSON-serialisable as their textual value without any
    custom encoder.
    """

    BOUNDARY_ERROR = "BOUNDARY_ERROR"
    EXTENSION_COLLAPSE = "EXTENSION_COLLAPSE"
    SLASH_CHORD_COLLAPSE = "SLASH_CHORD_COLLAPSE"
    KEY_CONTEXT_ERROR = "KEY_CONTEXT_ERROR"
    LIKELY_TAB_ERROR = "LIKELY_TAB_ERROR"
    UNKNOWN = "UNKNOWN"


from .classifier import (  # noqa: E402  (deferred import for enum)
    LIKELY_TAB_ERROR_CONF_THRESHOLD,
    classify_alignment,
    classify_disagreement,
)
from .reclassify import (  # noqa: E402
    reclassify_all_alignments,
    reclassify_song,
)
from .calibration import (  # noqa: E402
    DEFAULT_CANDIDATE_THRESHOLDS,
    confidence_calibration_report,
)


__all__ = [
    "DisagreementClass",
    "classify_disagreement",
    "classify_alignment",
    "LIKELY_TAB_ERROR_CONF_THRESHOLD",
    "reclassify_all_alignments",
    "reclassify_song",
    "confidence_calibration_report",
    "DEFAULT_CANDIDATE_THRESHOLDS",
]
