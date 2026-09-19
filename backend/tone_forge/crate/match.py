"""Session-aware crate match ranking — a weighted, extensible model.

This EXTENDS borrow's donor score (``(harm - 0.3*folded) * 2**(-semis/6)``,
borrow.py) into a confidence-weighted convex combination over every analysis
signal the pipeline surfaces. It reuses borrow's gates and primitives
verbatim (``_fold_ratio``/``_STRETCH_LIMIT`` tempo gate, ``harmonic_compat``
terms, ``_transpose_steps`` transpose penalty, ``_stem_aliases``/``_PITCHLESS``)
so crate ranking and the borrow render agree.

The formula is deliberately one-line-editable: ``_MATCH_WEIGHTS`` names every
signal (implemented ones with a non-zero weight; deferred seams —
groove/timbre/vocal/loudness/chord_prog — pre-declared at weight 0). Adding
or removing a signal is a single weight edit; normalizing by ``Σ(w·c)`` keeps
the score in ``[0, 1]`` no matter which signals a given pair actually has, so a
track missing melody/energy is ranked fairly on the rest rather than penalized
to zero.

Signal inputs and their sources are documented at each sub-score. The three
"available now but not yet weighted" pieces the brief calls out —
groove/timbre/vocal/loudness — read their stored fields (has_vocals,
spectral_centroid, loudness_lufs) but sit at weight 0 until calibrated; the
chord-PROGRESSION term waits on the chord-accuracy fix and is likewise 0.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from tone_forge.contracts import CrateTrack
from tone_forge.performance import borrow

# ---------------------------------------------------------------------------
# Weights — the extension point. Implemented signals carry a weight; the
# deferred seams are declared at 0 so turning one on is a one-line edit with
# no API/shape change (the normalization already tolerates its absence).
# ---------------------------------------------------------------------------
_MATCH_WEIGHTS: Dict[str, float] = {
    "tempo": 0.25,
    "harmony": 0.28,
    "melody": 0.15,
    "energy": 0.10,
    "genre": 0.10,
    "instr": 0.12,
    # Deferred seams (inputs described in the brief's signal inventory):
    "groove": 0.0,      # performance/groove.py micro-timing signature
    "timbre": 0.0,      # spectral_centroid tonal-seat matching
    "vocal": 0.0,       # has_vocals instrumental/vocal filter-as-score
    "loudness": 0.0,    # loudness_lufs level matching
    "chord_prog": 0.0,  # chord-progression matching (waits on chord fix)
}

_CORE_STEMS = ("drums", "bass", "other", "vocals")
_MAJ_SCALE = frozenset({0, 2, 4, 5, 7, 9, 11})
_MIN_SCALE = frozenset({0, 2, 3, 5, 7, 8, 10})


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _key_of(result: Optional[Dict]) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    return result.get("detected_key") or result.get("key")


# ---------------------------------------------------------------------------
# Melody signal — from the melody-sequence lane.
# Source: analysis/melody_sequence.py:190 build_melody_sequence → MelodySequence
# (contracts.py MelodySequence). Ingestion materializes it onto the crate
# track's stored analysis as result["melody"] = {"notes": [{pitch,start,end,
# velocity}], "confidence": ...}; the session side reads the same key when its
# own analysis carries a melody (0-weight fallback when it doesn't).
# ---------------------------------------------------------------------------


def _melody_notes(result: Optional[Dict]) -> List[Dict]:
    if not isinstance(result, dict):
        return []
    mel = result.get("melody")
    if isinstance(mel, dict) and isinstance(mel.get("notes"), list):
        return [n for n in mel["notes"]
                if isinstance(n, dict) and isinstance(n.get("pitch"), (int, float))]
    return []


def _melody_conf(result: Optional[Dict]) -> float:
    if isinstance(result, dict):
        mel = result.get("melody")
        if isinstance(mel, dict):
            try:
                return _clamp01(float(mel.get("confidence") or 0.0))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _median_pitch(notes: List[Dict]) -> Optional[float]:
    ps = sorted(int(n["pitch"]) for n in notes)
    if not ps:
        return None
    m = len(ps) // 2
    return float(ps[m]) if len(ps) % 2 else (ps[m - 1] + ps[m]) / 2.0


def _scale_pcs(key_str: Optional[str]) -> Optional[frozenset]:
    parsed = borrow._parse_key(key_str)
    if parsed is None:
        return None
    tonic, is_minor = parsed
    base = _MIN_SCALE if is_minor else _MAJ_SCALE
    return frozenset((tonic + iv) % 12 for iv in base)


def _step_signs(notes: List[Dict]) -> List[int]:
    ordered = sorted(notes, key=lambda n: float(n.get("start", 0.0)))
    ps = [int(n["pitch"]) for n in ordered]
    return [(1 if ps[i + 1] > ps[i] else (-1 if ps[i + 1] < ps[i] else 0))
            for i in range(len(ps) - 1)]


def _resample(vec: List[int], n: int) -> List[float]:
    if not vec:
        return [0.0] * n
    return [float(vec[int(i * len(vec) / n)]) for i in range(n)]


def _contour_sim(a: List[Dict], b: List[Dict]) -> float:
    """[0,1] similarity of two coarse up/down contours (cosine of down-sampled
    step-direction vectors, remapped from [-1,1])."""
    sa, sb = _step_signs(a), _step_signs(b)
    if not sa or not sb:
        return 0.5
    n = min(len(sa), len(sb), 16)
    va, vb = _resample(sa, n), _resample(sb, n)
    dot = sum(x * y for x, y in zip(va, vb))
    na = math.sqrt(sum(x * x for x in va))
    nb = math.sqrt(sum(y * y for y in vb))
    if na == 0.0 or nb == 0.0:
        return 0.5
    return _clamp01((dot / (na * nb) + 1.0) / 2.0)


def _melody_fit(session_result: Optional[Dict], track: CrateTrack,
                track_result: Optional[Dict]) -> Tuple[Optional[float], float]:
    """(value, confidence). None when the session has no melody lane to
    compare against — the register/scale/contour terms are all relative to
    the session melody. Averages whichever of the three components are
    computable from the material present (register from features when the
    track blob's full notes aren't loaded)."""
    s_notes = _melody_notes(session_result)
    if not s_notes:
        return None, 0.0
    t_notes = _melody_notes(track_result)
    comps: List[float] = []

    # register / octave fit
    s_med = _median_pitch(s_notes)
    t_med = _median_pitch(t_notes) if t_notes else (
        float(track.features.melody_register)
        if track.features.melody_register is not None else None)
    if s_med is not None and t_med is not None:
        comps.append(_clamp01(1.0 - abs(s_med - t_med) / 24.0))

    # key/scale membership of the track melody in the SESSION key's scale
    scale = _scale_pcs(_key_of(session_result))
    if t_notes and scale is not None:
        inside = sum(1 for n in t_notes if (int(n["pitch"]) % 12) in scale)
        comps.append(inside / len(t_notes))

    # coarse contour agreement
    if len(s_notes) >= 2 and len(t_notes) >= 2:
        comps.append(_contour_sim(s_notes, t_notes))

    if not comps:
        return None, 0.0
    s_conf = _melody_conf(session_result)
    t_conf = track.features.melody_confidence or _melody_conf(track_result)
    known = [c for c in (s_conf, t_conf) if isinstance(c, (int, float)) and c > 0]
    conf = min(known) if known else 0.5
    return sum(comps) / len(comps), _clamp01(conf)


# ---------------------------------------------------------------------------
# Harmony — content cosine (pc-histograms) refined by the key-label
# relationship. Uses the track's PRECOMPUTED pc_histogram (features) when
# present so we don't re-fold its chord ribbon per request; falls back to the
# stored blob's ribbon. Reuses borrow's _song_pc_histogram / _cosine /
# _harmonic_score / _parse_key verbatim.
# ---------------------------------------------------------------------------


def _harmony(session_result: Optional[Dict], track: CrateTrack,
             track_result: Optional[Dict]) -> Optional[float]:
    if not isinstance(session_result, dict):
        return None
    ha = borrow._song_pc_histogram(session_result)
    hb: Optional[List[float]] = list(track.features.pc_histogram) or None
    if hb is None and isinstance(track_result, dict):
        hb = borrow._song_pc_histogram(track_result)
    ka = _key_of(session_result)
    kb = track.features.detected_key or _key_of(track_result)
    lbl = borrow._harmonic_score(ka, kb)
    if ha is None or hb is None or len(hb) != 12:
        return lbl
    cos = borrow._cosine(ha, hb)
    if borrow._parse_key(ka) is None or borrow._parse_key(kb) is None:
        return cos
    return 0.6 * cos + 0.4 * lbl


# ---------------------------------------------------------------------------
# Energy, genre, instrumentation complementarity.
# ---------------------------------------------------------------------------


def _song_energy(result: Optional[Dict]) -> Optional[float]:
    """Aggregate song energy in [0,1] for level/intensity fit. Prefers the
    energy_curve mean, then the performance_graph phrase energies, then a
    stored scalar — the same RMS-family signal CrateFeatures.energy stores at
    ingestion (Phrase.energy / bar_energies)."""
    if not isinstance(result, dict):
        return None
    ec = result.get("energy_curve")
    if isinstance(ec, list) and ec:
        vals = [float(x) for x in ec if isinstance(x, (int, float))]
        if vals:
            return _clamp01(sum(vals) / len(vals))
    g = result.get("performance_graph")
    if isinstance(g, dict) and isinstance(g.get("phrases"), list):
        es = [float(p["energy"]) for p in g["phrases"]
              if isinstance(p, dict) and isinstance(p.get("energy"), (int, float))]
        if es:
            return _clamp01(sum(es) / len(es))
    e = result.get("energy")
    if isinstance(e, (int, float)):
        return _clamp01(float(e))
    return None


def _genre_affinity(session_result: Optional[Dict], track: CrateTrack,
                    mode: str) -> Optional[float]:
    """SIMILAR: 1 if same genre else tag/subgenre Jaccard. CONTRAST: 1 −
    similarity (a toggle, not a separate path). None when the session carries
    no genre/tags to compare (typical user upload) — the term then drops out
    of the weighted mean. A session that is itself a crate track (crate-vs-
    crate) or a UI-supplied genre facet gives it substance."""
    if not isinstance(session_result, dict):
        return None
    sg = str(session_result.get("genre") or "").strip().lower()
    st = {str(t).lower() for t in (session_result.get("tags") or []) if t}
    if not sg and not st:
        return None
    if sg and track.genre and sg == track.genre.strip().lower():
        sim = 1.0
    else:
        a = set(st) | ({sg} if sg else set())
        b = ({track.genre.strip().lower()} if track.genre else set())
        b |= {t.lower() for t in track.tags}
        b |= {s.lower() for s in track.subgenres}
        union = a | b
        sim = (len(a & b) / len(union)) if union else 0.0
    return _clamp01(1.0 - sim if mode == "contrast" else sim)


def _logical_stems(result: Optional[Dict]) -> set:
    if not isinstance(result, dict):
        return set()
    sp = result.get("stems_paths") or result.get("stems") or {}
    if not isinstance(sp, dict):
        return set()
    return {borrow._logical_stem(k) for k in sp}


def _instr_complement(session_result: Optional[Dict],
                      track: CrateTrack) -> Optional[float]:
    """Reward crate tracks that SUPPLY a core stem the session LACKS (a strong
    drum loop for a drum-less session). Fraction of the session's missing core
    stems the track can supply. Neutral 0.5 when the session already has every
    core stem (a track can reinforce but not fill a hole)."""
    track_have = {borrow._logical_stem(s) for s in track.features.available_stems}
    if not track_have:
        return None
    session_have = _logical_stems(session_result)
    missing = set(_CORE_STEMS) - session_have
    if not missing:
        return 0.5
    return len(missing & track_have) / len(missing)


# ---------------------------------------------------------------------------
# The score.
# ---------------------------------------------------------------------------


def _target_stem(track: CrateTrack, stem: str) -> Optional[str]:
    """The concrete stem key in the track that serves the requested logical
    part (borrow's alias resolution — 'other' → guitar_center etc.). None when
    the track can't supply that part (and available_stems is known)."""
    aliases = borrow._stem_aliases(stem)
    have = set(track.features.available_stems)
    if not have:
        return stem  # unknown availability — don't veto on it
    return next((a for a in aliases if a in have), None)


def score_crate(session_result: Optional[Dict], track: CrateTrack,
                track_result: Optional[Dict] = None, *,
                stem: str = "other", genre_mode: str = "similar",
                clean_export: bool = False) -> Optional[Dict]:
    """Rank one crate ``track`` for a session. Returns a scored candidate dict
    (superset of borrow's candidate shape) or ``None`` when a HARD GATE vetoes
    it (tempo out of the octave-folded stretch band, harmonic clash on a
    melodic stem, an export-encumbered track under a clean-export filter, a
    meter clash, or the requested stem not present in the track).

    ``session_result`` may be ``None``/tempo-less for the blank-canvas case;
    then the tempo term and its gate are simply omitted and the track is
    ranked on the remaining signals (normalization keeps the score in [0,1]).
    """
    feat = track.features

    # --- HARD GATE: license (clean-export filter) --------------------------
    if clean_export and track.license.export_encumbered:
        return None

    # --- HARD GATE: stem availability --------------------------------------
    donor_stem = _target_stem(track, stem)
    if donor_stem is None:
        return None

    pitchless = stem in borrow._PITCHLESS

    # --- HARD GATE + sub-score: tempo (octave-folded stretch band) ---------
    session_bpm = borrow._tempo_of(session_result) if session_result else None
    folded_dist: Optional[float] = None
    stretch_ratio: Optional[float] = None
    s_tempo: Optional[float] = None
    if session_bpm and feat.tempo_bpm > 0:
        ratio = session_bpm / feat.tempo_bpm
        folded = min((abs(ratio * m - 1.0), m) for m in (0.5, 1.0, 2.0))
        folded_dist = folded[0]
        if folded_dist > borrow._STRETCH_LIMIT:
            return None  # even octave-folded, too far to stretch cleanly
        stretch_ratio = ratio * folded[1]
        s_tempo = _clamp01(1.0 - folded_dist / borrow._STRETCH_LIMIT)

    # --- HARD GATE: meter (deferred seam — fires only when both known) -----
    s_meter = _session_meter(session_result)
    if s_meter is not None and feat.time_signature != s_meter:
        return None

    # --- sub-score: harmony (+ floor gate for melodic stems) ---------------
    harm: Optional[float] = None
    if not pitchless and session_result is not None:
        harm = _harmony(session_result, track, track_result)
        if harm is not None and harm < 0.2:
            return None  # harmonic clash — never offer (borrow's floor)

    # --- transpose penalty (donor → session key) ---------------------------
    semis = 0
    session_key = _key_of(session_result)
    donor_key = feat.detected_key or _key_of(track_result)
    if not pitchless and (session_key or donor_key):
        try:
            semis = borrow._transpose_steps(donor_key, session_key)
        except Exception:  # noqa: BLE001
            semis = 0
    penalty = 2.0 ** (-abs(semis) / 6.0)

    # --- assemble weighted, confidence-scaled convex combination -----------
    # each: (name, sub-score in [0,1], confidence in [0,1])
    terms: List[Tuple[str, float, float]] = []
    if s_tempo is not None:
        terms.append(("tempo", s_tempo, 1.0))
    if harm is not None:
        # confidence = session key strength; 1.0 when unlabelled but content
        # cosine still carries the value.
        c_key = float(session_result.get("detected_key_strength") or 0.0) \
            if isinstance(session_result, dict) else 0.0
        terms.append(("harmony", _clamp01(harm), c_key if c_key > 0 else 1.0))
    elif pitchless and session_bpm:
        # drums: harmony is irrelevant, don't let its absence skew the mean.
        pass
    mel_val, mel_conf = _melody_fit(session_result, track, track_result)
    if mel_val is not None and mel_conf > 0:
        terms.append(("melody", _clamp01(mel_val), mel_conf))
    e_session = _song_energy(session_result)
    if e_session is not None and feat.energy > 0:
        terms.append(("energy", _clamp01(1.0 - abs(e_session - feat.energy)), 1.0))
    genre_val = _genre_affinity(session_result, track, genre_mode)
    if genre_val is not None:
        terms.append(("genre", genre_val, 1.0))
    instr_val = _instr_complement(session_result, track)
    if instr_val is not None:
        terms.append(("instr", instr_val, 1.0))

    num = sum(_MATCH_WEIGHTS.get(n, 0.0) * c * s for n, s, c in terms)
    den = sum(_MATCH_WEIGHTS.get(n, 0.0) * c for n, s, c in terms)
    raw = (num / den) if den > 0 else 0.0
    score = _clamp01(raw * penalty)

    return {
        "trackId": track.id,
        "matchScore": round(score, 4),
        "signals": {n: round(s, 3) for n, s, _c in terms},
        "harmonic": (round(harm, 3) if harm is not None else None),
        "tempoDistance": (round(folded_dist, 3) if folded_dist is not None else None),
        "transposeSemis": semis,
        "stretchRatio": (round(stretch_ratio, 3) if stretch_ratio is not None else None),
        "targetStem": donor_stem,
        "genreMode": genre_mode,
    }


def _session_meter(result: Optional[Dict]) -> Optional[Tuple[int, int]]:
    """Session time signature when the analysis surfaced one (deferred seam:
    the pipeline does not persist it on ``result`` today, so this returns None
    for user songs and the meter gate stays inert — exactly the seam the brief
    describes). Reads a ``time_signature`` field if a future analysis stores
    one, tolerant of list/tuple/'3/4' string forms."""
    if not isinstance(result, dict):
        return None
    ts = result.get("time_signature")
    if isinstance(ts, (list, tuple)) and len(ts) == 2:
        try:
            return (int(ts[0]), int(ts[1]))
        except (TypeError, ValueError):
            return None
    if isinstance(ts, str) and "/" in ts:
        a, _, b = ts.partition("/")
        try:
            return (int(a), int(b))
        except ValueError:
            return None
    return None


def rank_crate(session_result: Optional[Dict], tracks: List[CrateTrack],
               blobs: Optional[Dict[str, Dict]] = None, *,
               stem: str = "other", genre_mode: str = "similar",
               clean_export: bool = False, limit: int = 24) -> List[Dict]:
    """Score + rank a list of crate tracks for a session, best-first. ``blobs``
    maps track id → stored analysis (for the richer melody/harmony terms);
    absent, each track is scored on its features alone. Ties break toward the
    less-encumbered license (CC0 < CC-BY < BY-SA)."""
    from tone_forge.crate import registry as _reg

    scored: List[Tuple[Dict, CrateTrack]] = []
    for t in tracks:
        blob = (blobs or {}).get(t.id)
        sc = score_crate(session_result, t, blob, stem=stem,
                         genre_mode=genre_mode, clean_export=clean_export)
        if sc is not None:
            scored.append((sc, t))
    scored.sort(
        key=lambda pair: (pair[0]["matchScore"],
                          -_reg.license_rank(pair[1].license.license_id)),
        reverse=True)
    return [sc for sc, _t in scored[:max(0, limit)]]
