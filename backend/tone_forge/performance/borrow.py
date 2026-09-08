"""Borrow Beat — real drum loops from ANOTHER song as tempo-matched pads.

The honest pivot away from Re-Drum: instead of re-synthesizing drums from a
noisy hit table (garbage in, garbage out), serve the DONOR song's actual
recorded drum-groove loops. They're coherent by construction — a real
drummer's bars — and the only adaptation needed is TEMPO: each loop is
time-stretched to the CURRENT song's tempo so it locks to the grid.

MVP is drums (pitchless, so no key matching). The same rails generalize to
bass/harmonic stems later, gated on key compatibility (Phase 2).

Rendered loops land in a per-(donor, stem, target-tempo) cache and are
served like the drum-kit composites; the client loads them as loopable
file pads, which its existing bar-sync already phase-locks.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BORROW_VERSION = 1
_MAX_LOOPS = 4          # loops offered per donor
_STRETCH_LIMIT = 0.5    # refuse to stretch beyond ±50% (artifacts)


def _cache_dir() -> Optional[Path]:
    raw = os.environ.get("TONEFORGE_BORROW_CACHE")
    if raw == "0":
        return None
    root = Path(raw) if raw else Path.home() / ".toneforge" / "borrow_cache"
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root
    except Exception:
        return None


def _tempo_of(result: Dict) -> Optional[float]:
    for k in ("tempo_bpm", "tempo"):
        v = result.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


# Pitchless stems need only tempo; melodic ones must be harmonically
# compatible with the current song (Phase 2).
_PITCHLESS = {"drums"}

_PITCH_CLASS = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "FB": 4,
    "F": 5, "E#": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9,
    "A#": 10, "BB": 10, "B": 11, "CB": 11,
}
# Mode → whether its tonic triad is minor-quality (for relative pairing).
_MINOR_MODES = {"minor", "aeolian", "dorian", "phrygian", "locrian"}


def _parse_key(s: Optional[str]) -> Optional[Tuple[int, bool]]:
    """'C# minor' / 'D mixolydian' → (tonic_pitch_class, is_minor). None
    when unparseable."""
    if not isinstance(s, str) or not s.strip():
        return None
    parts = s.strip().split()
    root = parts[0].upper()
    pc = _PITCH_CLASS.get(root)
    if pc is None:
        return None
    mode = parts[1].lower() if len(parts) > 1 else "major"
    return pc, mode in _MINOR_MODES


def _harmonic_score(a: Optional[str], b: Optional[str]) -> float:
    """0..1 compatibility for mixing key `b` (donor) under key `a` (song).
    Camelot-style: same key = 1, relative major/minor = 0.9, a perfect
    fifth away = 0.8, up/down one semitone (a light pitch nudge) = 0.5,
    else 0. Unknown key on either side = 0.4 (unranked-but-allowed)."""
    ka, kb = _parse_key(a), _parse_key(b)
    if ka is None or kb is None:
        return 0.4
    (pa, ma), (pb, mb) = ka, kb
    if pa == pb and ma == mb:
        return 1.0
    # relative major/minor: minor tonic is 3 semitones below its relative
    # major (Am ↔ C). Same "key signature".
    rel = ((pa + 3) % 12, False) if ma else ((pa - 3) % 12, True)
    if (pb, mb) == rel:
        return 0.9
    if ma == mb and (pb - pa) % 12 in (5, 7):   # perfect fourth / fifth
        return 0.8
    if ma == mb and (pb - pa) % 12 in (1, 11):   # ±1 semitone (small shift)
        return 0.5
    return 0.0


def _best_loop_spans(result: Dict, n: int) -> List[Tuple[float, float]]:
    """Up to n strong 2-bar spans between downbeats, spread across the song,
    skipping intro/outro material (same selection as the Drum Kit grooves)."""
    downs = [float(d) for d in (result.get("downbeats_s") or [])
             if isinstance(d, (int, float))]
    if len(downs) < 3:
        return []
    sections = []
    for s in result.get("sections") or []:
        if not isinstance(s, dict):
            continue
        a = s.get("start_time", s.get("start_s", s.get("start")))
        b = s.get("end_time", s.get("end_s", s.get("end")))
        label = str(s.get("type") or s.get("label") or "").lower()
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            sections.append((float(a), float(b), label))

    def _boundary(t: float) -> bool:
        for a, b, label in sections:
            if a <= t < b:
                return any(w in label for w in ("intro", "outro", "ending"))
        return False

    spans = [(downs[i], downs[i + 2]) for i in range(0, len(downs) - 2, 2)]
    dur = result.get("duration_sec")
    if isinstance(dur, (int, float)) and dur > 0:
        spans = [s for s in spans if s[1] <= float(dur)]
    body = [s for s in spans if not _boundary(s[0])] or spans
    if not body:
        return []
    k = min(n, len(body))
    return [body[int(round(i * (len(body) - 1) / max(1, k - 1)))]
            for i in range(k)]


def _cache_key(donor_id: str, stem: str, target_bpm: float,
               span: Tuple[float, float]) -> str:
    h = hashlib.sha1(
        f"{donor_id}|{stem}|{round(target_bpm, 2)}|{round(span[0], 3)}|"
        f"{round(span[1], 3)}|v{BORROW_VERSION}".encode()).hexdigest()[:20]
    return f"borrow_{h}.wav"


def sample_path(fname: str) -> Optional[Path]:
    d = _cache_dir()
    if d is None:
        return None
    p = d / fname
    return p if p.exists() and p.stat().st_size > 0 else None


def render_borrow_loops(donor_id: str, donor_result: Dict, stem: str,
                        target_bpm: float,
                        donor_stem: Optional[str] = None) -> List[Dict]:
    """Render the donor's best loops, time-stretched to target_bpm. `stem`
    is the logical role (drums/bass/other); `donor_stem` the actual key in
    the donor's stems_paths (e.g. 'guitar_center' for 'other'). Heavy."""
    donor_stem = donor_stem or stem
    try:
        import numpy as np
        import librosa
        import soundfile as sf
    except ImportError:
        return []
    out_dir = _cache_dir()
    if out_dir is None:
        return []

    donor_bpm = _tempo_of(donor_result)
    if not donor_bpm or not target_bpm:
        return []
    ratio = target_bpm / donor_bpm
    if abs(ratio - 1.0) > _STRETCH_LIMIT:
        # Too far to stretch cleanly — try the octave (half/double time)
        # so a 180 BPM donor can still lend to a 95 BPM song.
        for mult in (0.5, 2.0):
            if abs(ratio * mult - 1.0) <= _STRETCH_LIMIT:
                ratio *= mult
                break
        else:
            return []

    spans = _best_loop_spans(donor_result, _MAX_LOOPS)
    if not spans:
        return []

    import tempfile

    from tone_forge.stem_fetch import materialize_stems

    pads: List[Dict] = []
    with tempfile.TemporaryDirectory(prefix="toneforge_borrow_") as td:
        stems = materialize_stems(donor_result, Path(td), roles=[donor_stem])
        wav = stems.get(donor_stem)
        if wav is None:
            return []
        try:
            y, sr = sf.read(str(wav), dtype="float32", always_2d=True)
        except Exception:
            return []

        for i, (a, b) in enumerate(spans):
            fname = _cache_key(donor_id, donor_stem, target_bpm, (a, b))
            dest = out_dir / fname
            if not (dest.exists() and dest.stat().st_size > 0):
                i0, i1 = int(a * sr), min(int(b * sr), y.shape[0])
                if i1 - i0 < int(0.2 * sr):
                    continue
                seg = y[i0:i1]
                # Phase-vocoder stretch per channel to the target tempo.
                # rate > 1 = faster/shorter (donor slower than target).
                try:
                    chans = [librosa.effects.time_stretch(
                        np.ascontiguousarray(seg[:, c]), rate=1.0 / ratio)
                        for c in range(seg.shape[1])]
                    m = min(len(c) for c in chans)
                    stretched = np.stack([c[:m] for c in chans], axis=1)
                except Exception:
                    stretched = seg
                peak = float(np.max(np.abs(stretched))) or 1.0
                stretched = (stretched / peak * 0.89).astype(np.float32)
                try:
                    tmp = dest.with_name(dest.name + ".part.wav")
                    sf.write(str(tmp), stretched, sr, subtype="PCM_16")
                    tmp.rename(dest)
                except Exception:
                    continue
            pads.append({
                "padIdx": i,
                "name": f"Loop {i + 1}",
                "category": "DRUMS" if stem == "drums" else stem.upper(),
                "family": "percussion" if stem == "drums" else "mixed",
                "colorHint": "#3B82F6",
                "loopable": True,
                # File-backed loop: loopPointSec 0 tells the scheduler to
                # loop the whole file (it's already exactly 2 bars at the
                # target tempo), bar-quantized so layers phase-lock.
                "loopPointSec": 0,
                "loopScore": 1.0,
                "defaultQuantize": "1 bar",
                "sampleFile": fname,
            })
    return pads


# ---------------------------------------------------------------------------
# Donor ranking (Phase 1: tempo only for drums; harmonic added in Phase 2)
# ---------------------------------------------------------------------------

def _stem_aliases(stem: str) -> Tuple[str, ...]:
    # "other" (harmonic/guitar/keys) is stored under a mix of names.
    if stem == "other":
        return ("other", "guitar_center", "guitar_sides", "guitar",
                "keys", "piano", "synth")
    return (stem,)


def borrow_candidates(entries: List[Dict], entry_id: str, stem: str,
                      target_bpm: Optional[float],
                      target_key: Optional[str] = None) -> List[Dict]:
    """Rank donor songs worth borrowing `stem` from. Drums: tempo proximity
    only (octave-folded). Melodic stems (bass/other): tempo AND harmonic
    compatibility with the current song's key. Best-first."""
    pitchless = stem in _PITCHLESS
    aliases = _stem_aliases(stem)
    out: List[Dict] = []
    for e in entries:
        eid = str(e.get("id") or "")
        if not eid or eid == entry_id:
            continue
        r = e.get("result")
        if not isinstance(r, dict):
            continue
        stems = r.get("stems_paths") or {}
        donor_stem = next((a for a in aliases if a in stems), None)
        if donor_stem is None:
            continue
        if len(r.get("downbeats_s") or []) < 3:
            continue
        dbpm = _tempo_of(r)
        if not dbpm:
            continue
        ratio = (target_bpm / dbpm) if target_bpm else 1.0
        folded = min((abs(ratio * m - 1.0), m) for m in (0.5, 1.0, 2.0))
        if folded[0] > _STRETCH_LIMIT:
            continue

        harm = 1.0
        donor_key = r.get("detected_key") or r.get("key")
        if not pitchless:
            harm = _harmonic_score(target_key, donor_key)
            if harm <= 0.0:
                continue  # harmonically clashes — never offer
        # score: harmony dominates for melodic; tempo closeness refines.
        score = harm - 0.3 * folded[0]
        out.append({
            "entryId": eid,
            "name": str(e.get("name") or eid),
            "tempo": round(dbpm, 1),
            "key": donor_key,
            "tempoDistance": round(folded[0], 3),
            "harmonic": round(harm, 2),
            "score": round(score, 3),
            "donorStem": donor_stem,
        })
    out.sort(key=lambda c: c["score"], reverse=True)
    return out


def borrow_job(donor_id: str, donor_result: Dict, stem: str,
               target_bpm: float, donor_stem: Optional[str] = None):
    """Process-pool entry point."""
    return render_borrow_loops(donor_id, donor_result, stem, target_bpm,
                               donor_stem=donor_stem)
