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
swapped for a fitted one by dropping a joblib-serialized
``IsotonicRegression`` into ``calibration_v1.joblib`` alongside this
module — the auto-loader at import time picks it up and rebinds
``_CALIBRATOR``. The public ``calibrate`` signature is stable.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Optional, Protocol, Sequence

from tone_forge.tone import tiers

logger = logging.getLogger("toneforge.tone.calibration")


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


# Filename the auto-loader looks for. Lives alongside this module so
# adding the fitted artifact is a single ``git add`` operation; no
# code change required. Versioned in the name so a future v2 refit
# can ship alongside v1 (the loader picks the newest by default —
# extension point left to a follow-up, current loader only knows v1).
FITTED_MODEL_FILENAME: str = "calibration_v1.joblib"


class Calibrator(Protocol):
    """Pluggable interface for distance → confidence mapping.

    The placeholder is a plain function; the fitted model is an
    instance of :class:`IsotonicCalibrator`. Both honour the same
    contract and are interchangeable via the module-level
    ``_CALIBRATOR`` reference.
    """

    def __call__(self, distance: float) -> float: ...


def _sanitize_distance(distance: float) -> Optional[float]:
    """Project arbitrary input onto a usable nonnegative float.

    Shared input gate for both placeholder and fitted calibrators —
    nonsensical inputs (negative / NaN / inf / None / unparseable)
    collapse to ``None`` so the calling calibrator can return 0.0
    uniformly. Better to under-claim than to silently report
    confidence for a broken distance.
    """
    if distance is None:
        return None
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return None
    if math.isnan(d) or math.isinf(d) or d < 0.0:
        return None
    return d


def _placeholder_calibrate(distance: float) -> float:
    """Exponential decay capped below HIGH. Pre-fit placeholder.

    Returns ``exp(-distance / SCALE)`` clamped to
    ``[0, PLACEHOLDER_CAP]``. The cap ensures HIGH is unreachable
    from this signal alone; MEDIUM-via-confidence remains reachable
    so genuinely close matches still surface.
    """
    d = _sanitize_distance(distance)
    if d is None:
        return 0.0
    raw = math.exp(-d / PLACEHOLDER_DISTANCE_SCALE)
    if raw > PLACEHOLDER_CONFIDENCE_CAP:
        return PLACEHOLDER_CONFIDENCE_CAP
    return raw


class IsotonicCalibrator:
    """Adapter from ``sklearn.isotonic.IsotonicRegression`` to ``Calibrator``.

    Encapsulates the input sanitization and output clamp that
    :func:`calibrate` owes its callers (NaN / inf / negative / None /
    unparseable → 0.0; output bounded to ``[0, 1]``) so the public
    surface looks identical whether the placeholder or the fitted
    model is active.

    The wrapped model is held by reference; ``__call__`` issues a
    single ``predict`` per call. We do not attempt to batch — the
    retrieval pipeline calls ``calibrate`` once per match per request,
    not in a hot loop.

    Constructed by :meth:`load_from_joblib`; not auto-imported because
    sklearn is a heavy dependency we'd rather pay for once (at the
    auto-loader call site) than on every import of this module.
    """

    def __init__(self, model: object) -> None:
        # ``model`` is duck-typed: any object exposing ``predict`` that
        # accepts a 1-D array-like and returns an iterable of floats
        # works. Tests substitute a tiny stub for this reason.
        self._model = model

    def __call__(self, distance: float) -> float:
        d = _sanitize_distance(distance)
        if d is None:
            return 0.0
        try:
            prediction = self._model.predict([d])  # type: ignore[attr-defined]
            raw = float(prediction[0])
        except Exception:
            # A misbehaving model is treated like a broken distance —
            # under-claim rather than propagate. The auto-loader has
            # already vetted the artifact at import time; this catch is
            # belt-and-braces for runtime numerics edge cases (e.g. the
            # model emitting NaN on an out-of-domain input).
            return 0.0
        if math.isnan(raw) or math.isinf(raw):
            return 0.0
        if raw < 0.0:
            return 0.0
        if raw > 1.0:
            return 1.0
        return raw

    @classmethod
    def load_from_joblib(cls, path: Path) -> "IsotonicCalibrator":
        """Load a fitted model from a joblib file.

        Raises if the file does not exist, cannot be deserialized, or
        the loaded object does not expose a ``predict`` method. The
        auto-loader catches all of these and falls back to the
        placeholder.
        """
        import joblib  # lazy: only pay the import on the load path

        model = joblib.load(path)
        if not hasattr(model, "predict"):
            raise ValueError(
                f"calibration artifact at {path} does not expose predict"
            )
        return cls(model)


def _try_load_fitted_calibrator() -> Optional[Calibrator]:
    """Return an ``IsotonicCalibrator`` if the artifact is loadable.

    Looks for ``calibration_v1.joblib`` next to this module. Never
    raises: a missing file, broken pickle, version-skewed sklearn, or
    any other deserialization failure logs a warning and yields
    ``None``, leaving the placeholder in charge. This is the same
    "never block on missing artifact" pattern the rest of the
    local-engine surface uses.
    """
    path = Path(__file__).parent / FITTED_MODEL_FILENAME
    if not path.exists():
        return None
    try:
        cal = IsotonicCalibrator.load_from_joblib(path)
    except Exception as exc:
        logger.warning(
            "calibration: failed to load %s, falling back to placeholder: %s",
            path.name, exc,
        )
        return None
    logger.info("calibration: loaded fitted isotonic model from %s", path.name)
    return cal


# Module-level swappable calibrator. The fitted artifact, when
# present alongside this module, replaces the placeholder at import
# time. The public ``calibrate`` function reads through this reference
# so the swap is invisible to callers — every dependent module gets
# the upgraded calibration without code changes.
_CALIBRATOR: Calibrator = _try_load_fitted_calibrator() or _placeholder_calibrate


def calibrate(distance: float) -> float:
    """Map a raw retrieval distance to calibrated confidence in ``[0, 1]``.

    Reads through the module-level ``_CALIBRATOR`` — the placeholder
    until the fitted isotonic model artifact lands, the fitted model
    afterward. Both paths sanitize input (nonsensical inputs collapse
    to 0.0) and bound output to ``[0, 1]``, so callers can rely on
    the contract regardless of which calibrator is active.
    """
    return _CALIBRATOR(distance)


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
    "FITTED_MODEL_FILENAME",
    "IsotonicCalibrator",
    "PLACEHOLDER_CONFIDENCE_CAP",
    "PLACEHOLDER_DISTANCE_SCALE",
    "calibrate",
    "compute_margin",
]
