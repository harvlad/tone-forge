"""Tone Retrieval (Jam-facing wrapper).

Composes the three pure helpers in this package into the single
``retrieve()`` entry point that returns a tier-aware ``ToneMatch``.

The composition is intentionally narrow — this subsystem owns the
*policy* boundary, not the retrieval algorithm itself. The actual
distance calculation lives in the frozen ``preset_catalog`` subsystem
and is wired in at the API edge (``tone_forge_api``). Callers pass us
already-retrieved candidates (list of dicts with at minimum
``preset_id`` / ``preset_name`` / ``instrument`` / ``distance``);
``retrieve()`` calibrates, classifies, and chooses a fallback chain
when needed.

Dependency injection on the retriever keeps ``tone/`` boundary-clean —
no import of ``preset_catalog`` from this subsystem. The boundary test
in ``tests/test_subsystem_boundaries.py`` enforces it.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence

from tone_forge.contracts import (
    ConfidenceTier,
    MonitorChainFamily,
    SongUnderstanding,
    ToneCandidate,
    ToneMatch,
    UserRole,
)
from tone_forge.tone import calibration, policy, tiers

MAX_ALTERNATES: int = 2  # Plan §7 UX: MEDIUM shows top + 2 alternates.


def retrieve(
    candidates: Sequence[Mapping[str, Any]],
    *,
    understanding: Optional[SongUnderstanding] = None,
    role: Optional[UserRole] = None,
    preferred_family: Optional[MonitorChainFamily] = None,
) -> ToneMatch:
    """Project a list of retrieval candidates onto a tier-aware ``ToneMatch``.

    Parameters
    ----------
    candidates
        Top-k from the frozen catalog retriever, in distance-ascending
        order. Each dict must carry ``preset_id``, ``preset_name``,
        ``instrument``, and ``distance``. Extra keys (``parameters``,
        ``audio_path``, ...) are forwarded into ``ToneCandidate``
        where the contract has a slot for them. The list may be empty;
        we surface that as ``UNKNOWN`` with a chosen fallback chain.
    understanding
        Optional ``SongUnderstanding``. Used only for fallback-chain
        selection on LOW / UNKNOWN tiers — the tier decision itself
        depends only on the candidate distances.
    role
        Optional ``UserRole``. Currently used only in the ``debug``
        block for telemetry attribution; the policy does not branch
        on role. Kept in the signature so future role-specific
        calibration (e.g. bass models) is a non-breaking change.
    preferred_family
        Optional ``MonitorChainFamily`` from the user's persisted
        Discovery answer (``DeviceCaps.preferred_chain_family``).
        When set, the fallback-chain selector short-circuits to this
        family on LOW / UNKNOWN tiers — the user's explicit pin
        always beats the tempo / key heuristic. The HIGH / MEDIUM
        paths still pick the retrieved preset; only the fallback id
        is affected.

    Returns
    -------
    ToneMatch
        Tier-aware match. Invariants:
          * ``tier == HIGH``     → ``chosen`` populated, ``fallback_chain_id`` None.
          * ``tier == MEDIUM``   → ``chosen`` populated, ``alternates`` may carry
            top-2 runners-up, ``fallback_chain_id`` None.
          * ``tier == LOW``      → ``chosen`` None, ``fallback_chain_id`` set.
          * ``tier == UNKNOWN``  → ``chosen`` None, ``fallback_chain_id`` set.
    """
    cleaned = _clean_candidates(candidates)
    fallback = policy.select_fallback_chain(
        understanding, preferred_family=preferred_family,
    )
    base_debug = _base_debug(role, cleaned)

    if not cleaned:
        # Genuine "no signal" — UNKNOWN, not LOW. Distinguishes a
        # retrieval miss from a low-confidence hit on the user's side.
        return ToneMatch(
            tier=ConfidenceTier.UNKNOWN,
            chosen=None,
            alternates=(),
            fallback_chain_id=fallback,
            rationale="No retrieval candidates available.",
            debug={**base_debug, "reason": "empty_candidates"},
        )

    top = cleaned[0]
    distances = [c["distance"] for c in cleaned]
    confidence = calibration.calibrate(distances[0])
    margin = calibration.compute_margin(distances)
    tier = tiers.classify(confidence, margin)

    debug = {
        **base_debug,
        "calibrated_confidence": confidence,
        "margin": margin,
        "raw_distances": tuple(distances),
    }

    if tier == ConfidenceTier.LOW:
        return ToneMatch(
            tier=ConfidenceTier.LOW,
            chosen=None,
            alternates=(),
            fallback_chain_id=fallback,
            rationale=(
                f"Confidence {confidence:.2f} too low to auto-suggest "
                f"(margin={_fmt_margin(margin)}); using curated chain."
            ),
            debug=debug,
        )

    top_candidate = _to_tone_candidate(top, confidence)
    alternates: List[ToneCandidate] = []
    for raw in cleaned[1 : 1 + MAX_ALTERNATES]:
        alt_conf = calibration.calibrate(raw["distance"])
        alternates.append(_to_tone_candidate(raw, alt_conf))

    rationale = _tier_rationale(tier, top, confidence, margin)
    return ToneMatch(
        tier=tier,
        chosen=top_candidate,
        alternates=tuple(alternates),
        fallback_chain_id=None,
        rationale=rationale,
        debug=debug,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> List[dict]:
    """Drop malformed candidate rows, coerce types, sort by distance.

    Defensive against legacy callers that may pass:
      * a dict instead of a list (single-candidate shape from
        ``unified_pipeline._match_presets_per_stem``),
      * candidates missing one of the required keys,
      * distances stored as strings.
    """
    if candidates is None:
        return []
    if isinstance(candidates, Mapping):
        # Single-candidate dict shape (legacy ``preset_matches[role]``).
        candidates = [candidates]
    cleaned: List[dict] = []
    for raw in candidates:
        if not isinstance(raw, Mapping):
            continue
        try:
            distance = float(raw["distance"])
        except (KeyError, TypeError, ValueError):
            continue
        if distance != distance or distance < 0.0:  # NaN or negative
            continue
        preset_id = str(raw.get("preset_id") or "")
        preset_name = str(raw.get("preset_name") or "")
        if not preset_id or not preset_name:
            continue
        cleaned.append({
            "preset_id": preset_id,
            "preset_name": preset_name,
            "instrument": str(raw.get("instrument") or "Unknown"),
            "distance": distance,
            "audio_preview_url": _opt_str(raw.get("audio_preview_url")),
            "parameters": dict(raw.get("parameters") or {}),
        })
    cleaned.sort(key=lambda c: c["distance"])
    return cleaned


def _to_tone_candidate(raw: Mapping[str, Any], confidence: float) -> ToneCandidate:
    return ToneCandidate(
        preset_id=raw["preset_id"],
        preset_name=raw["preset_name"],
        instrument=raw["instrument"],
        distance=raw["distance"],
        calibrated_confidence=confidence,
        audio_preview_url=raw.get("audio_preview_url"),
        parameters=dict(raw.get("parameters") or {}),
    )


def _base_debug(
    role: Optional[UserRole], candidates: Sequence[Mapping[str, Any]]
) -> dict:
    return {
        "role": role.value if role is not None else None,
        "n_candidates": len(candidates),
    }


def _tier_rationale(
    tier: ConfidenceTier,
    top: Mapping[str, Any],
    confidence: float,
    margin: Optional[float],
) -> str:
    margin_text = _fmt_margin(margin)
    if tier == ConfidenceTier.HIGH:
        return (
            f"Confident match: {top['preset_name']} "
            f"(confidence={confidence:.2f}, margin={margin_text})."
        )
    if tier == ConfidenceTier.MEDIUM:
        return (
            f"Suggested match: {top['preset_name']} "
            f"(confidence={confidence:.2f}, margin={margin_text})."
        )
    # LOW/UNKNOWN handled at call sites.
    return f"{tier.value} tier match: {top['preset_name']}."


def _fmt_margin(margin: Optional[float]) -> str:
    if margin is None:
        return "n/a"
    return f"{margin:.2f}"


def _opt_str(value: Any) -> Optional[str]:
    if isinstance(value, str) and value:
        return value
    return None


__all__ = [
    "MAX_ALTERNATES",
    "retrieve",
]
