"""Fallback-chain selection policy.

Pins the routing decisions used when retrieval lands LOW or UNKNOWN.
Jam always needs *some* sound, so every branch must return a chain id
— assertions also cover the missing-signal degradation paths.
"""

from __future__ import annotations

from typing import Optional

import pytest

from tone_forge.contracts import MonitorChainFamily, SongUnderstanding
from tone_forge.tone import policy


def _understanding(
    *, tempo: float = 120.0, key: Optional[str] = None
) -> SongUnderstanding:
    """Minimal SongUnderstanding for policy tests.

    Only tempo and key drive selection today; the other fields are
    populated with empty defaults so the dataclass instantiates."""
    return SongUnderstanding(
        tempo_bpm=tempo,
        tempo_confidence=0.5,
        time_signature=(4, 4),
        beats_s=(),
        downbeats_s=(),
        sections=(),
        chords=(),
        key=key,
        key_confidence=0.5 if key else 0.0,
    )


# ---------------------------------------------------------------------------
# Tempo zones
# ---------------------------------------------------------------------------

def test_fast_tempo_routes_to_modern_gain() -> None:
    u = _understanding(tempo=160.0)
    assert policy.select_fallback_family(u) == MonitorChainFamily.MODERN_GAIN
    assert policy.select_fallback_chain(u) == policy.CHAIN_ID_MODERN_GAIN


def test_slow_tempo_routes_to_ambient() -> None:
    u = _understanding(tempo=70.0)
    assert policy.select_fallback_family(u) == MonitorChainFamily.AMBIENT


def test_mid_tempo_major_key_routes_to_clean() -> None:
    u = _understanding(tempo=120.0, key="C major")
    assert policy.select_fallback_family(u) == MonitorChainFamily.CLEAN


def test_mid_tempo_minor_key_routes_to_classic_rock() -> None:
    u = _understanding(tempo=120.0, key="A minor")
    assert policy.select_fallback_family(u) == MonitorChainFamily.CLASSIC_ROCK


def test_mid_tempo_unknown_key_routes_to_classic_rock() -> None:
    """Unknown modality goes to the defensible mid-gain default; we
    don't promote to clean unless we *know* it's major."""
    u = _understanding(tempo=120.0, key=None)
    assert policy.select_fallback_family(u) == MonitorChainFamily.CLASSIC_ROCK


# ---------------------------------------------------------------------------
# Tempo boundaries — pin the inclusive/exclusive sides
# ---------------------------------------------------------------------------

def test_exact_fast_tempo_threshold_is_not_modern_gain() -> None:
    """Plan §7 reads 'tempo > 140' — strict. Exactly 140 stays in the
    mid-tempo zone, routed by key."""
    u = _understanding(tempo=policy.FAST_TEMPO_BPM, key="C major")
    assert policy.select_fallback_family(u) == MonitorChainFamily.CLEAN


def test_exact_slow_tempo_threshold_is_not_ambient() -> None:
    """Plan §7 reads 'tempo < 100' but we tightened to <90 since we
    can't read texture/reverb signals. Exactly 90 stays mid-tempo."""
    u = _understanding(tempo=policy.SLOW_TEMPO_BPM, key="A minor")
    assert policy.select_fallback_family(u) == MonitorChainFamily.CLASSIC_ROCK


def test_just_above_fast_tempo_threshold_routes_modern_gain() -> None:
    u = _understanding(tempo=policy.FAST_TEMPO_BPM + 0.1)
    assert policy.select_fallback_family(u) == MonitorChainFamily.MODERN_GAIN


def test_just_below_slow_tempo_threshold_routes_ambient() -> None:
    u = _understanding(tempo=policy.SLOW_TEMPO_BPM - 0.1)
    assert policy.select_fallback_family(u) == MonitorChainFamily.AMBIENT


# ---------------------------------------------------------------------------
# Degradation paths — missing or malformed signals
# ---------------------------------------------------------------------------

def test_none_understanding_returns_safe_default() -> None:
    """No bundle / no analysis — must still return a usable chain id."""
    assert policy.select_fallback_family(None) == MonitorChainFamily.EDGE_OF_BREAKUP
    assert policy.select_fallback_chain(None) == policy.CHAIN_ID_EDGE_OF_BREAKUP


def test_zero_tempo_is_treated_as_missing() -> None:
    """``tempo_bpm == 0.0`` is the bundle assembler's "no estimate"
    sentinel. Selector must not pick a tempo branch on it."""
    u = _understanding(tempo=0.0)
    assert policy.select_fallback_family(u) == MonitorChainFamily.EDGE_OF_BREAKUP


def test_negative_tempo_is_treated_as_missing() -> None:
    u = _understanding(tempo=-50.0)
    assert policy.select_fallback_family(u) == MonitorChainFamily.EDGE_OF_BREAKUP


def test_nan_tempo_is_treated_as_missing() -> None:
    import math
    u = _understanding(tempo=math.nan)
    assert policy.select_fallback_family(u) == MonitorChainFamily.EDGE_OF_BREAKUP


def test_major_detection_is_case_insensitive() -> None:
    """Legacy key strings are inconsistent ("C Major" vs "C major")."""
    u_upper = _understanding(tempo=120.0, key="C Major")
    u_lower = _understanding(tempo=120.0, key="c major")
    assert (
        policy.select_fallback_family(u_upper)
        == policy.select_fallback_family(u_lower)
        == MonitorChainFamily.CLEAN
    )


def test_bare_pitch_class_key_is_not_major() -> None:
    """Bare 'C' has unknown modality — must not be promoted to clean.
    Mid-tempo + unknown modality is the classic_rock branch."""
    u = _understanding(tempo=120.0, key="C")
    assert policy.select_fallback_family(u) == MonitorChainFamily.CLASSIC_ROCK


# ---------------------------------------------------------------------------
# Chain id contract
# ---------------------------------------------------------------------------

def test_family_to_chain_id_covers_every_family() -> None:
    """Every MonitorChainFamily value must be reachable from the map —
    if a family is added in contracts.py without a chain id, this test
    breaks on the next refactor pass."""
    for family in MonitorChainFamily:
        assert family in policy.FAMILY_TO_CHAIN_ID


def test_default_chain_id_is_edge_of_breakup() -> None:
    """Pin the policy's safe-default choice — Plan §3 puts edge of
    breakup as the 'works on anything' fallback."""
    assert policy.DEFAULT_CHAIN_ID == policy.CHAIN_ID_EDGE_OF_BREAKUP


def test_chain_id_namespace_is_tfc_prefix() -> None:
    """All chain ids live under the 'tfc.' namespace; pin so a future
    rename of one id doesn't drift away from the rest."""
    for chain_id in policy.FAMILY_TO_CHAIN_ID.values():
        assert chain_id.startswith("tfc."), chain_id


@pytest.mark.parametrize("tempo,key,expected_id", [
    (160.0, None, policy.CHAIN_ID_MODERN_GAIN),
    (160.0, "C major", policy.CHAIN_ID_MODERN_GAIN),  # tempo dominates
    (60.0, None, policy.CHAIN_ID_AMBIENT),
    (120.0, "G major", policy.CHAIN_ID_CLEAN_STRAT),
    (120.0, "E minor", policy.CHAIN_ID_CLASSIC_ROCK),
    (120.0, None, policy.CHAIN_ID_CLASSIC_ROCK),
    (0.0, None, policy.CHAIN_ID_EDGE_OF_BREAKUP),
])
def test_select_fallback_chain_matrix(
    tempo: float, key: Optional[str], expected_id: str
) -> None:
    """Tabular pin of the entire decision surface so a future tweak is
    a one-line diff in this matrix."""
    u = _understanding(tempo=tempo, key=key)
    assert policy.select_fallback_chain(u) == expected_id


# ---------------------------------------------------------------------------
# preferred_family override (Priority 7 — DeviceCaps consumer wiring)
# ---------------------------------------------------------------------------

def test_preferred_family_overrides_tempo_heuristic() -> None:
    """User pinned AMBIENT in onboarding; even a fast song must route
    to AMBIENT instead of MODERN_GAIN. The persisted answer always
    wins because the heuristic is a guess and the answer is not."""
    u = _understanding(tempo=160.0)
    family = policy.select_fallback_family(
        u, preferred_family=MonitorChainFamily.AMBIENT,
    )
    assert family is MonitorChainFamily.AMBIENT
    assert policy.select_fallback_chain(
        u, preferred_family=MonitorChainFamily.AMBIENT,
    ) == policy.CHAIN_ID_AMBIENT


def test_preferred_family_overrides_missing_understanding() -> None:
    """``understanding=None`` would default to EDGE_OF_BREAKUP, but a
    pinned family must still take precedence on the LOW path."""
    assert policy.select_fallback_chain(
        None, preferred_family=MonitorChainFamily.CLASSIC_ROCK,
    ) == policy.CHAIN_ID_CLASSIC_ROCK


def test_preferred_family_none_falls_back_to_heuristic() -> None:
    """The override is opt-in — ``preferred_family=None`` (the default)
    must leave the existing decision surface untouched."""
    u = _understanding(tempo=160.0)
    assert policy.select_fallback_chain(
        u, preferred_family=None,
    ) == policy.CHAIN_ID_MODERN_GAIN
