"""Unit tests for Stage 1.1 — key-strength-weighted diatonic bias.

The Krumhansl-Schmuckler key detection in ``_detect_key_from_chroma``
returns ``(root, mode, key_strength)`` where ``key_strength`` ∈ [0, 1]
captures how dominant the best-fitting key is over the runner-up.
Strong-key songs (key_strength ≈ 1) should receive the full historical
diatonic bias; tonally ambiguous or modulating songs (key_strength near
0) should see the bias relax proportionally so a flat 0.10 boost
doesn't drag them into a single-key reading.

These tests pin the contract of the ``key_strength`` calculation
itself. The effect on chord decisions is observable at the regression
suite level (``test_chord_eval_regression.py``).
"""

import numpy as np

from tone_forge.analysis import chord_detector


def test_key_strength_strong_key_clean_major_triad_aggregate() -> None:
    """A clean C-major triad chroma yields a high key_strength.

    Three pitch classes (C, E, G) lit at equal mass — the C-major
    Krumhansl profile dominates rotated alternatives by a comfortable
    margin, so ``key_strength`` should land near the upper end of
    [0, 1] and clip to 1.0 when the margin exceeds the calibration
    normaliser.
    """
    chroma = np.zeros((12, 1))
    for pc in (0, 4, 7):  # C, E, G
        chroma[pc, 0] = 1.0
    root, mode, strength = chord_detector._detect_key_from_chroma(chroma)
    assert root == 0
    assert mode == 'major'
    assert 0.5 <= strength <= 1.0, (
        f"clean C-major chord-tone aggregate should yield strong "
        f"key_strength, got {strength}"
    )


def test_key_strength_ambiguous_uniform_chroma_low() -> None:
    """A uniform chroma vector (no harmonic structure) yields low key_strength.

    All 12 pitch classes lit equally — every rotated Krumhansl
    profile correlates identically, so the best/second-best margin is
    essentially zero and ``key_strength`` should clip to 0.
    """
    chroma = np.ones((12, 1)) * 0.5
    _root, _mode, strength = chord_detector._detect_key_from_chroma(chroma)
    assert strength == 0.0, (
        f"uniform chroma should yield zero key_strength, got {strength}"
    )


def test_key_strength_in_unit_interval() -> None:
    """``key_strength`` is guaranteed to live in [0, 1] for any input.

    Smoke test across a handful of random non-negative chroma vectors
    that the clip + normalise math never produces a value outside the
    contracted range.
    """
    rng = np.random.default_rng(seed=20251115)
    for _ in range(20):
        chroma = np.clip(rng.normal(loc=0.5, scale=0.3, size=(12, 5)), 0.0, None)
        _root, _mode, strength = chord_detector._detect_key_from_chroma(chroma)
        assert 0.0 <= strength <= 1.0, (
            f"key_strength out of contracted range [0, 1]: {strength}"
        )


def test_key_strength_zero_chroma_returns_zero_strength() -> None:
    """All-zero chroma falls back to (C, major, 0.0).

    Defensive contract: an empty/silent input should never raise and
    should report ``key_strength=0`` so the scaled bias falls to 0
    and the historical fixed-bias path is disabled.
    """
    chroma = np.zeros((12, 1))
    root, mode, strength = chord_detector._detect_key_from_chroma(chroma)
    assert root == 0 and mode == 'major'
    assert strength == 0.0


def test_key_strength_margin_norm_constant_pinned() -> None:
    """Pin the calibration constant so changes are explicit.

    ``_KEY_STRENGTH_MARGIN_NORM`` normalises the Krumhansl best /
    second-best correlation margin into a [0, 1] strength signal
    consumed by Stage 1.2's relative-major/minor key tie-breaker.
    Calibrated to 0.01 because real-audio fixture margins span
    ~0.0004-0.006; an earlier 0.05 calibration was based on
    documentation-asserted (not measured) margins and saturated to ~0
    on every fixture, killing the signal entirely.
    """
    assert chord_detector._KEY_STRENGTH_MARGIN_NORM == 0.01
