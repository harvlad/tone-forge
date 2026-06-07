"""Calibration helpers behavior.

Two pure functions to pin:
  * ``calibrate`` — placeholder confidence curve. Properties checked
    rather than exact values, since the placeholder is a stand-in for
    the fitted model that arrives in P6.1.
  * ``compute_margin`` — margin formula. Exact values pinned.
"""

from __future__ import annotations

import math

import pytest

from tone_forge.tone import calibration, tiers


# ---------------------------------------------------------------------------
# calibrate — placeholder properties
# ---------------------------------------------------------------------------

def test_calibrate_returns_value_in_unit_interval() -> None:
    """Walk a coarse grid and assert the output is always usable as
    confidence input to the tier classifier."""
    for d in [0.0, 0.1, 0.5, 1.0, 2.0, 5.0, 50.0]:
        c = calibration.calibrate(d)
        assert 0.0 <= c <= 1.0


def test_calibrate_is_monotone_decreasing_in_distance() -> None:
    """Closer matches must yield higher confidence — pin the property
    so a future refactor can't silently invert the relationship."""
    samples = [calibration.calibrate(d) for d in [0.0, 0.25, 0.5, 1.0, 2.0]]
    for prev, cur in zip(samples, samples[1:]):
        assert cur <= prev


def test_calibrate_caps_below_high_threshold() -> None:
    """The placeholder must never produce a confidence that on its own
    could unlock the HIGH tier — that's the whole point of the cap.
    The fitted isotonic model lifts this constraint."""
    assert calibration.PLACEHOLDER_CONFIDENCE_CAP < tiers.HIGH_CONFIDENCE_MIN
    # At distance=0 the bare exponential is 1.0; the cap must clip it.
    assert calibration.calibrate(0.0) == calibration.PLACEHOLDER_CONFIDENCE_CAP


def test_calibrate_can_reach_medium() -> None:
    """The placeholder must still be useful: genuinely close matches
    should be able to clear the MEDIUM-via-confidence threshold so the
    user sees a suggestion."""
    # Need calibrate(d) >= MEDIUM_CONFIDENCE_MIN (0.55) for some small d.
    assert calibration.calibrate(0.1) >= tiers.MEDIUM_CONFIDENCE_MIN


def test_calibrate_large_distance_approaches_zero() -> None:
    assert calibration.calibrate(100.0) == pytest.approx(0.0, abs=1e-6)


# Defensive inputs

def test_calibrate_negative_distance_returns_zero() -> None:
    assert calibration.calibrate(-1.0) == 0.0


def test_calibrate_nan_returns_zero() -> None:
    assert calibration.calibrate(math.nan) == 0.0


def test_calibrate_inf_returns_zero() -> None:
    assert calibration.calibrate(math.inf) == 0.0


def test_calibrate_none_returns_zero() -> None:
    assert calibration.calibrate(None) == 0.0  # type: ignore[arg-type]


def test_calibrate_string_returns_zero() -> None:
    assert calibration.calibrate("not a number") == 0.0  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# compute_margin
# ---------------------------------------------------------------------------

def test_compute_margin_basic() -> None:
    """``(d2 - d1) / d1`` — clear formula, pin exact value."""
    m = calibration.compute_margin([0.5, 1.0, 1.5])
    assert m == pytest.approx((1.0 - 0.5) / 0.5)  # 1.0


def test_compute_margin_returns_none_for_single_distance() -> None:
    """Legacy preset_matches persist only top-1 — must report no
    margin signal, not zero (those are different policy outcomes)."""
    assert calibration.compute_margin([0.5]) is None


def test_compute_margin_returns_none_for_empty() -> None:
    assert calibration.compute_margin([]) is None


def test_compute_margin_returns_none_for_none() -> None:
    assert calibration.compute_margin(None) is None  # type: ignore[arg-type]


def test_compute_margin_sorts_defensively() -> None:
    """Caller might pass a view that isn't sorted; result is identical
    to the sorted-input case."""
    sorted_m = calibration.compute_margin([0.5, 1.0, 1.5])
    unsorted_m = calibration.compute_margin([1.5, 0.5, 1.0])
    assert sorted_m == unsorted_m


def test_compute_margin_zero_when_tied() -> None:
    """Top two distances identical — explicit zero, not None.
    The classifier reads this as 'no separation', a genuine signal."""
    m = calibration.compute_margin([0.7, 0.7])
    assert m == pytest.approx(0.0)


def test_compute_margin_returns_none_for_zero_top() -> None:
    """Division by zero — self-match or degenerate. Report no signal
    rather than infinity."""
    assert calibration.compute_margin([0.0, 0.5]) is None


def test_compute_margin_skips_nonfinite() -> None:
    """NaN / inf inputs are dropped; remaining finite distances feed
    the formula. Two finites left → real margin."""
    m = calibration.compute_margin([0.5, math.nan, 1.0, math.inf])
    assert m == pytest.approx(1.0)


def test_compute_margin_returns_none_when_only_one_finite() -> None:
    assert calibration.compute_margin([0.5, math.nan, math.inf]) is None


def test_compute_margin_returns_none_for_unparseable() -> None:
    """A string in the middle of the list should drop the call to
    None — we won't silently pretend the distance vector was clean."""
    assert calibration.compute_margin([0.5, "garbage", 1.0]) is None  # type: ignore[list-item]


def test_compute_margin_skips_negative() -> None:
    """Negative distances are impossible from a valid L2 norm; drop
    them and recompute on the rest."""
    m = calibration.compute_margin([0.5, -1.0, 1.0])
    assert m == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Composition with classifier
# ---------------------------------------------------------------------------

def test_pipeline_smoke_close_match_lands_medium() -> None:
    """End-to-end smoke: a close top distance with a clear runner-up
    gap should land MEDIUM. Pre-calibration, HIGH is unreachable by
    design."""
    distances = [0.2, 1.5, 2.0]  # tiny top, big gap
    conf = calibration.calibrate(distances[0])
    margin = calibration.compute_margin(distances)
    tier = tiers.classify(conf, margin)
    assert tier == tiers.ConfidenceTier.MEDIUM


def test_pipeline_smoke_far_match_lands_low() -> None:
    distances = [3.0, 3.1, 3.2]
    conf = calibration.calibrate(distances[0])
    margin = calibration.compute_margin(distances)
    tier = tiers.classify(conf, margin)
    assert tier == tiers.ConfidenceTier.LOW
