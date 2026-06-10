"""End-to-end tests for ``tone.retrieve()``.

Pins the composition layer: clean candidates → calibrate → margin →
classify → policy → ``ToneMatch``. The three pure helpers each have
their own focused suites (test_tone_tiers / test_tone_calibration /
test_tone_policy); this file covers the orchestrator's branching and
the contract it promises ``session.bundle._build_tone`` (P6e).
"""

from __future__ import annotations

import math
from typing import Any, List, Mapping, Optional

import pytest

from tone_forge.contracts import (
    ConfidenceTier,
    SongUnderstanding,
    ToneMatch,
    UserRole,
)
from tone_forge.tone import MAX_ALTERNATES, retrieve
from tone_forge.tone import calibration, policy


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _candidate(
    preset_id: str,
    distance: float,
    *,
    preset_name: Optional[str] = None,
    instrument: str = "Analog",
    parameters: Optional[Mapping[str, Any]] = None,
    audio_preview_url: Optional[str] = None,
) -> dict:
    out: dict = {
        "preset_id": preset_id,
        "preset_name": preset_name or f"{preset_id}_name",
        "instrument": instrument,
        "distance": distance,
    }
    if parameters is not None:
        out["parameters"] = dict(parameters)
    if audio_preview_url is not None:
        out["audio_preview_url"] = audio_preview_url
    return out


def _understanding(
    *, tempo: float = 120.0, key: Optional[str] = "A minor"
) -> SongUnderstanding:
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
# Empty / no-signal path
# ---------------------------------------------------------------------------


def test_empty_candidates_returns_unknown_with_fallback() -> None:
    """No retrieval results at all — UNKNOWN tier, fallback chain set,
    no chosen candidate. Distinct from LOW (which means "we have a
    candidate but it's not good enough")."""
    match = retrieve([], understanding=_understanding())
    assert isinstance(match, ToneMatch)
    assert match.tier == ConfidenceTier.UNKNOWN
    assert match.chosen is None
    assert match.alternates == ()
    assert match.fallback_chain_id is not None
    assert match.fallback_chain_id.startswith("tfc.")
    assert match.debug.get("reason") == "empty_candidates"


def test_none_candidates_handled_gracefully() -> None:
    """Legacy callers may pass ``None`` for the preset_matches slot."""
    match = retrieve(None, understanding=_understanding())  # type: ignore[arg-type]
    assert match.tier == ConfidenceTier.UNKNOWN
    assert match.fallback_chain_id is not None


def test_empty_candidates_uses_policy_for_fallback_choice() -> None:
    """Fallback chain on UNKNOWN must come from the policy, so the
    understanding actually flows through."""
    fast = retrieve([], understanding=_understanding(tempo=160.0))
    assert fast.fallback_chain_id == policy.CHAIN_ID_MODERN_GAIN
    slow = retrieve([], understanding=_understanding(tempo=60.0))
    assert slow.fallback_chain_id == policy.CHAIN_ID_AMBIENT


def test_empty_candidates_without_understanding_uses_default_chain() -> None:
    match = retrieve([], understanding=None)
    assert match.tier == ConfidenceTier.UNKNOWN
    assert match.fallback_chain_id == policy.DEFAULT_CHAIN_ID


# ---------------------------------------------------------------------------
# Single-candidate (legacy preset_matches[role] shape) acceptance
# ---------------------------------------------------------------------------


def test_single_candidate_dict_is_accepted() -> None:
    """``preset_matches[role]`` is a *dict*, not a list. The cleaner
    must coerce it so legacy call sites don't have to re-shape."""
    single = _candidate("p1", 0.5)
    match = retrieve(single, understanding=_understanding())  # type: ignore[arg-type]
    assert match.tier in (ConfidenceTier.HIGH, ConfidenceTier.MEDIUM, ConfidenceTier.LOW)
    # Single-candidate has no margin signal → None.
    assert match.debug.get("margin") is None


def test_single_candidate_close_match_is_medium_not_high() -> None:
    """Even at distance=0 (pre-calibration max confidence), HIGH is
    unreachable because the placeholder calibrator caps below 0.80
    *and* a single candidate has no margin to satisfy HIGH_MARGIN_MIN.
    Pins the "auto-apply can't fire" invariant from P6b."""
    match = retrieve(_candidate("p1", 0.0), understanding=_understanding())
    assert match.tier != ConfidenceTier.HIGH


# ---------------------------------------------------------------------------
# Malformed-row filtering
# ---------------------------------------------------------------------------


def test_malformed_rows_dropped() -> None:
    """Rows missing ids, with NaN distances, or unparseable values are
    dropped silently; the cleaner must not raise on legacy junk."""
    candidates: List[Any] = [
        _candidate("good", 0.5),
        {"preset_id": "no_distance", "preset_name": "x", "instrument": "Analog"},
        {"preset_id": "", "preset_name": "x", "instrument": "Analog", "distance": 0.1},
        {"preset_id": "no_name", "preset_name": "", "instrument": "Analog", "distance": 0.1},
        {"preset_id": "nan_d", "preset_name": "y", "instrument": "Analog", "distance": float("nan")},
        {"preset_id": "neg_d", "preset_name": "y", "instrument": "Analog", "distance": -1.0},
        {"preset_id": "bad_d", "preset_name": "y", "instrument": "Analog", "distance": "wat"},
        "not a mapping",
        None,
    ]
    match = retrieve(candidates, understanding=_understanding())
    # Only one valid candidate survived → no margin signal.
    assert match.debug.get("n_candidates") == 1
    assert match.debug.get("margin") is None


def test_candidates_resorted_by_distance() -> None:
    """Cleaner is defensive: even if callers pass an unsorted list,
    the top candidate is the lowest-distance one."""
    candidates = [
        _candidate("far", 2.0),
        _candidate("near", 0.1),
        _candidate("mid", 1.0),
    ]
    match = retrieve(candidates, understanding=_understanding())
    if match.chosen is not None:
        assert match.chosen.preset_id == "near"


# ---------------------------------------------------------------------------
# LOW tier — chosen is None, fallback set
# ---------------------------------------------------------------------------


def test_low_tier_returns_fallback_no_chosen() -> None:
    """Far distances → low confidence + low margin → LOW tier.
    Contract: ``chosen`` is None, ``fallback_chain_id`` is set."""
    # Distance 10 → exp(-10) ≈ 4.5e-5 → confidence ≈ 0, well below
    # MEDIUM_CONFIDENCE_MIN. Margin between two far candidates is
    # also tiny → won't satisfy MEDIUM_MARGIN_MIN.
    candidates = [_candidate("p1", 10.0), _candidate("p2", 10.5)]
    match = retrieve(candidates, understanding=_understanding())
    assert match.tier == ConfidenceTier.LOW
    assert match.chosen is None
    assert match.alternates == ()
    assert match.fallback_chain_id is not None


def test_low_tier_fallback_routed_by_understanding() -> None:
    candidates = [_candidate("p1", 10.0), _candidate("p2", 10.5)]
    match = retrieve(candidates, understanding=_understanding(tempo=160.0))
    assert match.tier == ConfidenceTier.LOW
    assert match.fallback_chain_id == policy.CHAIN_ID_MODERN_GAIN


# ---------------------------------------------------------------------------
# MEDIUM tier — chosen + alternates (capped)
# ---------------------------------------------------------------------------


def test_medium_tier_populates_chosen_and_alternates() -> None:
    """Close top distance + large margin → MEDIUM (HIGH is unreachable
    pre-calibration). Chosen + up to MAX_ALTERNATES alternates."""
    candidates = [
        _candidate("top", 0.1),
        _candidate("alt1", 0.5),
        _candidate("alt2", 0.6),
        _candidate("alt3", 0.7),
    ]
    match = retrieve(candidates, understanding=_understanding())
    assert match.tier in (ConfidenceTier.HIGH, ConfidenceTier.MEDIUM)
    assert match.chosen is not None
    assert match.chosen.preset_id == "top"
    assert len(match.alternates) == MAX_ALTERNATES
    assert [a.preset_id for a in match.alternates] == ["alt1", "alt2"]
    assert match.fallback_chain_id is None


def test_alternates_have_their_own_calibrated_confidence() -> None:
    """Each alternate must carry its own ``calibrated_confidence`` —
    consumers display alternates next to the chosen one."""
    candidates = [
        _candidate("top", 0.1),
        _candidate("alt1", 0.4),
        _candidate("alt2", 0.6),
    ]
    match = retrieve(candidates, understanding=_understanding())
    assert match.chosen is not None
    chosen_conf = match.chosen.calibrated_confidence
    for alt in match.alternates:
        # Monotone calibrator: more distance → less confidence.
        assert alt.calibrated_confidence < chosen_conf


def test_alternates_capped_at_max() -> None:
    """A long retrieval list must not blow MEDIUM's UX budget."""
    candidates = [_candidate(f"p{i}", 0.1 + 0.05 * i) for i in range(20)]
    match = retrieve(candidates, understanding=_understanding())
    assert len(match.alternates) == MAX_ALTERNATES


# ---------------------------------------------------------------------------
# Debug block contract
# ---------------------------------------------------------------------------


def test_debug_block_carries_calibration_signals() -> None:
    """Telemetry & explainability rely on these keys being present."""
    candidates = [_candidate("top", 0.1), _candidate("alt", 0.5)]
    match = retrieve(
        candidates,
        understanding=_understanding(),
        role=UserRole.GUITAR,
    )
    assert "calibrated_confidence" in match.debug
    assert "margin" in match.debug
    assert "raw_distances" in match.debug
    assert match.debug["raw_distances"] == (0.1, 0.5)
    assert match.debug["n_candidates"] == 2
    assert match.debug["role"] == UserRole.GUITAR.value


def test_debug_role_none_when_role_missing() -> None:
    match = retrieve(
        [_candidate("p1", 0.5)],
        understanding=_understanding(),
    )
    assert match.debug["role"] is None


# ---------------------------------------------------------------------------
# Optional fields propagate
# ---------------------------------------------------------------------------


def test_optional_parameters_and_preview_url_forwarded() -> None:
    candidates = [
        _candidate(
            "top", 0.1,
            parameters={"gain": 0.5, "color": "red"},
            audio_preview_url="https://example/preview.wav",
        ),
    ]
    match = retrieve(candidates, understanding=_understanding())
    assert match.chosen is not None
    assert match.chosen.parameters == {"gain": 0.5, "color": "red"}
    assert match.chosen.audio_preview_url == "https://example/preview.wav"


def test_missing_optional_fields_default_safely() -> None:
    """A minimal candidate (no parameters, no preview url) must still
    produce a well-formed ToneCandidate."""
    match = retrieve([_candidate("top", 0.1)], understanding=_understanding())
    assert match.chosen is not None
    assert match.chosen.parameters == {}
    assert match.chosen.audio_preview_url is None


# ---------------------------------------------------------------------------
# Rationale string is non-empty and informative
# ---------------------------------------------------------------------------


def test_rationale_present_on_every_tier() -> None:
    matches = [
        retrieve([], understanding=_understanding()),  # UNKNOWN
        retrieve([_candidate("p", 10.0), _candidate("p2", 10.5)],
                 understanding=_understanding()),  # LOW
        retrieve([_candidate("p", 0.1), _candidate("p2", 0.5)],
                 understanding=_understanding()),  # MEDIUM
    ]
    for m in matches:
        assert isinstance(m.rationale, str)
        assert m.rationale  # non-empty


# ---------------------------------------------------------------------------
# Tier-invariant contract from the docstring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("candidates", [
    [],
    [_candidate("solo", 0.5)],
    [_candidate("top", 0.1), _candidate("alt", 0.5)],
    [_candidate("far", 10.0), _candidate("far2", 10.5)],
])
def test_tier_invariants(candidates: List[dict]) -> None:
    """Pin the docstring's invariants for every tier.

    * HIGH/MEDIUM → chosen populated, fallback None.
    * LOW/UNKNOWN → chosen None, fallback set.
    """
    match = retrieve(candidates, understanding=_understanding())
    if match.tier in (ConfidenceTier.HIGH, ConfidenceTier.MEDIUM):
        assert match.chosen is not None
        assert match.fallback_chain_id is None
    else:
        assert match.chosen is None
        assert match.alternates == ()
        assert match.fallback_chain_id is not None


# ---------------------------------------------------------------------------
# preferred_family forwarding (Priority 7 — DeviceCaps consumer wiring)
# ---------------------------------------------------------------------------

def test_preferred_family_overrides_fallback_chain_on_unknown() -> None:
    """Empty candidates → UNKNOWN; the pinned family must beat the
    EDGE_OF_BREAKUP default. Covers the path the API edge takes when
    the user has answered the onboarding question."""
    from tone_forge.contracts import MonitorChainFamily

    match = retrieve(
        [],
        understanding=_understanding(),
        preferred_family=MonitorChainFamily.AMBIENT,
    )
    assert match.tier == ConfidenceTier.UNKNOWN
    assert match.fallback_chain_id == policy.CHAIN_ID_AMBIENT


def test_preferred_family_overrides_fallback_chain_on_low() -> None:
    """Far-distance candidates → LOW; same override behavior applies."""
    from tone_forge.contracts import MonitorChainFamily

    match = retrieve(
        [_candidate("far", 10.0)],
        understanding=_understanding(tempo=160.0),
        preferred_family=MonitorChainFamily.CLASSIC_ROCK,
    )
    assert match.tier == ConfidenceTier.LOW
    assert match.fallback_chain_id == policy.CHAIN_ID_CLASSIC_ROCK


def test_preferred_family_does_not_affect_high_tier() -> None:
    """HIGH-tier matches still emit ``chosen``; the override only
    influences fallback ids, not preset selection."""
    from tone_forge.contracts import MonitorChainFamily

    match = retrieve(
        [_candidate("top", 0.1), _candidate("alt", 5.0)],
        understanding=_understanding(),
        preferred_family=MonitorChainFamily.AMBIENT,
    )
    assert match.tier in (ConfidenceTier.HIGH, ConfidenceTier.MEDIUM)
    assert match.chosen is not None
    assert match.fallback_chain_id is None
