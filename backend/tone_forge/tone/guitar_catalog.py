"""Guitar-tone catalog matcher.

Maps a guitar stem onto the curated ``MonitorChain`` bank by extracting
an 8-feature DSP fingerprint, z-normalizing against the catalog
distribution, and selecting the nearest chain. Returns a wire-shape
``ToneRecommendation`` ready for the Jam UI.

Boundary notes
--------------
* Runs entirely against the *bundled* fingerprint JSONs alongside the
  chain YAMLs. The placeholder fingerprints shipped with v0 are
  hand-authored estimates labeled as such in the file's ``source``
  field; they are replaced by ``scripts/render_chain_references.py``
  once Connect renders reference audio.
* Does **not** modify ``tone.calibration`` — that module's distance
  scale is tuned for the synth preset path (raw L2 over min-max
  normalized features). Z-normalized L2 lives in a different scale, so
  this module inlines its own ``exp(-d / TAU)`` calibrator with a tau
  retuned so the directional Alcest example lands in MEDIUM/HIGH
  rather than collapsing to near-zero confidence.
* Tier classification reuses ``tone.tiers.classify`` verbatim — the
  policy thresholds (0.80/0.20 HIGH, 0.55/0.10 MEDIUM) are the same
  policy boundary regardless of which catalog produced the distances.
* Fallback selection reuses ``tone.policy.select_fallback_chain`` so
  LOW/UNKNOWN tiers route through the same tempo+key heuristic the
  rest of the system uses.

Calibration tau
---------------
Tau = 14.0. Rationale: directional Alcest example computed in the
pre-implementation verification step yielded z-normalized distance
≈ 5.96 for the winning chain. ``exp(-5.96 / 14.0) ≈ 0.65`` places that
match in the MEDIUM-via-confidence band, which is the intended
behavior pre-calibration: surface the match to the user but do not
auto-apply. Once ``scripts/render_chain_references.py`` produces
measured fingerprints, this tau is the single number to revisit.

Public API
----------
``recommend(guitar_stem_path, *, understanding=None) -> ToneRecommendation``
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from tone_forge.contracts import (
    ConfidenceTier,
    MonitorChainFamily,
    SongUnderstanding,
    ToneRecAlternate,
    ToneRecApply,
    ToneRecFallback,
    ToneRecMatch,
    ToneRecommendation,
)
from tone_forge.tone import policy, tiers


def recommend_from_tempo_key(
    guitar_stem_path: Optional[Path],
    *,
    tempo_bpm: Optional[float],
    key: Optional[str],
) -> ToneRecommendation:
    """Convenience wrapper for callers that only have tempo+key.

    Constructs a minimal ``SongUnderstanding`` with empty section/chord
    tuples — sufficient for ``policy.select_fallback_chain``, which is
    the only consumer of ``understanding`` on the recommend path.
    """
    if tempo_bpm is None and key is None:
        understanding: Optional[SongUnderstanding] = None
    else:
        understanding = SongUnderstanding(
            tempo_bpm=float(tempo_bpm) if tempo_bpm else 0.0,
            tempo_confidence=0.0,
            time_signature=(4, 4),
            beats_s=(),
            downbeats_s=(),
            sections=(),
            chords=(),
            key=key,
        )
    return recommend(guitar_stem_path, understanding=understanding)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Distance scale for the z-normalized calibrator. See module docstring.
DISTANCE_TAU: float = 14.0

# Query window length. HPSS dominates fingerprint cost (≈315 ms on 8 s,
# ≈60 s on full stem in measurement). 8 s carries enough envelope and
# harmonic structure for the 8-feature descriptor.
QUERY_WINDOW_SECONDS: float = 8.0

# Sample rate matches catalog_builder.extract_preset_fingerprint so the
# feature math is identical for catalog reference renders.
QUERY_SAMPLE_RATE: int = 22050

# Floor std-dev per feature so a degenerate catalog (all chains
# identical on one axis) does not divide by zero. Set well below the
# smallest meaningful variation we expect across the bank.
_STD_FLOOR: float = 1e-3

# Eight-feature schema, in the canonical order used everywhere.
_FEATURE_KEYS: Tuple[str, ...] = (
    "brightness",
    "warmth",
    "air",
    "attack_ms",
    "decay_ms",
    "sustain_ratio",
    "harmonic_ratio",
    "pitch_stability",
)

# Feature-validity mask: per-axis booleans indicating whether the
# extractor's output for that axis is trustworthy on this particular
# audio. False means "compute it for parity with the catalog row, but
# exclude this axis from the z-norm L2 distance." The mask is carried
# alongside the vector through the catalog and query paths, then
# AND-combined at distance time so an axis is only used if BOTH
# endpoints reported it as valid.
#
# Backwards compatibility: catalog fingerprint JSONs predating this
# field are treated as all-valid (the prior implicit contract).
_FEATURE_VALIDITY_KEY: str = "feature_validity"

# Polyphony onset-density thresholds for the dedicated reliability
# function. Tuned against the QUERY_WINDOW_SECONDS (8 s) window:
# > 2.0 onsets/s OR > 16 absolute onsets in the window triggers the
# polyphonic gate. These knobs live here, not at call sites, so the
# heuristic can evolve independently of the feature math.
_POLYPHONY_ONSETS_PER_SECOND: float = 2.0
_POLYPHONY_ABSOLUTE_ONSETS: int = 16

# Features the polyphony gate invalidates. Empirically these are the
# axes that saturate / collapse on multi-note content; see
# PHASE2_FEATURE_MASK_REPORT.md for the validation experiment. The
# remaining four axes (brightness, warmth, air, harmonic_ratio) carry
# the distance under polyphonic queries.
_POLYPHONY_INVALIDATES: Tuple[str, ...] = (
    "attack_ms",
    "decay_ms",
    "sustain_ratio",
    "pitch_stability",
)

# Number of alternates surfaced in MEDIUM rationale (matches
# tone.MAX_ALTERNATES — kept independent here so the synth path's
# constant does not become a hidden dependency).
MAX_ALTERNATES: int = 2

_CHAINS_ROOT: Path = Path(__file__).resolve().parent.parent / "monitor" / "chains"


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CatalogEntry:
    chain_id: str
    display_name: str
    family: MonitorChainFamily
    vector: np.ndarray  # raw 8-feature vector in canonical order
    # Per-axis boolean validity flags in the same order as ``vector``.
    # All-True for legacy fingerprints that predate the field — see
    # ``_load_entry``.
    validity: np.ndarray


@dataclass(frozen=True)
class _Catalog:
    entries: Tuple[_CatalogEntry, ...]
    feature_mean: np.ndarray  # shape (8,)
    feature_std: np.ndarray   # shape (8,), floored at _STD_FLOOR


_CATALOG_CACHE: Optional[_Catalog] = None


def _get_catalog() -> _Catalog:
    """Lazy-load the chain fingerprint catalog and compute the z-norm scale.

    Cached after first call. Tests that mutate fingerprint files should
    call ``_reset_catalog_cache()``.
    """
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE

    entries: List[_CatalogEntry] = []
    # Enumerate fingerprints directly from disk rather than asking
    # ``monitor/`` for chain ids. Each fingerprint JSON carries its own
    # ``chain_id``, ``display_name`` and ``family`` — enough to populate
    # a catalog entry without crossing the tone→monitor boundary.
    if _CHAINS_ROOT.is_dir():
        for fingerprint_path in sorted(_CHAINS_ROOT.glob("*.fingerprint.json")):
            try:
                entry = _load_entry(fingerprint_path)
            except Exception as exc:
                logger.warning(
                    "guitar_catalog: failed to load fingerprint %s: %s",
                    fingerprint_path, exc,
                )
                continue
            entries.append(entry)

    if not entries:
        # Catalog is empty. The recommend() path falls through to a
        # UNKNOWN-tier fallback so the UI still gets a usable payload.
        _CATALOG_CACHE = _Catalog(
            entries=(),
            feature_mean=np.zeros(len(_FEATURE_KEYS), dtype=np.float64),
            feature_std=np.ones(len(_FEATURE_KEYS), dtype=np.float64),
        )
        return _CATALOG_CACHE

    vectors = np.stack([e.vector for e in entries], axis=0)
    mean = vectors.mean(axis=0)
    std = vectors.std(axis=0)
    std = np.where(std < _STD_FLOOR, _STD_FLOOR, std)

    _CATALOG_CACHE = _Catalog(
        entries=tuple(entries),
        feature_mean=mean.astype(np.float64),
        feature_std=std.astype(np.float64),
    )
    return _CATALOG_CACHE


def _reset_catalog_cache() -> None:
    """Test helper. Drops the in-memory catalog so the next call rereads disk."""
    global _CATALOG_CACHE
    _CATALOG_CACHE = None


def _load_entry(fingerprint_path: Path) -> _CatalogEntry:
    """Parse one ``<chain_id>.fingerprint.json`` into a ``_CatalogEntry``.

    The JSON is the single source of truth for the catalog: it carries
    ``chain_id``, ``display_name``, ``family``, the feature vector, and
    the optional ``feature_validity`` mask. We do not also parse the
    sibling YAML — that would re-introduce the tone→monitor import that
    the boundary discipline forbids.

    Backwards compatibility: a fingerprint JSON without the optional
    ``feature_validity`` block is treated as all-valid. This is the
    contract that lets the new distance code run against legacy on-disk
    fingerprints before they are re-rendered.
    """
    raw = json.loads(fingerprint_path.read_text(encoding="utf-8"))

    chain_id = raw.get("chain_id")
    if not isinstance(chain_id, str) or not chain_id:
        raise ValueError(f"missing chain_id in {fingerprint_path}")

    display_name = raw.get("display_name")
    if not isinstance(display_name, str) or not display_name:
        raise ValueError(f"missing display_name in {fingerprint_path}")

    family_raw = raw.get("family")
    if not isinstance(family_raw, str) or not family_raw:
        raise ValueError(f"missing family in {fingerprint_path}")
    try:
        family = MonitorChainFamily(family_raw)
    except ValueError as exc:
        raise ValueError(
            f"family {family_raw!r} not a valid MonitorChainFamily in {fingerprint_path}"
        ) from exc

    features = raw.get("features") or {}
    vector = np.array(
        [_coerce_feature(features.get(key), key=key) for key in _FEATURE_KEYS],
        dtype=np.float64,
    )

    validity_block = raw.get(_FEATURE_VALIDITY_KEY)
    validity = _coerce_validity_block(validity_block, where=str(fingerprint_path))

    return _CatalogEntry(
        chain_id=chain_id,
        display_name=display_name,
        family=family,
        vector=vector,
        validity=validity,
    )


def _coerce_validity_block(block: object, *, where: str) -> np.ndarray:
    """Return an 8-vector of booleans from an optional JSON dict.

    ``None`` (field missing) → all-True (legacy fingerprint). A dict
    missing some keys defaults those keys to True so a partial mask is
    treated as "validity not asserted for that axis." Any non-bool
    coerces via ``bool()``.
    """
    if block is None:
        return np.ones(len(_FEATURE_KEYS), dtype=bool)
    if not isinstance(block, dict):
        logger.warning(
            "guitar_catalog: %s has non-dict %s=%r; treating as all-valid",
            where, _FEATURE_VALIDITY_KEY, block,
        )
        return np.ones(len(_FEATURE_KEYS), dtype=bool)
    return np.array(
        [bool(block.get(key, True)) for key in _FEATURE_KEYS],
        dtype=bool,
    )


def _coerce_feature(value: object, *, key: str) -> float:
    if value is None:
        raise ValueError(f"fingerprint missing feature {key!r}")
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"feature {key!r} not numeric: {value!r}") from exc
    if math.isnan(f) or math.isinf(f):
        raise ValueError(f"feature {key!r} is NaN/inf")
    return f


# ---------------------------------------------------------------------------
# Query fingerprint
# ---------------------------------------------------------------------------


def _extract_query_fingerprint(
    audio_path: Path,
) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """Compute the 8-feature query vector + validity mask from a guitar stem.

    Mirrors ``catalog_builder.extract_preset_fingerprint`` math but:
      * loads at most ``QUERY_WINDOW_SECONDS`` from the middle of the
        file (HPSS cost scales with duration), and
      * returns the raw 8-vector + validity — no provenance, no SHA1.

    Returns ``None`` on any load/extraction failure. Callers treat
    ``None`` as the UNKNOWN-tier path.
    """
    try:
        import librosa
    except Exception as exc:
        logger.warning("guitar_catalog: librosa unavailable: %s", exc)
        return None

    try:
        # Probe duration cheaply so we can center the window.
        full_duration = librosa.get_duration(path=str(audio_path))
        if full_duration <= 0:
            return None
        if full_duration <= QUERY_WINDOW_SECONDS:
            offset = 0.0
            window = full_duration
        else:
            offset = max(0.0, (full_duration - QUERY_WINDOW_SECONDS) / 2.0)
            window = QUERY_WINDOW_SECONDS

        y, sr = librosa.load(
            str(audio_path),
            sr=QUERY_SAMPLE_RATE,
            mono=True,
            offset=offset,
            duration=window,
        )
    except Exception as exc:
        logger.warning(
            "guitar_catalog: failed to load %s: %s", audio_path, exc
        )
        return None

    if y.size == 0:
        return None

    try:
        return _compute_8_features(y, sr)
    except Exception as exc:
        logger.warning(
            "guitar_catalog: feature extraction failed on %s: %s",
            audio_path, exc,
        )
        return None


def _compute_polyphony_reliability(y: np.ndarray, sr: int) -> Dict[str, bool]:
    """Dedicated reliability check for the polyphony gate.

    Returns a per-axis ``{feature_key: is_valid}`` mapping. Currently
    only ``_POLYPHONY_INVALIDATES`` axes are affected; the rest are
    always reported True so the caller can simply AND this dict into
    any future axis-level reliability output.

    Isolated behind this function so the heuristic (onset density,
    crest factor, harmonic complexity, etc.) can evolve independently
    of the feature math. Today's implementation: librosa onset
    detection over the analysis window; trip the gate if onset density
    > ``_POLYPHONY_ONSETS_PER_SECOND`` or absolute onsets >
    ``_POLYPHONY_ABSOLUTE_ONSETS``.

    Fails-open: on any extractor exception, all axes are reported
    valid — we don't want a librosa hiccup to silently mask out half
    the catalog.
    """
    base = {key: True for key in _FEATURE_KEYS}
    try:
        import librosa

        onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time")
        duration = float(y.size) / float(sr) if sr > 0 else 0.0
        n_onsets = int(len(onsets))
        density = (n_onsets / duration) if duration > 0.0 else 0.0
        polyphonic = (
            density > _POLYPHONY_ONSETS_PER_SECOND
            or n_onsets > _POLYPHONY_ABSOLUTE_ONSETS
        )
    except Exception as exc:
        logger.warning(
            "guitar_catalog: polyphony reliability probe failed (%s); "
            "treating all axes as valid.",
            exc,
        )
        return base

    if polyphonic:
        for key in _POLYPHONY_INVALIDATES:
            base[key] = False
    return base


def _compute_8_features(
    y: np.ndarray, sr: int
) -> Tuple[np.ndarray, np.ndarray]:
    """Pure-DSP feature math, matching catalog_builder for parity.

    Returns ``(vector, validity)``: the 8-feature vector in canonical
    order plus a boolean mask of which axes are trusted on this audio.
    The vector is always fully populated (computed for parity with the
    catalog); the mask is what gates which axes participate in the
    z-norm L2 distance.
    """
    import librosa

    out = {k: 0.0 for k in _FEATURE_KEYS}

    # 1. Brightness — spectral centroid normalised to Nyquist.
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    out["brightness"] = float(np.mean(centroid) / (sr / 2))

    # 2-3. Warmth + air — band energy ratios from STFT magnitude.
    D = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)
    total_energy = float(np.sum(D ** 2))
    if total_energy > 0.0:
        low_mid_mask = (freqs >= 200) & (freqs <= 800)
        high_mask = freqs >= 8000
        out["warmth"] = float(np.sum(D[low_mid_mask, :] ** 2) / (total_energy + 1e-10))
        out["air"] = float(np.sum(D[high_mask, :] ** 2) / (total_energy + 1e-10))

    # 4-6. ADSR-like envelope features.
    envelope = np.abs(librosa.effects.preemphasis(y))
    envelope = np.convolve(envelope, np.ones(512) / 512, mode="same")
    if envelope.size > 0:
        peak_idx = int(np.argmax(envelope))
        peak_val = float(envelope[peak_idx])

        if peak_idx > 0 and peak_val > 0.0:
            threshold = peak_val * 0.1
            attack_start = 0
            for i in range(peak_idx):
                if envelope[i] > threshold:
                    attack_start = i
                    break
            out["attack_ms"] = float((peak_idx - attack_start) / sr * 1000.0)

        if peak_idx < envelope.size - 1 and peak_val > 0.0:
            decay_portion = envelope[peak_idx:]
            target = peak_val * 0.37
            decay_idx = int(np.argmax(decay_portion < target))
            if decay_idx > 0:
                out["decay_ms"] = float(decay_idx / sr * 1000.0)
            sustain_portion = decay_portion[len(decay_portion) // 2:]
            if sustain_portion.size > 0:
                out["sustain_ratio"] = float(np.mean(sustain_portion) / peak_val)

    # 7. Harmonic ratio — HPSS dominates query cost (~315 ms on 8 s).
    y_harmonic, _ = librosa.effects.hpss(y)
    harmonic_energy = float(np.sum(y_harmonic ** 2))
    if total_energy > 0.0:
        out["harmonic_ratio"] = float(harmonic_energy / (total_energy + 1e-10))

    # 8. Pitch stability via pyin. The previous ``max(0, 1 - std/100)``
    # clamp saturates to zero whenever cents-std exceeds 100, which
    # happens routinely on polyphonic content and on percussive/dry
    # signals — collapsing this axis to a constant and poisoning the
    # z-norm distance. Replace with an exponential decay so the metric
    # is smooth and monotonic across the full range while keeping the
    # output in [0, 1].
    try:
        f0, _, _ = librosa.pyin(y, fmin=50, fmax=2000, sr=sr, hop_length=512)
        f0 = np.nan_to_num(f0, nan=0.0)
        voiced_f0 = f0[f0 > 0]
        if voiced_f0.size > 10:
            mean_f0 = float(np.mean(voiced_f0))
            if mean_f0 > 0.0:
                std_cents = float(
                    np.std(1200.0 * np.log2(voiced_f0 / mean_f0 + 1e-10))
                )
                out["pitch_stability"] = float(math.exp(-std_cents / 100.0))
            else:
                out["pitch_stability"] = 0.5
        else:
            out["pitch_stability"] = 0.5
    except Exception:
        out["pitch_stability"] = 0.5

    vector = np.array([out[k] for k in _FEATURE_KEYS], dtype=np.float64)

    # Validity is purely a function of the audio, computed alongside
    # the features so a single ``_compute_8_features`` call produces
    # everything ``_extract_query_fingerprint`` and
    # ``render_chain_references`` need.
    reliability = _compute_polyphony_reliability(y, sr)
    validity = np.array(
        [bool(reliability.get(k, True)) for k in _FEATURE_KEYS],
        dtype=bool,
    )
    return vector, validity


# ---------------------------------------------------------------------------
# Calibration + distance
# ---------------------------------------------------------------------------


def _znorm_l2(
    query: np.ndarray,
    catalog_vec: np.ndarray,
    std: np.ndarray,
    *,
    query_validity: Optional[np.ndarray] = None,
    catalog_validity: Optional[np.ndarray] = None,
) -> float:
    """L2 distance after dividing each axis by the catalog std-dev.

    Validity-aware: an axis only participates in the distance if BOTH
    the query and the catalog row reported it as valid. The selection
    is *explicit*: we index the surviving axes out of each vector and
    compute the L2 over the reduced vectors. This is intentional —
    multiplying a 0/1 mask into ``delta`` would still allow NaN/Inf
    on invalid axes to poison the distance via 0 * inf = NaN, and it
    would hide which axes participated from the debug payload.

    With no validity provided, defaults to all-True on both sides
    (legacy behavior unchanged). Returns a non-negative finite float;
    raises on NaN. If no axes survive the AND, returns 0.0 (every
    axis equally invalid → no information).
    """
    n = catalog_vec.shape[0]
    if query_validity is None:
        query_validity = np.ones(n, dtype=bool)
    if catalog_validity is None:
        catalog_validity = np.ones(n, dtype=bool)

    valid = np.asarray(query_validity, dtype=bool) & np.asarray(
        catalog_validity, dtype=bool
    )
    if not valid.any():
        # No axes survive: distance is undefined by reduction. Returning
        # 0.0 would put every chain at distance 0; that's worse than
        # the legacy behavior of an all-axes z-norm. Push the entire
        # rank toward the UNKNOWN fallback by reporting a sentinel +Inf
        # — caller treats inf the same as a failed distance.
        return float("inf")

    # Explicit valid-axis selection: index, then compute.
    q = query[valid]
    c = catalog_vec[valid]
    s = std[valid]
    delta = (q - c) / s
    d = float(np.linalg.norm(delta))
    if math.isnan(d) or math.isinf(d):
        raise ValueError("z-normalized distance is non-finite")
    return d


def _calibrate(distance: float) -> float:
    """``exp(-distance / DISTANCE_TAU)`` clamped to ``[0, 1]``.

    Intentionally *not* capped below HIGH_CONFIDENCE_MIN. The calibration
    placeholder in ``tone.calibration`` caps to defend the synth path
    against premature HIGH; here, we already require the margin check
    in ``tiers.classify``, and the directional verification showed the
    Alcest example produces a 0.47 margin (>0.20) — so we want to allow
    a HIGH tier when both signals agree. The pre-render fingerprints
    being hand-authored estimates is documented in the source_note of
    every fingerprint JSON, which is the audit trail for that
    permissiveness.
    """
    if not math.isfinite(distance) or distance < 0.0:
        return 0.0
    raw = math.exp(-distance / DISTANCE_TAU)
    if raw < 0.0:
        return 0.0
    if raw > 1.0:
        return 1.0
    return raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def recommend(
    guitar_stem_path: Optional[Path],
    *,
    understanding: Optional[SongUnderstanding] = None,
) -> ToneRecommendation:
    """Produce a UI-ready ToneRecommendation for a guitar stem.

    Parameters
    ----------
    guitar_stem_path
        Path to the isolated guitar stem (typically the ``other``
        channel from the stem separator, optionally band-limited). May
        be ``None`` or non-existent — the UNKNOWN fallback path
        handles that.
    understanding
        Optional song understanding. Used only when we fall through to
        the curated chain (LOW/UNKNOWN) so the tempo+key heuristic in
        ``tone.policy`` can pick the right family.

    Returns
    -------
    ToneRecommendation
        Always populated. XOR invariant on ``match``/``fallback`` is
        enforced by the contract's ``__post_init__``.
    """
    catalog = _get_catalog()
    fallback_chain_id = policy.select_fallback_chain(understanding)

    if not catalog.entries:
        return _empty_catalog_fallback(fallback_chain_id, understanding)

    query_pair = (
        _extract_query_fingerprint(guitar_stem_path)
        if guitar_stem_path is not None and Path(guitar_stem_path).is_file()
        else None
    )
    if query_pair is None:
        return _unknown_fallback(
            fallback_chain_id,
            understanding,
            reason="query_extraction_failed",
            rationale=(
                "Could not extract a guitar fingerprint from the stem; "
                "using the curated fallback chain."
            ),
        )
    query, query_validity = query_pair

    # Score every chain in the bank — bank is tiny (5 chains at v0),
    # no need for an index.
    ranked: List[Tuple[_CatalogEntry, float]] = []
    for entry in catalog.entries:
        try:
            d = _znorm_l2(
                query,
                entry.vector,
                catalog.feature_std,
                query_validity=query_validity,
                catalog_validity=entry.validity,
            )
        except Exception as exc:
            logger.warning(
                "guitar_catalog: distance failed on %s: %s",
                entry.chain_id, exc,
            )
            continue
        if not math.isfinite(d):
            # Sentinel +Inf from _znorm_l2 — no axes survived the AND.
            # Treat same as distance failure for this chain.
            continue
        ranked.append((entry, d))

    if not ranked:
        return _unknown_fallback(
            fallback_chain_id,
            understanding,
            reason="all_distances_failed",
            rationale=(
                "Catalog scoring failed on every chain; "
                "using the curated fallback chain."
            ),
        )

    ranked.sort(key=lambda pair: pair[1])
    top_entry, top_distance = ranked[0]
    distances = [d for _, d in ranked]

    confidence = _calibrate(top_distance)
    margin = _compute_margin(distances)
    tier = tiers.classify(confidence, margin)

    debug = {
        "tau": DISTANCE_TAU,
        "raw_distances": tuple(round(d, 4) for d in distances),
        "calibrated_confidence": round(confidence, 4),
        "margin": None if margin is None else round(margin, 4),
        "query_vector": tuple(round(float(x), 6) for x in query),
        "query_validity": tuple(bool(v) for v in query_validity),
        "feature_keys": _FEATURE_KEYS,
        "feature_mean": tuple(round(float(x), 6) for x in catalog.feature_mean),
        "feature_std": tuple(round(float(x), 6) for x in catalog.feature_std),
        "ranking": tuple(
            {
                "chain_id": entry.chain_id,
                "distance": round(d, 4),
                "catalog_validity": tuple(bool(v) for v in entry.validity),
            }
            for entry, d in ranked
        ),
    }

    if tier in (ConfidenceTier.LOW, ConfidenceTier.UNKNOWN):
        # Top of the bank scored, but neither confidence nor margin
        # cleared MEDIUM. Hand off to the curated fallback while
        # preserving the ranking for telemetry.
        return _low_confidence_fallback(
            fallback_chain_id, understanding, confidence, margin, debug
        )

    match = ToneRecMatch(
        chain_id=top_entry.chain_id,
        display_name=top_entry.display_name,
        archetype=top_entry.family.value,
        distance=round(top_distance, 4),
        confidence=round(confidence, 4),
    )

    alternates: List[ToneRecAlternate] = []
    for entry, d in ranked[1 : 1 + MAX_ALTERNATES]:
        alternates.append(
            ToneRecAlternate(
                chain_id=entry.chain_id,
                display_name=entry.display_name,
                archetype=entry.family.value,
                distance=round(d, 4),
            )
        )

    rationale = _match_rationale(tier, top_entry.display_name, confidence, margin)

    return ToneRecommendation(
        tier=tier,
        rationale=rationale,
        apply=ToneRecApply(chain_id=top_entry.chain_id),
        match=match,
        fallback=None,
        alternates=tuple(alternates),
        preview_url=None,
        debug=debug,
    )


# ---------------------------------------------------------------------------
# Wire helpers
# ---------------------------------------------------------------------------


def to_wire_dict(rec: ToneRecommendation) -> Dict[str, object]:
    """Project a ToneRecommendation onto the JSON shape consumed by jam.js.

    Centralising the projection here keeps the API edge thin and lets
    the boundary test guard the wire schema independently of the
    contract dataclass.
    """
    payload: Dict[str, object] = {
        "tier": rec.tier.value,
        "rationale": rec.rationale,
        "apply": {
            "chain_id": rec.apply.chain_id,
            "action": rec.apply.action,
        },
        "alternates": [
            {
                "chain_id": alt.chain_id,
                "display_name": alt.display_name,
                "archetype": alt.archetype,
                "distance": alt.distance,
            }
            for alt in rec.alternates
        ],
        "preview_url": rec.preview_url,
        "debug": dict(rec.debug),
    }
    if rec.match is not None:
        payload["match"] = {
            "chain_id": rec.match.chain_id,
            "display_name": rec.match.display_name,
            "archetype": rec.match.archetype,
            "distance": rec.match.distance,
            "confidence": rec.match.confidence,
        }
        payload["fallback"] = None
    else:
        assert rec.fallback is not None  # XOR invariant
        payload["match"] = None
        payload["fallback"] = {
            "chain_id": rec.fallback.chain_id,
            "display_name": rec.fallback.display_name,
            "archetype": rec.fallback.archetype,
            "reason": rec.fallback.reason,
        }
    return payload


# ---------------------------------------------------------------------------
# Internals: fallbacks
# ---------------------------------------------------------------------------


def _resolve_fallback_meta(chain_id: str) -> Tuple[str, str]:
    """Return ``(display_name, archetype)`` for a chain id.

    Reads the fingerprint JSON directly to avoid the tone→monitor
    import boundary. Robust to the JSON missing or malformed — degrades
    to the id so the fallback path never raises.
    """
    fingerprint_path = _CHAINS_ROOT / f"{chain_id}.fingerprint.json"
    try:
        raw = json.loads(fingerprint_path.read_text(encoding="utf-8"))
        display_name = raw.get("display_name")
        family = raw.get("family")
        if not isinstance(display_name, str) or not display_name:
            display_name = chain_id
        if not isinstance(family, str):
            family = ""
        return display_name, family
    except Exception as exc:
        logger.warning(
            "guitar_catalog: failed to resolve fallback chain %s: %s",
            chain_id, exc,
        )
        return chain_id, ""


def _empty_catalog_fallback(
    fallback_chain_id: str,
    understanding: Optional[SongUnderstanding],
) -> ToneRecommendation:
    display_name, archetype = _resolve_fallback_meta(fallback_chain_id)
    return ToneRecommendation(
        tier=ConfidenceTier.UNKNOWN,
        rationale=(
            "Guitar tone catalog is empty (no chain fingerprints on disk); "
            f"using curated fallback {display_name!r}."
        ),
        apply=ToneRecApply(chain_id=fallback_chain_id),
        match=None,
        fallback=ToneRecFallback(
            chain_id=fallback_chain_id,
            display_name=display_name,
            archetype=archetype,
            reason="empty_catalog",
        ),
        alternates=(),
        preview_url=None,
        debug={"tau": DISTANCE_TAU, "catalog_size": 0},
    )


def _unknown_fallback(
    fallback_chain_id: str,
    understanding: Optional[SongUnderstanding],
    *,
    reason: str,
    rationale: str,
) -> ToneRecommendation:
    display_name, archetype = _resolve_fallback_meta(fallback_chain_id)
    return ToneRecommendation(
        tier=ConfidenceTier.UNKNOWN,
        rationale=rationale,
        apply=ToneRecApply(chain_id=fallback_chain_id),
        match=None,
        fallback=ToneRecFallback(
            chain_id=fallback_chain_id,
            display_name=display_name,
            archetype=archetype,
            reason=reason,
        ),
        alternates=(),
        preview_url=None,
        debug={"tau": DISTANCE_TAU, "reason": reason},
    )


def _low_confidence_fallback(
    fallback_chain_id: str,
    understanding: Optional[SongUnderstanding],
    confidence: float,
    margin: Optional[float],
    debug: Dict[str, object],
) -> ToneRecommendation:
    display_name, archetype = _resolve_fallback_meta(fallback_chain_id)
    margin_str = "n/a" if margin is None else f"{margin:.2f}"
    return ToneRecommendation(
        tier=ConfidenceTier.LOW,
        rationale=(
            f"Top catalog match confidence {confidence:.2f} "
            f"(margin={margin_str}) too low to suggest; "
            f"using curated fallback {display_name!r}."
        ),
        apply=ToneRecApply(chain_id=fallback_chain_id),
        match=None,
        fallback=ToneRecFallback(
            chain_id=fallback_chain_id,
            display_name=display_name,
            archetype=archetype,
            reason="low_confidence",
        ),
        alternates=(),
        preview_url=None,
        debug=debug,
    )


def _compute_margin(distances: List[float]) -> Optional[float]:
    """``(d_second - d_top) / d_top`` from an ascending distance list.

    Returns ``None`` for the same reasons as
    ``tone.calibration.compute_margin``: fewer than two finite
    distances, or non-positive top distance.
    """
    cleaned = [
        d for d in distances
        if isinstance(d, (int, float)) and math.isfinite(d) and d >= 0.0
    ]
    if len(cleaned) < 2:
        return None
    cleaned.sort()
    d_top, d_second = cleaned[0], cleaned[1]
    if d_top <= 0.0:
        return None
    return (d_second - d_top) / d_top


def _match_rationale(
    tier: ConfidenceTier,
    display_name: str,
    confidence: float,
    margin: Optional[float],
) -> str:
    margin_str = "n/a" if margin is None else f"{margin:.2f}"
    if tier == ConfidenceTier.HIGH:
        return (
            f"Confident match: {display_name} "
            f"(confidence={confidence:.2f}, margin={margin_str})."
        )
    if tier == ConfidenceTier.MEDIUM:
        return (
            f"Suggested match: {display_name} "
            f"(confidence={confidence:.2f}, margin={margin_str})."
        )
    return f"{tier.value} tier match: {display_name}."


__all__ = [
    "DISTANCE_TAU",
    "MAX_ALTERNATES",
    "QUERY_SAMPLE_RATE",
    "QUERY_WINDOW_SECONDS",
    "recommend",
    "recommend_from_tempo_key",
    "to_wire_dict",
]
