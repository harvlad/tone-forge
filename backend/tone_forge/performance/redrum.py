"""Re-Drum — swap the kit, keep the groove.

The drum-kit pipeline made a song's drums separable into two independent
halves: the PERFORMANCE (the hits table — every hit's time, class, and
strength) and the KIT (the median-stacked one-shot composites). This module
recombines them: place a target kit's composites at the source song's hit
times, velocity-scaled by each hit's strength, and render a replacement
drums stem. Same drummer, different drums — or another song's drums entirely.

Kit sources are other analyzed songs (``kit="song:<entry_id>"``) or the song
itself (``kit="self"`` — re-triggering its own composites tightens a muddy
performance, a subtle but audible cleanup). The render is deterministic per
(source hits, kit files) pair and cached like the composites.

All CPU DSP; runs in the /kit route's process pool alongside the hit/render
backfills it depends on.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from .drum_kit import DRUM_HITS_RESULT_KEY, HITS_VERSION, select_one_shots
from .drum_kit_render import RENDER_VERSION, load_manifest, sample_path

logger = logging.getLogger(__name__)

# v2: render stereo. v1 wrote a mono file that REPLACED a stereo demucs
# stem in the clients' stem players — a channel-count change on a live
# mixer bus, which iOS treats as a graph reconfiguration (AVAudioEngine
# stops itself and drops every scheduled segment). Bumping the version
# re-renders the mono files already sitting in the server cache.
# v3: true stereo passthrough of render-v2 stereo composites (v2 was
# dual-mono — crash-safe but the collapsed image was plainly audible).
REDRUM_VERSION = 7

# When the target kit lacks a class the groove uses, fall through this map
# rather than dropping the hit — a groove with holes reads as a glitch, a
# near-neighbor substitution reads as a style choice.
_CLASS_FALLBACK = {
    "kick": ("kick", "tom", "perc", "snare"),
    "snare": ("snare", "perc", "tom", "kick"),
    "tom": ("tom", "kick", "snare", "perc"),
    "hat_closed": ("hat_closed", "hat_open", "perc", "cymbal"),
    "hat_open": ("hat_open", "hat_closed", "cymbal", "perc"),
    "cymbal": ("cymbal", "hat_open", "hat_closed", "perc"),
    "perc": ("perc", "snare", "hat_closed", "tom"),
}


def _cache_dir() -> Optional[Path]:
    raw = os.environ.get("TONEFORGE_REDRUM_CACHE")
    if raw == "0":
        return None
    root = Path(raw) if raw else Path.home() / ".toneforge" / "redrum_cache"
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root
    except Exception:
        return None


def _kit_class_files(kit_entry_id: str) -> Optional[Dict[str, List[Path]]]:
    """class → rendered composite files for a kit-source song, in pad order
    (so a kit with two kicks round-robins them). None when the song has no
    rendered composites yet."""
    manifest = load_manifest(kit_entry_id)
    if not manifest:
        return None
    out: Dict[str, List[Path]] = {}
    for pad_idx in sorted(manifest):
        fname = manifest[pad_idx]
        # Filenames are builder-generated padNN_<class>_vX-Y.wav.
        cls = fname.split("_", 1)[1].rsplit("_v", 1)[0]
        p = sample_path(kit_entry_id, fname)
        if p is not None:
            out.setdefault(cls, []).append(p)
    return out or None


def cache_key(entry_id: str, kit_entry_id: str) -> str:
    h = hashlib.sha1(
        f"{entry_id}|{kit_entry_id}|v{HITS_VERSION}.{RENDER_VERSION}.{REDRUM_VERSION}"
        .encode()).hexdigest()[:20]
    return f"redrum_{h}.wav"


def rendered_path(entry_id: str, kit_entry_id: str) -> Optional[Path]:
    d = _cache_dir()
    if d is None:
        return None
    p = d / cache_key(entry_id, kit_entry_id)
    return p if p.exists() and p.stat().st_size > 0 else None


# Re-Drum plays the groove's BACKBONE, not every detected onset. The hit
# detector over-fires badly on real stems — a 280 s song came back with 1968
# hits (1190 of them "kicks" from sub-bass bleed, 43% below 0.2 strength, 7
# hits/sec). Placing a kit sample at each = a crunchy wash, not a beat. So
# for re-drumming we keep only confident, musically-spaced hits.
_STRENGTH_FLOOR = 0.18
# Minimum seconds between kept hits of the same class — a drummer can't
# realistically retrigger a kick/snare faster than this; closer onsets are
# flams, double-triggers, or bleed. Hats genuinely go fast (16th rolls), so
# they get a shorter gate.
_MIN_GAP = {
    "kick": 0.09, "snare": 0.09, "tom": 0.09, "perc": 0.09,
    "hat_closed": 0.055, "hat_open": 0.08, "cymbal": 0.15,
}


def _sixteenth_grid(beats: List[float]) -> List[float]:
    """A 16th-note grid: 4 evenly-spaced subdivisions between each pair of
    tracked beats (a beat = a quarter note). Empty when < 2 beats."""
    grid: List[float] = []
    for a, b in zip(beats, beats[1:]):
        if b > a:
            step = (b - a) / 4.0
            grid.extend(a + k * step for k in range(4))
    if beats:
        grid.append(beats[-1])
    return grid


def _musical_hits(hits: List[Dict],
                  beats: Optional[List[float]] = None) -> List[Dict]:
    """Reduce a raw hits table to the confident, on-grid backbone.

    The detector over-fires (sub-bass bleed retriggers the low band, ghosts
    below usable strength, flam double-triggers), so placing a kit sample at
    every onset is a wash. Two stages:

      1. Drop onsets below a strength floor.
      2. QUANTIZE to the song's 16th-note grid and keep ONE hit per
         (class, grid slot) — the strongest. This collapses a bleed cluster
         near a beat into a single hit AND tightens timing, which is what
         makes the result read as a coherent beat rather than crunch. Hits
         that land more than half a 16th off any grid point are off-grid
         noise and dropped.

    Without a beat grid (rare — most songs have one) it falls back to a
    per-class minimum-gap dedupe."""
    strong = [h for h in hits if float(h.get("strength", 0.0)) >= _STRENGTH_FLOOR]
    if not strong:
        ranked = sorted(hits, key=lambda h: float(h.get("strength", 0.0)),
                        reverse=True)
        strong = ranked[: max(1, len(ranked) // 3)]

    beat_list = [float(b) for b in (beats or []) if isinstance(b, (int, float))]
    grid16 = _sixteenth_grid(beat_list)
    if len(grid16) >= 4:
        import bisect

        # Two grids: kick/snare quantize to the 8TH-note grid, hats/perc to
        # the 16th. The low band over-fires so badly (bass indistinguishable
        # from kicks) that no per-hit feature separates them; the strongest
        # musical prior left is DENSITY — a kick/snare pattern almost never
        # lands on every 16th, so binning them to 8ths caps the machine-gun
        # at one hit per 8th while hats keep their 16th detail. Keep the
        # STRONGEST hit per (class, slot); drop hits >half a slot off grid.
        grid8 = grid16[::2]
        med16 = _median_step(grid16)

        def _snap(g: List[float], t: float, tol: float):
            j = bisect.bisect_left(g, t)
            cand = [k for k in (j, j - 1) if 0 <= k < len(g)]
            if not cand:
                return None
            gi = min(cand, key=lambda k: abs(g[k] - t))
            return gi if abs(g[gi] - t) <= tol else None

        coarse = {"kick", "snare", "tom"}
        best: Dict[tuple, Dict] = {}
        for h in strong:
            t = float(h["t"])
            if h["cls"] in coarse:
                g, tol = grid8, med16              # 8th slot, ±one 16th
            else:
                g, tol = grid16, med16 * 0.5        # 16th slot, ±half a 16th
            gi = _snap(g, t, tol)
            if gi is None:
                continue
            key = (h["cls"], id(g), gi)
            if key not in best or float(h.get("strength", 0.0)) \
                    > float(best[key].get("strength", 0.0)):
                snapped = dict(h)
                snapped["t"] = round(g[gi], 4)
                best[key] = snapped
        kept = list(best.values())
        kept.sort(key=lambda h: float(h["t"]))
        return kept

    # No usable grid: per-class min-gap dedupe.
    by_cls: Dict[str, List[Dict]] = {}
    for h in strong:
        by_cls.setdefault(h["cls"], []).append(h)
    kept: List[Dict] = []
    for cls, group in by_cls.items():
        group.sort(key=lambda h: float(h["t"]))
        gap = _MIN_GAP.get(cls, 0.09)
        last_t = -1e9
        for h in group:
            t = float(h["t"])
            if t - last_t >= gap:
                kept.append(h)
                last_t = t
    kept.sort(key=lambda h: float(h["t"]))
    return kept


def _median_step(grid: List[float]) -> float:
    diffs = sorted(b - a for a, b in zip(grid, grid[1:]) if b > a)
    return diffs[len(diffs) // 2] if diffs else 0.1


def render_redrum(entry_id: str, result: Dict, kit_entry_id: str) -> Optional[Path]:
    """Render the replacement drums stem: target kit composites at the source
    song's hit times. Returns the cached WAV path, or None when either side's
    prerequisites (hits table / rendered composites) are missing."""
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        return None

    cached = rendered_path(entry_id, kit_entry_id)
    if cached is not None:
        return cached
    out_dir = _cache_dir()
    if out_dir is None:
        return None

    table = result.get(DRUM_HITS_RESULT_KEY)
    hits = table.get("hits") if isinstance(table, dict) else None
    if not hits:
        return None
    hits = _musical_hits(hits, beats=result.get("beats_s"))
    if not hits:
        return None
    class_files = _kit_class_files(kit_entry_id)
    if not class_files:
        return None

    # Load every composite once, normalized to (n, 2). All were written by
    # drum_kit_render at the kit song's native rate — one rate per kit, so
    # no per-hit resampling. Render-v1 caches were mono; duplicate those so
    # a mid-migration cache still renders instead of erroring.
    samples: Dict[str, List] = {}
    sr = None
    for cls, paths in class_files.items():
        for p in paths:
            try:
                data, file_sr = sf.read(str(p), dtype="float32",
                                        always_2d=True)
            except Exception:
                continue
            if sr is None:
                sr = file_sr
            if file_sr != sr:
                continue  # mixed-rate cache (mid-version) — skip odd one out
            if data.shape[1] == 1:
                data = np.repeat(data, 2, axis=1)
            samples.setdefault(cls, []).append(np.asarray(data[:, :2]))
    if sr is None or not samples:
        return None

    def _pick(cls: str, n_used: int):
        for c in _CLASS_FALLBACK.get(cls, (cls,)):
            group = samples.get(c)
            if group:
                return group[n_used % len(group)]  # round-robin variants
        # Chain exhausted (e.g. hats on a kick-only kit): any sample beats a
        # hole in the groove.
        for group in samples.values():
            if group:
                return group[n_used % len(group)]
        return None

    dur = result.get("duration_sec")
    last_end = max(float(h["end"]) for h in hits)
    total_s = max(float(dur) if isinstance(dur, (int, float)) and dur > 0
                  else 0.0, last_end + 3.0)
    out = np.zeros((int(total_s * sr) + sr, 2), dtype=np.float64)

    # Next onset of the SAME class, so a placed composite is CHOKED by its
    # own next hit — the whole reason the original stem didn't turn to soup.
    # Without this, a 0.5 s kick placed every 0.13 s (16ths) rings eight
    # deep, and a 2.5 s cymbal never stops: dozens of full, peak-normalized
    # one-shots overlap into mud ("sounds like soup"). Hats also choke each
    # other (closed→closed is a real choke on the kit). Cross-class ring is
    # kept — a crash SHOULD ring over the next kick — but same-class stacking
    # is what smears.
    same_next: Dict[int, float] = {}
    by_cls_times: Dict[str, List[float]] = {}
    for h in hits:
        by_cls_times.setdefault(h["cls"], []).append(float(h["t"]))
    # Hats share one choke timeline (open + closed cut each other).
    hat_times = sorted(
        t for c, ts in by_cls_times.items() if c.startswith("hat") for t in ts)
    fade = int(0.006 * sr)  # 6 ms release so the choke doesn't click

    used_count: Dict[str, int] = {}
    for idx, h in enumerate(hits):
        cls = h["cls"]
        smp = _pick(cls, used_count.get(cls, 0))
        if smp is None:
            continue
        used_count[cls] = used_count.get(cls, 0) + 1
        t = float(h["t"])
        # Velocity floor 0.15 (was 0.4): the old floor made every ghost
        # note nearly as loud as an accent, flattening the groove's
        # dynamics into a constant barrage.
        gain = 0.15 + 0.85 * float(h.get("strength", 1.0))
        i0 = int(t * sr)

        # Choke length: distance to this class's next onset (hats: next hat
        # of either kind), capped by the sample's own length.
        choke_times = hat_times if cls.startswith("hat") else by_cls_times[cls]
        nxt = None
        for tt in choke_times:
            if tt > t + 1e-4:
                nxt = tt
                break
        max_len = smp.shape[0]
        if nxt is not None:
            max_len = min(max_len, max(int(0.03 * sr), int((nxt - t) * sr)))
        seg = smp[:max_len]
        avail = out.shape[0] - i0
        if avail <= 0 or seg.shape[0] <= 0:
            continue
        seg = seg[:avail]
        if seg.shape[0] > fade * 2:
            # Release fade so a choked tail ends clean, not on a cliff.
            ramp = np.linspace(1.0, 0.0, fade)[:, None]
            seg = seg.copy()
            seg[-fade:] *= ramp
        out[i0: i0 + seg.shape[0]] += seg * gain

    peak = float(np.max(np.abs(out)))
    if peak <= 0:
        return None
    if peak > 0.98:  # only normalize DOWN — quiet grooves stay quiet
        out *= 0.98 / peak

    dest = out_dir / cache_key(entry_id, kit_entry_id)
    # Suffix keeps ".wav" LAST — soundfile infers the container from the
    # extension and refuses a bare ".part".
    tmp = dest.with_name(dest.name + ".part.wav")
    # Stereo end-to-end now: render-v2 composites carry the record's real
    # image (per-channel median), and this buffer accumulated it directly —
    # both the format-change crash AND the collapsed-image quality loss stay
    # fixed.
    try:
        sf.write(str(tmp), out.astype(np.float32), sr, subtype="PCM_16")
        tmp.rename(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        return None
    return dest


def kit_candidates(entries: List[Dict], exclude_id: str = "") -> List[Dict]:
    """Rank analyzed songs by how viable they are as a Re-Drum kit DONOR,
    judged from their persisted hits tables alone (no audio): a good donor
    has the core classes covered (kick + snare + some hat), plenty of hits
    to have stacked clean composites from, and well-isolated exemplars.

    Returns [{entryId, name, score, classes, hits}] best-first. Songs with
    no hits table are omitted — they'd need a full backfill before donating,
    and the sheet should suggest things that work on the first tap.
    """
    out: List[Dict] = []
    for e in entries:
        eid = str(e.get("id") or "")
        if not eid or eid == exclude_id:
            continue
        result = e.get("result")
        table = result.get(DRUM_HITS_RESULT_KEY) if isinstance(result, dict) else None
        hits = table.get("hits") if isinstance(table, dict) else None
        if not hits:
            continue
        classes = sorted({h["cls"] for h in hits})
        core = (("kick" in classes) + ("snare" in classes)
                + any(c.startswith("hat") for c in classes))
        iso = sum(float(h.get("isolation", 0)) for h in hits) / len(hits)
        # Coverage dominates; volume of material and isolation refine. The
        # log keeps a 2000-hit song from drowning a clean 200-hit one.
        import math

        score = core * 2.0 + math.log10(max(len(hits), 1)) + iso
        out.append({
            "entryId": eid,
            "name": str(e.get("name") or eid),
            "score": round(score, 3),
            "classes": classes,
            "hits": len(hits),
        })
    out.sort(key=lambda c: c["score"], reverse=True)
    return out


def redrum_job(entry_id: str, result: Dict,
               kit_entry_id: str, kit_result: Dict):
    """Process-pool entry: make sure BOTH sides' prerequisites exist (source
    hits; kit hits + composites), then render. Returns
    (source_hits_if_newly_attached, kit_hits_if_newly_attached, wav_path) —
    the parent persists any newly attached tables."""
    from .drum_kit import ensure_drum_hits
    from .drum_kit_render import ensure_kit_job

    src_table, src_attached = ensure_drum_hits(entry_id, result)
    kit_attached_table, _files = ensure_kit_job(kit_entry_id, kit_result)
    path = None
    if src_table is not None:
        try:
            path = render_redrum(entry_id, result, kit_entry_id)
        except Exception:
            logger.warning("redrum render failed %s kit=%s", entry_id,
                           kit_entry_id, exc_info=True)
    return (src_table if src_attached else None), kit_attached_table, \
        (str(path) if path else None)


__all__ = ["render_redrum", "redrum_job", "rendered_path", "cache_key",
           "select_one_shots", "REDRUM_VERSION"]
