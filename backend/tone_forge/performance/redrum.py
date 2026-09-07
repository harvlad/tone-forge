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
import uuid
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
REDRUM_VERSION = 3

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

    used_count: Dict[str, int] = {}
    for h in hits:
        cls = h["cls"]
        smp = _pick(cls, used_count.get(cls, 0))
        if smp is None:
            continue
        used_count[cls] = used_count.get(cls, 0) + 1
        # Velocity floor at 0.4: strength is peak-normalized per song, and a
        # fully linear map makes ghost notes vanish under the composite's
        # already-normalized level.
        gain = 0.4 + 0.6 * float(h.get("strength", 1.0))
        i0 = int(float(h["t"]) * sr)
        seg = smp[: out.shape[0] - i0]
        if seg.shape[0] <= 0:
            continue
        out[i0: i0 + seg.shape[0]] += seg * gain

    peak = float(np.max(np.abs(out)))
    if peak <= 0:
        return None
    if peak > 0.98:  # only normalize DOWN — quiet grooves stay quiet
        out *= 0.98 / peak

    dest = out_dir / cache_key(entry_id, kit_entry_id)
    # Suffix keeps ".wav" LAST — soundfile infers the container from the
    # extension and refuses a bare ".part". The uuid makes the scratch name
    # unique per render: the pool used to be single-worker, so a fixed
    # ".part.wav" could never collide, but two workers rendering the same
    # (song, kit) pair would interleave writes into one file and rename the
    # wreckage into place. Rename stays atomic, so a reader sees either the
    # old complete file or the new one.
    tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex[:8]}.part.wav")
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
