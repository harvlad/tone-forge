"""Distance → calibrated confidence + margin signal.

Two pure helpers feed the tier classifier (``tone.tiers.classify``):

* ``calibrate(distance) -> float`` projects a raw retrieval distance
  onto ``[0, 1]``. Plan §7 specifies an isotonic regression fit from
  100 hand-labeled clips. Until that artifact is committed, this module
  ships a deliberately conservative placeholder that is hard-capped
  below the HIGH-tier confidence threshold so auto-apply cannot fire
  on uncalibrated data.

* ``compute_margin(distances) -> float | None`` is the relative
  separation between the top-1 distance and the runner-up. Large
  margin → unambiguous winner. Returns ``None`` when fewer than two
  distances are supplied (e.g. legacy ``preset_matches`` dicts that
  only persist top-1).

Both functions are pure and have no I/O. The placeholder calibrator is
swapped for a fitted one in P6.1 by replacing the module-level
``_CALIBRATOR`` reference; the public ``calibrate`` signature is
stable.
"""

from __future__ import annotations

import math
from typing import Optional, Protocol, Sequence

from tone_forge.tone import tiers


# The placeholder calibrator must not produce confidences that on
# their own unlock HIGH. We cap one tick below the HIGH threshold so
# the classifier's HIGH branch (which also requires margin) cannot fire
# from pre-calibration data, but MEDIUM-via-confidence remains
# reachable when the match is genuinely close.
PLACEHOLDER_CONFIDENCE_CAP: float = tiers.HIGH_CONFIDENCE_MIN - 0.01

# Distance scale for the placeholder. Features in the preset catalog
# are min-max normalized to roughly [0, 1] per dimension (see
# ``preset_catalog.catalog_builder.PresetFingerprint.to_vector``), so
# an 8-feature L2 distance lies in roughly [0, sqrt(8)] ≈ [0, 2.83].
# A scale of 1.0 makes distance=1.0 map to confidence ≈ 0.37, which
# matches the "this is in the right neighborhood but not certain"
# qualitative band. The figure moves when the fitted model lands.
PLACEHOLDER_DISTANCE_SCALE: float = 1.0


class Calibrator(Protocol):
    """Pluggable interface for distance → confidence mapping.

    The fitted isotonic model (P6.1+) will be a class implementing this
    protocol — sklearn's ``IsotonicRegression`` already exposes a
    ``predict`` that fits with a small adapter.
    """

    def __call__(self, distance: float) -> float: ...


def calibrate(distance: float) -> float:
    """Map a raw retrieval distance to calibrated confidence in ``[0, 1]``.

    PRE-CALIBRATION PLACEHOLDER. Until the fitted isotonic model is
    committed (Plan §7 requires 100 labeled clips), this returns
    ``exp(-distance / SCALE)`` clamped to ``[0, PLACEHOLDER_CAP]``.
    The cap ensures HIGH is unreachable from this signal alone; it
    keeps MEDIUM-via-confidence reachable so genuinely close matches
    still surface to the user.

    Nonsensical inputs (negative / NaN / inf) collapse to 0.0 — better
    to under-claim than to silently report confidence for a broken
    distance.
    """
    return _placeholder_calibrate(distance)


def _placeholder_calibrate(distance: float) -> float:
    if distance is None:
        return 0.0
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(d) or math.isinf(d) or d < 0.0:
        return 0.0
    raw = math.exp(-d / PLACEHOLDER_DISTANCE_SCALE)
    if raw > PLACEHOLDER_CONFIDENCE_CAP:
        return PLACEHOLDER_CONFIDENCE_CAP
    return raw


def compute_margin(distances: Sequence[float]) -> Optional[float]:
    """Return ``(d_second - d_top) / d_top`` from a sorted distance list.

    The input is whatever ``preset_catalog.match_audio_file`` returned,
    in ascending order. We re-sort defensively because callers might
    pass an unsorted view.

    Returns ``None`` when:
      * fewer than two finite distances are present,
      * the top distance is non-positive (degenerate self-match case;
        margin formula divides by it),
      * any of the inputs are unparseable.

    Returning ``None`` rather than 0.0 lets the classifier distinguish
    "we know there's no separation" (margin=0) from "we cannot tell"
    (margin=None) — those are different policy signals.
    """
    if distances is None:
        return None
    cleaned: list[float] = []
    for value in distances:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(f) or math.isinf(f) or f < 0.0:
            continue
        cleaned.append(f)
    if len(cleaned) < 2:
        return None
    cleaned.sort()
    d_top, d_second = cleaned[0], cleaned[1]
    if d_top <= 0.0:
        return None
    return (d_second - d_top) / d_top


__all__ = [
    "Calibrator",
    "PLACEHOLDER_CONFIDENCE_CAP",
    "PLACEHOLDER_DISTANCE_SCALE",
    "calibrate",
    "compute_margin",
]
