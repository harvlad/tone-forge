"""Median-stacked drum one-shots — clean composites of the song's own hits.

A single slice out of the drum stem carries whatever the separator and the
arrangement left in it: bass/vocal bleed, separation artifacts, the tail of
the previous hit. But the same drum fires dozens-to-hundreds of times per
song, and everything EXCEPT the drum is uncorrelated hit-to-hit. Aligning
the instances of one class on their attack and taking a per-sample median
cancels the bleed and keeps the drum — the standard trick behind commercial
drum-sample extraction, done here with material the hits table already has.

Pipeline (runs where the drum stem is local — the /kit route's process-pool
backfill, or a dev box):

  1. ``select_one_shots`` (shared with the manifest builder, so file N and
     pad N are always the same exemplar) picks the pad exemplars.
  2. Per pad: gather same-class instances, keep the ones whose attack
     spectrum actually matches the exemplar (the class label alone is too
     coarse — a song can have two different kicks), align each by
     cross-correlating attacks, amplitude-normalize, NaN-pad past each
     instance's own clean window, and take the per-sample nanmedian.
  3. Bake the tier-1 cleanup into the file: 2 ms fade-in, exponential
     fade-out, peak normalize. Written as WAV at the stem's NATIVE sample
     rate — detection runs at 22.05 kHz but rendering must not: hats live
     above that Nyquist.

Outputs land in a per-song cache dir with a ``files.json`` manifest;
``load_manifest`` is the cheap serving-time lookup. Everything degrades to
None — the kit then serves raw ``stemSlice`` pads exactly as before.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from .drum_kit import (
    DRUM_HITS_RESULT_KEY,
    HITS_VERSION,
    _TAIL_SEC,
    ensure_drum_hits,
    select_one_shots,
)

logger = logging.getLogger(__name__)

# Bump to invalidate rendered composites after algorithm changes (cache dirs
# are versioned by BOTH: new hits tables also re-render).
RENDER_VERSION = 1

# Stacking knobs. 32 instances is plenty for median convergence; the 0.80
# attack-spectrum similarity gate keeps "the other kick" out of the stack.
_MAX_STACK = 32
_MIN_SIMILARITY = 0.80
# Hit times come from the 22.05 kHz detector at hop 512 — quantized to a
# ~23 ms grid. The alignment search must cover that quantization or the
# stack smears; ±30 ms does, with margin for backtrack variance.
_ALIGN_SEARCH_S = 0.030
_ATTACK_S = 0.025         # attack window used for alignment + similarity
_PRE_ROLL_S = 0.002       # pre-attack headroom, faded in
_FADE_IN_S = 0.002

# Per-class alignment band: cross-correlate on the band the drum actually
# lives in, so broadband bleed doesn't steer the alignment. (Same band
# philosophy as detection; ranges differ because this runs at native SR.)
_ALIGN_BAND = {
    "kick": (20.0, 150.0), "tom": (60.0, 400.0),
    "snare": (150.0, 4000.0), "perc": (150.0, 4000.0),
    "hat_closed": (4000.0, 12000.0), "hat_open": (4000.0, 12000.0),
    "cymbal": (4000.0, 12000.0),
}


def _cache_root() -> Optional[Path]:
    raw = os.environ.get("TONEFORGE_DRUMKIT_CACHE")
    if raw == "0":
        return None
    root = Path(raw) if raw else Path.home() / ".toneforge" / "drum_kit_cache"
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root
    except Exception:
        return None


def _entry_dir(entry_id: str) -> Optional[Path]:
    root = _cache_root()
    if root is None:
        return None
    # Version in the dir name: a HITS_VERSION or RENDER_VERSION bump makes
    # the old renders unreachable rather than subtly mismatched.
    return root / f"{entry_id}-v{HITS_VERSION}.{RENDER_VERSION}"


def load_manifest(entry_id: str) -> Optional[Dict[int, str]]:
    """padIdx → filename for a song's rendered composites, or None when the
    song hasn't been rendered (or the cache is disabled/stale-versioned)."""
    d = _entry_dir(entry_id)
    if d is None:
        return None
    mf = d / "files.json"
    if not mf.exists():
        return None
    try:
        raw = json.loads(mf.read_text())
        out = {int(k): str(v) for k, v in raw.items()}
    except Exception:
        return None
    # Every listed file must exist — a half-written cache serves nothing.
    for fname in out.values():
        if not (d / fname).exists():
            return None
    return out


def sample_path(entry_id: str, fname: str) -> Optional[Path]:
    """Filesystem path for one rendered sample (route serving)."""
    d = _entry_dir(entry_id)
    if d is None:
        return None
    p = d / fname
    return p if p.exists() else None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_drum_samples(entry_id: str, result: Dict) -> Optional[Dict[int, str]]:
    """Render the median-stacked composite for every one-shot pad.

    Needs the hits table AND reachable drum-stem audio. Returns the
    padIdx → filename map (also persisted as files.json), or None when
    anything essential is missing. Heavy; call off the event loop.
    """
    table = result.get(DRUM_HITS_RESULT_KEY)
    hits = table.get("hits") if isinstance(table, dict) else None
    if not hits:
        return None
    out_dir = _entry_dir(entry_id)
    if out_dir is None:
        return None

    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        return None

    import tempfile

    from tone_forge.stem_fetch import materialize_stems

    with tempfile.TemporaryDirectory(prefix="toneforge_drumrender_") as td:
        stems = materialize_stems(result, Path(td), roles=["drums"])
        wav = stems.get("drums")
        if wav is None:
            return None
        try:
            y, sr = sf.read(str(wav), dtype="float32", always_2d=True)
        except Exception:
            return None
        y = y.mean(axis=1)  # mono mix at NATIVE rate — hats need the top octave
        if y.size == 0:
            return None

        by_cls: Dict[str, List[Dict]] = {}
        for h in hits:
            by_cls.setdefault(h["cls"], []).append(h)

        # One band-filtered alignment signal per class in the kit (cached —
        # sosfiltfilt over the full stem is the expensive part).
        from scipy import signal as _sig

        align_cache: Dict[str, "np.ndarray"] = {}

        def _aligned(cls: str):
            if cls not in align_cache:
                lo, hi = _ALIGN_BAND.get(cls, (150.0, 4000.0))
                nyq = sr / 2
                lo_n, hi_n = max(lo / nyq, 0.001), min(hi / nyq, 0.95)
                try:
                    sos = _sig.butter(4, [lo_n, hi_n], btype="band", output="sos")
                    align_cache[cls] = _sig.sosfiltfilt(sos, y).astype(np.float32)
                except Exception:
                    align_cache[cls] = y
            return align_cache[cls]

        out_dir.mkdir(parents=True, exist_ok=True)
        files: Dict[int, str] = {}
        for pad_idx, (cls, _label, exemplar) in enumerate(select_one_shots(hits)):
            comp = _stack_composite(np, y, _aligned(cls), sr, exemplar,
                                    by_cls.get(cls, []), cls)
            if comp is None:
                continue
            # Version in the FILENAME too (not just the cache dir): clients
            # cache by filename, so an algorithm bump must mint new names or
            # they'd keep serving stale composites forever.
            fname = f"pad{pad_idx:02d}_{cls}_v{HITS_VERSION}-{RENDER_VERSION}.wav"
            try:
                sf.write(str(out_dir / fname), comp, sr, subtype="PCM_16")
                files[pad_idx] = fname
            except Exception:
                logger.warning("drum sample write failed: %s", fname, exc_info=True)

        if not files:
            return None
        tmp = out_dir / "files.json.part"
        tmp.write_text(json.dumps({str(k): v for k, v in files.items()}))
        tmp.rename(out_dir / "files.json")
        return files


def _stack_composite(np, y, y_align, sr: int, exemplar: Dict,
                     instances: List[Dict], cls: str):
    """One cleaned composite: gather → gate on attack similarity → align
    (on the class-band signal) → normalize → nanmedian → fades. ``y`` is the
    full-band render source, ``y_align`` the band-filtered alignment signal.
    Returns float32 array or None."""
    L = int(_TAIL_SEC[cls] * sr)
    pre = int(_PRE_ROLL_S * sr)
    attack_n = int(_ATTACK_S * sr)
    search = int(_ALIGN_SEARCH_S * sr)

    def _attack(t: float, src=y):
        i0 = int(t * sr)
        seg = src[max(0, i0): i0 + attack_n]
        return seg if seg.size == attack_n else None

    ex_attack = _attack(exemplar["t"])
    ex_align = _attack(exemplar["t"], y_align)
    if ex_attack is None or ex_align is None:
        return None

    def _spec(seg):
        # Attack magnitude spectrum, log-compressed — enough to tell "same
        # drum" from "the other kick" without a mel pipeline in this hot loop.
        mag = np.abs(np.fft.rfft(seg * np.hanning(seg.size)))
        return np.log1p(mag)

    ex_spec = _spec(ex_attack)
    ex_norm = float(np.linalg.norm(ex_spec) + 1e-12)

    scored = []
    for inst in instances:
        a = _attack(inst["t"])
        if a is None:
            continue
        s = _spec(a)
        sim = float(np.dot(s, ex_spec) / ((np.linalg.norm(s) + 1e-12) * ex_norm))
        if inst is exemplar or sim >= _MIN_SIMILARITY:
            scored.append((sim, inst))
    scored.sort(key=lambda p: p[0], reverse=True)
    picked = [inst for _, inst in scored[:_MAX_STACK]] or [exemplar]

    rows = []
    for inst in picked:
        i0 = int(inst["t"] * sr)
        # Refine alignment on the CLASS BAND: slide ±search against the
        # exemplar's band-filtered attack, so bleed outside the drum's own
        # band can't steer the lag.
        lo = max(0, i0 - search)
        window = y_align[lo: i0 + attack_n + search]
        if window.size < attack_n:
            continue
        corr = np.correlate(window, ex_align, mode="valid")
        i0 = lo + int(np.argmax(corr))
        start = i0 - pre
        if start < 0:
            continue
        seg = y[start: start + pre + L].astype(np.float64)
        peak = float(np.max(np.abs(seg))) if seg.size else 0.0
        if peak <= 0:
            continue
        seg = seg / peak
        row = np.full(pre + L, np.nan)
        # Only this instance's CLEAN window contributes; past its own next
        # onset the row is NaN so the median never averages in a neighbor.
        valid = min(seg.size, pre + int(max(0.0, inst["end"] - inst["t"]) * sr))
        row[:valid] = seg[:valid]
        rows.append(row)

    if not rows:
        return None
    stack = np.vstack(rows)
    counts = np.sum(~np.isnan(stack), axis=0)
    comp = np.zeros(pre + L)
    have = counts > 0
    if not have.any():
        return None
    # nanmedian warns on all-NaN columns; mask them out instead.
    comp[have] = np.nanmedian(stack[:, have], axis=0)

    env = np.abs(comp)
    peak = float(env.max())
    if peak <= 0:
        return None

    # Trim LEADING dead air down to ~2 ms before the attack: the detector's
    # backtracked hit time sits up to tens of ms early, and every one of
    # those ms is finger-to-sound latency on the pad.
    attack_at = np.where(env > peak * 0.15)[0]
    if attack_at.size:
        lead = max(0, int(attack_at[0]) - int(0.002 * sr))
        comp = comp[lead:]
        env = env[lead:]

    # Trim trailing dead air (median of sparse tails → near-zero), keeping
    # at least 100 ms so even a clicky hit has body.
    over = np.where(env > peak * 0.003)[0]
    end = max(int(0.1 * sr), int(over[-1]) + 1) if over.size else comp.size
    comp = comp[: min(end + int(0.01 * sr), comp.size)]

    # Baked tier-1 cleanup: short fade-in up to the attack, exponential
    # fade-out so a truncated slice never ends on a cliff.
    fi = min(int(_FADE_IN_S * sr), comp.size)
    comp[:fi] *= np.linspace(0.0, 1.0, fi) ** 2
    fo = min(max(int(0.010 * sr), int(comp.size * 0.15)), comp.size)
    comp[-fo:] *= np.exp(np.linspace(0.0, -6.0, fo))

    comp = comp / (float(np.max(np.abs(comp))) + 1e-12) * 0.89  # ≈ −1 dBFS
    return comp.astype(np.float32)


# ---------------------------------------------------------------------------
# Process-pool job (the /kit route runs this off the event loop)
# ---------------------------------------------------------------------------

def ensure_kit_job(entry_id: str, result: Dict):
    """Hits table + rendered composites in ONE pool call so the drum stem is
    fetched at most once. Returns (hits_table_if_newly_attached_or_None,
    files_map_or_None) — the parent persists the table; files live in the
    on-disk render cache."""
    table, attached = ensure_drum_hits(entry_id, result)
    files = load_manifest(entry_id)
    if files is None and table is not None:
        try:
            files = render_drum_samples(entry_id, result)
        except Exception:
            logger.warning("drum sample render failed for %s", entry_id,
                           exc_info=True)
            files = None
    return (table if attached else None), files
