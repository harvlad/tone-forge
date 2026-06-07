"""ConfidenceTier classifier.

Pure function: ``(calibrated_confidence, margin) -> ConfidenceTier``.
This is the single decision boundary that determines whether Jam
auto-applies a matched preset (HIGH), suggests it with alternates
(MEDIUM), or falls back to a curated monitor chain (LOW).

Thresholds are pinned verbatim from ``/EXECUTION_PLAN.md`` §7:

    HIGH    if calibrated_confidence >= 0.80 AND margin >= 0.20
    MEDIUM  if calibrated_confidence >= 0.55 OR  margin >= 0.10
    LOW     otherwise
    UNKNOWN if retrieval errored (caller's signal, not classified here)

The thresholds are intentionally exposed as module-level constants so
the calibration-refit cadence (quarterly per §7) can tweak them without
chasing literals through the code.

Margin semantics
----------------
``margin`` is ``(d_second - d_top) / d_top`` — the relative separation
between the top-1 retrieval distance and the runner-up. Large margin
means the top result is clearly the best; small margin means several
candidates are similar and we should suggest rather than auto-apply.

Margin is ``None`` when only one candidate was retrieved (or when the
caller only persisted a top-1 distance, as the legacy ``preset_matches``
dict does). In that case HIGH is unreachable — auto-apply requires
*both* signals to agree — but MEDIUM is still reachable via the
confidence-alone branch. This degrades gracefully on legacy data
without inventing a margin we don't have.
"""

from __future__ import annotations

from typing import Optional

from tone_forge.contracts import ConfidenceTier

# Pinned thresholds. See EXECUTION_PLAN.md §7. Calibration refits
# adjust the *calibrator* curve, not these constants — they only move
# when the policy itself changes, which is a deliberate decision logged
# in CHANGELOG.
HIGH_CONFIDENCE_MIN: float = 0.80
HIGH_MARGIN_MIN: float = 0.20
MEDIUM_CONFIDENCE_MIN: float = 0.55
MEDIUM_MARGIN_MIN: float = 0.10


def classify(
    calibrated_confidence: float,
    margin: Optional[float],
) -> ConfidenceTier:
    """Map ``(calibrated_confidence, margin)`` onto a tier.

    Parameters
    ----------
    calibrated_confidence
        Value in ``[0, 1]`` from ``tone.calibration.calibrate``. Values
        outside that range are clamped — defending against future
        calibrator regressions rather than trusting caller hygiene.
    margin
        ``(d_second - d_top) / d_top`` or ``None`` when only a top-1
        distance was retrieved. ``None`` blocks HIGH (which requires
        both signals) but does not block MEDIUM, which has a
        confidence-alone branch.

    Returns
    -------
    ConfidenceTier
        Never returns ``UNKNOWN`` — that tier is reserved for the
        caller to signal a retrieval failure, not a low-confidence
        result. A successful retrieval that scored badly is LOW.
    """
    conf = _clamp_unit(calibrated_confidence)
    m = _safe_margin(margin)

    if m is not None and conf >= HIGH_CONFIDENCE_MIN and m >= HIGH_MARGIN_MIN:
        return ConfidenceTier.HIGH

    if conf >= MEDIUM_CONFIDENCE_MIN:
        return ConfidenceTier.MEDIUM
    if m is not None and m >= MEDIUM_MARGIN_MIN:
        return ConfidenceTier.MEDIUM

    return ConfidenceTier.LOW


def _clamp_unit(value: float) -> float:
    """Clamp to ``[0, 1]``; treat NaN as 0.0.

    NaN comparisons all return False, so it would silently slip past
    range checks and produce nonsense tiers. Map it to the safe end.
    """
    if value != value:  # NaN check
        return 0.0
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def _safe_margin(margin: Optional[float]) -> Optional[float]:
    """``None`` stays ``None``; NaN/negative is treated as zero margin.

    Negative margin would mean the runner-up is closer than the top —
    an impossible condition that indicates an upstream bug. Coerce to
    zero so policy doesn't reward broken inputs with a HIGH tier.
    """
    if margin is None:
        return None
    if margin != margin:
        return 0.0
    if margin < 0.0:
        return 0.0
    return float(margin)


__all__ = [
    "HIGH_CONFIDENCE_MIN",
    "HIGH_MARGIN_MIN",
    "MEDIUM_CONFIDENCE_MIN",
    "MEDIUM_MARGIN_MIN",
    "classify",
]
