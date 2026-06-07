"""Fallback monitor-chain selection for LOW / UNKNOWN tiers.

When retrieval cannot land a confident match, Jam still has to deliver
sound. The selector here picks a curated ``MonitorChain`` family based
on what we know about the song. The chosen chain id is returned as a
string (e.g. ``"tfc.classic_rock"``) and the monitor subsystem (P3)
owns the actual chain spec keyed by that id.

Plan §7 specifies the full heuristic in terms of tempo + key + several
spectral features (centroid, flux, texture density, reverb tail).
``SongUnderstanding`` carries tempo and key today; the spectral
features are queued for the analysis subsystem in a later commit. So
this module implements the *available-data* policy that uses tempo +
key only, with the spectral-feature branches documented for the
follow-on. Behavior degrades to the safest default
(``edge_of_breakup``) when signals are missing.

The chain ids follow the ``tfc.<family>`` namespace from Plan §3:

  * ``tfc.clean_strat``      — CLEAN
  * ``tfc.edge_of_breakup``  — EDGE_OF_BREAKUP   (always-safe default)
  * ``tfc.classic_rock``     — CLASSIC_ROCK
  * ``tfc.modern_gain``      — MODERN_GAIN
  * ``tfc.ambient``          — AMBIENT
"""

from __future__ import annotations

from typing import Optional

from tone_forge.contracts import MonitorChainFamily, SongUnderstanding

# Tempo zone thresholds. Pinned at the literals the plan uses so the
# decisions are auditable. Refits live in a CHANGELOG entry, not a
# silent literal change.
FAST_TEMPO_BPM: float = 140.0
SLOW_TEMPO_BPM: float = 90.0

# Chain id constants. Centralized here so consumers can ``import``
# rather than string-match downstream.
CHAIN_ID_CLEAN_STRAT: str = "tfc.clean_strat"
CHAIN_ID_EDGE_OF_BREAKUP: str = "tfc.edge_of_breakup"
CHAIN_ID_CLASSIC_ROCK: str = "tfc.classic_rock"
CHAIN_ID_MODERN_GAIN: str = "tfc.modern_gain"
CHAIN_ID_AMBIENT: str = "tfc.ambient"

# Family → chain id map. The chain bank in P3 may eventually carry
# multiple chains per family; until then the 1:1 mapping is exact.
FAMILY_TO_CHAIN_ID: dict[MonitorChainFamily, str] = {
    MonitorChainFamily.CLEAN: CHAIN_ID_CLEAN_STRAT,
    MonitorChainFamily.EDGE_OF_BREAKUP: CHAIN_ID_EDGE_OF_BREAKUP,
    MonitorChainFamily.CLASSIC_ROCK: CHAIN_ID_CLASSIC_ROCK,
    MonitorChainFamily.MODERN_GAIN: CHAIN_ID_MODERN_GAIN,
    MonitorChainFamily.AMBIENT: CHAIN_ID_AMBIENT,
}

DEFAULT_CHAIN_ID: str = CHAIN_ID_EDGE_OF_BREAKUP


def select_fallback_chain(
    understanding: Optional[SongUnderstanding],
) -> str:
    """Pick a monitor-chain id from what we know about the song.

    Returns a chain id string. Never returns ``None`` — Jam always
    needs *some* sound on the LOW path, so missing signals route to
    the always-safe ``edge_of_breakup`` default.
    """
    family = select_fallback_family(understanding)
    return FAMILY_TO_CHAIN_ID[family]


def select_fallback_family(
    understanding: Optional[SongUnderstanding],
) -> MonitorChainFamily:
    """Same decision as ``select_fallback_chain`` but typed as the
    family enum. Useful when the caller wants to read the policy
    decision without coupling to the chain-id string format."""
    if understanding is None:
        return MonitorChainFamily.EDGE_OF_BREAKUP

    tempo = _safe_tempo(understanding.tempo_bpm)
    is_major = _is_major_key(understanding.key)

    # Fast and aggressive — Plan §7: tempo > 140 + heavy spectral
    # centroid → modern_gain. Spectral centroid is unavailable; tempo
    # alone is a usable proxy for "this song is leaning hard".
    if tempo is not None and tempo > FAST_TEMPO_BPM:
        return MonitorChainFamily.MODERN_GAIN

    # Slow and spacious — Plan §7: tempo < 100 + sparse texture +
    # reverb tail → ambient. Without texture/reverb signals, take the
    # tighter tempo cut as the heuristic (only slow songs route here).
    if tempo is not None and tempo < SLOW_TEMPO_BPM:
        return MonitorChainFamily.AMBIENT

    # Mid-tempo: split on key. The plan's full rule is "major key +
    # low spectral flux → clean_strat" but flux isn't in the bundle
    # yet, so major-key alone routes to clean. Minor and unknown go
    # to classic_rock, which is the most defensible mid-gain choice
    # for the bluesy / mid-heavy slot the plan calls out.
    if tempo is not None and SLOW_TEMPO_BPM <= tempo <= FAST_TEMPO_BPM:
        if is_major:
            return MonitorChainFamily.CLEAN
        return MonitorChainFamily.CLASSIC_ROCK

    # No tempo signal at all — safest fall-through. Edge-of-breakup
    # is the "works on anything" chain per Plan §3.
    return MonitorChainFamily.EDGE_OF_BREAKUP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_tempo(tempo_bpm: float) -> Optional[float]:
    """``tempo_bpm == 0.0`` is the conservative "no estimate" sentinel
    used by ``session.bundle._resolve_tempo``. Treat it as missing.

    Negative tempos are impossible; return ``None`` so the selector
    falls through to the default path rather than picking a tempo
    branch on bogus data.
    """
    if tempo_bpm is None:
        return None
    try:
        t = float(tempo_bpm)
    except (TypeError, ValueError):
        return None
    if t <= 0.0:
        return None
    if t != t:  # NaN
        return None
    return t


def _is_major_key(key: Optional[str]) -> bool:
    """Major iff the key string contains 'major' (case-insensitive).

    Matches the legacy key-string format ("C major", "A minor", "F#
    major") that bubbles up from ``descriptor.detected_key``. Bare
    pitch-class strings ("C", "G#") are treated as not-major because
    we cannot infer modality; the selector then routes to the
    classic_rock / edge_of_breakup branches that are safer choices for
    unknown modality.
    """
    if not isinstance(key, str):
        return False
    return "major" in key.lower()


__all__ = [
    "CHAIN_ID_AMBIENT",
    "CHAIN_ID_CLASSIC_ROCK",
    "CHAIN_ID_CLEAN_STRAT",
    "CHAIN_ID_EDGE_OF_BREAKUP",
    "CHAIN_ID_MODERN_GAIN",
    "DEFAULT_CHAIN_ID",
    "FAMILY_TO_CHAIN_ID",
    "FAST_TEMPO_BPM",
    "SLOW_TEMPO_BPM",
    "select_fallback_chain",
    "select_fallback_family",
]
