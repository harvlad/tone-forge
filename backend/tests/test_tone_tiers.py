"""ConfidenceTier classifier behavior.

These thresholds drive whether Jam auto-applies a preset or falls back
to a curated monitor chain. The HIGH boundary in particular is the
"don't bug the user" line; we pin the policy with explicit tests so a
threshold change is never accidental.
"""

from __future__ import annotations

import math

import pytest

from tone_forge.contracts import ConfidenceTier
from tone_forge.tone import tiers


# ---------------------------------------------------------------------------
# HIGH boundary
# ---------------------------------------------------------------------------

def test_high_when_both_signals_clear() -> None:
    assert tiers.classify(0.90, 0.30) == ConfidenceTier.HIGH


def test_high_exact_thresholds() -> None:
    """HIGH thresholds are inclusive."""
    assert tiers.classify(
        tiers.HIGH_CONFIDENCE_MIN, tiers.HIGH_MARGIN_MIN
    ) == ConfidenceTier.HIGH


def test_high_demotes_to_medium_when_margin_low() -> None:
    """High confidence alone is not HIGH — that requires margin too."""
    assert tiers.classify(0.95, 0.10) == ConfidenceTier.MEDIUM


def test_high_demotes_to_medium_when_confidence_just_under() -> None:
    assert tiers.classify(0.79, 0.50) == ConfidenceTier.MEDIUM


def test_high_unreachable_without_margin_signal() -> None:
    """Legacy data lacks margin — HIGH must be impossible there.
    Otherwise we'd auto-apply a top-1 we have no separation evidence
    for, which is exactly what calibration is meant to prevent."""
    assert tiers.classify(0.99, None) == ConfidenceTier.MEDIUM


# ---------------------------------------------------------------------------
# MEDIUM branches
# ---------------------------------------------------------------------------

def test_medium_via_confidence_alone() -> None:
    """The OR clause: high confidence even with tight competition."""
    assert tiers.classify(0.70, 0.05) == ConfidenceTier.MEDIUM


def test_medium_via_margin_alone() -> None:
    """The OR clause: clear winner even with modest confidence."""
    assert tiers.classify(0.40, 0.15) == ConfidenceTier.MEDIUM


def test_medium_exact_confidence_threshold() -> None:
    assert tiers.classify(
        tiers.MEDIUM_CONFIDENCE_MIN, 0.0
    ) == ConfidenceTier.MEDIUM


def test_medium_exact_margin_threshold() -> None:
    assert tiers.classify(0.0, tiers.MEDIUM_MARGIN_MIN) == ConfidenceTier.MEDIUM


# ---------------------------------------------------------------------------
# LOW
# ---------------------------------------------------------------------------

def test_low_when_both_signals_weak() -> None:
    assert tiers.classify(0.30, 0.05) == ConfidenceTier.LOW


def test_low_when_no_margin_and_low_confidence() -> None:
    assert tiers.classify(0.40, None) == ConfidenceTier.LOW


def test_low_at_zero() -> None:
    assert tiers.classify(0.0, 0.0) == ConfidenceTier.LOW


# ---------------------------------------------------------------------------
# Defensive coercion
# ---------------------------------------------------------------------------

def test_confidence_above_one_clamps_down() -> None:
    """A calibrator regression that emits >1.0 must not unlock HIGH on
    its own — clamp first, then apply policy."""
    assert tiers.classify(1.5, 0.25) == ConfidenceTier.HIGH  # still ok
    assert tiers.classify(1.5, 0.05) == ConfidenceTier.MEDIUM  # capped at 1.0


def test_confidence_below_zero_clamps_up() -> None:
    assert tiers.classify(-0.5, 0.05) == ConfidenceTier.LOW


def test_negative_margin_is_treated_as_zero() -> None:
    """Margin < 0 is impossible — runner-up cannot beat the top result.
    Defend against upstream bugs by zeroing it rather than rewarding
    the inversion with a HIGH tier."""
    assert tiers.classify(0.95, -0.5) == ConfidenceTier.MEDIUM


def test_nan_confidence_falls_to_low() -> None:
    assert tiers.classify(math.nan, 0.5) == ConfidenceTier.MEDIUM
    assert tiers.classify(math.nan, 0.0) == ConfidenceTier.LOW


def test_nan_margin_is_treated_as_zero() -> None:
    assert tiers.classify(0.95, math.nan) == ConfidenceTier.MEDIUM


# ---------------------------------------------------------------------------
# Constants are exposed
# ---------------------------------------------------------------------------

def test_thresholds_exposed_as_module_constants() -> None:
    """Calibration refit cadence pokes these directly; assert they
    exist so a rename breaks here, not in a downstream consumer."""
    assert tiers.HIGH_CONFIDENCE_MIN == pytest.approx(0.80)
    assert tiers.HIGH_MARGIN_MIN == pytest.approx(0.20)
    assert tiers.MEDIUM_CONFIDENCE_MIN == pytest.approx(0.55)
    assert tiers.MEDIUM_MARGIN_MIN == pytest.approx(0.10)


def test_classify_never_returns_unknown() -> None:
    """UNKNOWN is the caller's signal for retrieval failure, not a
    classifier output. Walk a grid and assert."""
    for c in [0.0, 0.25, 0.55, 0.80, 1.0]:
        for m in [None, 0.0, 0.10, 0.20, 0.5]:
            tier = tiers.classify(c, m)
            assert tier != ConfidenceTier.UNKNOWN
