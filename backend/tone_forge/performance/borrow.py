"""Borrow — real loops from ANOTHER song (and this one) as tempo-matched pads.

The honest pivot away from Re-Drum: instead of re-synthesizing parts from a
noisy hit table (garbage in, garbage out), serve REAL recorded loops. They're
coherent by construction — a real performance's bars — and the adaptation is
TEMPO: each donor loop is time-stretched to the CURRENT song's tempo so it
locks to the grid.

Two axes grew past the drums MVP:

1. Harmonic compatibility is CONTENT-based, not key-label-based. Every song
   persists a chord ribbon (``result["chords"]`` — symbol strings with times);
   we fold it into a duration-weighted 12-bin pitch-class histogram and score
   two songs by how much harmonic material they actually share (cosine),
   refined by the circle-of-fifths / relative / parallel relationship of their
   keys. This catches songs that harmonize despite different key *labels* and
   rejects same-label pairs that share little real content. Falls back to the
   old key-label score when a song has no chord ribbon.

2. The pad grid mixes BOTH songs. The current song's own musical sections
   (verse/chorus/…) land on the first half of the grid, the donor's on the
   second half, so a player can jump between sections of either song on one
   surface — not just "full current song + 4 donor pads". Loops are cut at
   section boundaries, snapped to downbeats so they stay bar-locked.

Rendered loops land in a per-(source, stem, target-tempo, span) cache and are
served like the drum-kit composites; clients load them as loopable file pads,
which their existing bar-sync already phase-locks.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BORROW_VERSION = 4      # bumped: tempo-locked bar length + no octave-fold
_LOOPS_PER_SOURCE = 8   # half of a 16-pad grid per song (initial | donor)
_MAX_LOOPS = _LOOPS_PER_SOURCE   # back-compat alias
_STRETCH_LIMIT = 0.5    # refuse to stretch beyond ±50% (artifacts)
_SECTION_BARS = 4       # loop length cut from each section start (downbeats)

# Per-source pad colouring so a player can see at a glance which song a pad
# came from. Rendered from `colorHint` on every surface (web/iOS/desktop).
_COLOR_INITIAL = "#3B82F6"   # blue — this song
_COLOR_DONOR = "#F59E0B"     # amber — borrowed song


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
# compatible with the current song.
_PITCHLESS = {"drums"}

_PITCH_CLASS = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "FB": 4,
    "F": 5, "E#": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9,
    "A#": 10, "BB": 10, "B": 11, "CB": 11,
}
# Mode → whether its tonic triad is minor-quality (for relative pairing).
_MINOR_MODES = {"minor", "aeolian", "dorian", "phrygian", "locrian"}


# ---------------------------------------------------------------------------
# Harmonic content: chord ribbon → pitch-class histogram
# ---------------------------------------------------------------------------

# Chord quality → intervals (semitones from root) for the triad/tetrad. We only
# need the note SET, not voicing — the histogram is a bag of pitch classes.
def _quality_intervals(sfx: str) -> set:
    s = sfx.lower()
    # base triad
    if s.startswith(("dim", "°", "o")):
        base = {0, 3, 6}
    elif s.startswith(("aug", "+")):
        base = {0, 4, 8}
    elif s.startswith("sus2"):
        base = {0, 2, 7}
    elif s.startswith(("sus4", "sus")):
        base = {0, 5, 7}
    elif s.startswith(("m", "-")) and not s.startswith("maj"):
        base = {0, 3, 7}
    else:
        base = {0, 4, 7}   # major (bare symbol, "maj", "M", numerals)
    # optional 7th — dominant b7 by default, natural 7 for maj7/M7,
    # diminished-7 lowers it a further semitone.
    if "7" in s:
        if "maj7" in s or "ma7" in s or "M7" in sfx:
            base = base | {11}
        elif s.startswith(("dim7", "°7", "o7")):
            base = base | {9}
        else:
            base = base | {10}
    return base


def _chord_pcs(symbol: str) -> Optional[set]:
    """Pitch-class set for a chord symbol like 'F#m', 'Amaj7', 'G'. None when
    the root can't be parsed (e.g. 'N.C.')."""
    if not isinstance(symbol, str):
        return None
    s = symbol.strip()
    m = re.match(r"^([A-Ga-g])([#b♯♭]?)", s)
    if not m:
        return None
    root = m.group(1).upper() + m.group(2).replace("♯", "#").replace("♭", "b")
    pc = _PITCH_CLASS.get(root.upper())
    if pc is None:
        return None
    sfx = s[m.end():]
    # Slash bass note ('C/E') — ignore the bass, it's already implied harmony.
    sfx = sfx.split("/")[0]
    return {(pc + iv) % 12 for iv in _quality_intervals(sfx)}


def _chords_of(result: Dict) -> List[Dict]:
    for k in ("chords", "chords_beat_snapped"):
        c = result.get(k)
        if isinstance(c, list) and c:
            return c
    return []


def _song_pc_histogram(result: Dict) -> Optional[List[float]]:
    """Duration-weighted 12-bin pitch-class histogram from the chord ribbon,
    L2-normalised. None when no usable chords (caller falls back to key
    label). This is the real harmonic *content* of the song, not its label."""
    hist = [0.0] * 12
    total = 0.0
    for ch in _chords_of(result):
        if not isinstance(ch, dict):
            continue
        pcs = _chord_pcs(ch.get("symbol") or ch.get("name") or "")
        if not pcs:
            continue
        a = ch.get("start_s", ch.get("start", 0.0))
        b = ch.get("end_s", ch.get("end", 0.0))
        try:
            dur = max(0.05, float(b) - float(a))
        except (TypeError, ValueError):
            dur = 1.0
        for pc in pcs:
            hist[pc] += dur
        total += dur
    if total <= 0:
        return None
    norm = math.sqrt(sum(v * v for v in hist)) or 1.0
    return [v / norm for v in hist]


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return max(0.0, min(1.0, dot))   # both L2-normed & non-negative → [0,1]


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
    """0..1 compatibility for mixing key `b` (donor) under key `a` (song),
    from KEY LABELS. Camelot-style plus parallel major/minor: same key = 1,
    relative major/minor = 0.9, parallel (same tonic, flipped mode) = 0.85, a
    perfect fourth/fifth away = 0.8, ±1 semitone (a light pitch nudge) = 0.5,
    else 0. Unknown key on either side = 0.4 (unranked-but-allowed)."""
    ka, kb = _parse_key(a), _parse_key(b)
    if ka is None or kb is None:
        return 0.4
    (pa, ma), (pb, mb) = ka, kb
    if pa == pb and ma == mb:
        return 1.0
    # relative major/minor: minor tonic is 3 semitones below its relative
    # major (Am ↔ C). Same key signature.
    rel = ((pa + 3) % 12, False) if ma else ((pa - 3) % 12, True)
    if (pb, mb) == rel:
        return 0.9
    if pa == pb and ma != mb:        # parallel major/minor (C ↔ Cm)
        return 0.85
    if ma == mb and (pb - pa) % 12 in (5, 7):   # perfect fourth / fifth
        return 0.8
    if ma == mb and (pb - pa) % 12 in (1, 11):   # ±1 semitone (small shift)
        return 0.5
    return 0.0


def harmonic_compat(target_result: Dict, donor_result: Dict) -> float:
    """0..1 harmonic fit between two songs. Content cosine (real shared
    pitch-class material from the chord ribbons) dominates; the key-label
    relationship (circle-of-fifths / relative / parallel) refines it. Either
    signal alone carries when the other is missing."""
    ha = _song_pc_histogram(target_result)
    hb = _song_pc_histogram(donor_result)
    ka = target_result.get("detected_key") or target_result.get("key")
    kb = donor_result.get("detected_key") or donor_result.get("key")
    lbl = _harmonic_score(ka, kb)
    if ha is None or hb is None:
        return lbl                       # no content → pure label fallback
    cos = _cosine(ha, hb)
    if _parse_key(ka) is None or _parse_key(kb) is None:
        return cos                       # content only, no usable labels
    return 0.6 * cos + 0.4 * lbl


# ---------------------------------------------------------------------------
# Loop spans: musical sections, snapped to downbeats
# ---------------------------------------------------------------------------

def _sections_of(result: Dict) -> List[Tuple[float, float, str]]:
    out: List[Tuple[float, float, str]] = []
    for s in result.get("sections") or []:
        if not isinstance(s, dict):
            continue
        a = s.get("start_time", s.get("start_s", s.get("start")))
        b = s.get("end_time", s.get("end_s", s.get("end")))
        label = str(s.get("type") or s.get("label") or "").lower()
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b > a:
            out.append((float(a), float(b), label))
    return out


def _snap_window(start: float, end: float, downs: List[float],
                 bars: int, bar_sec: float,
                 max_t: Optional[float]) -> Optional[Tuple[float, float]]:
    """A window anchored to the first downbeat at/after `start`, exactly
    `bars` bars LONG by TEMPO (bars * bar_sec) — NOT by counting downbeats.

    Counting downbeat indices made loop length depend on how many downbeats
    the tracker found in the section: songs with sparse/irregular downbeats
    produced 3- or 6-bar windows where others got 4, so loops from different
    songs were different lengths and never locked to the grid. A tempo-derived
    length is identical for every loop, so after tempo-stretch every loop is
    exactly `bars` bars at the target tempo. Needs at least one downbeat in the
    section and enough audio after it."""
    inside = [d for d in downs if start - 0.05 <= d <= end + 0.05]
    if not inside:
        return None
    a = inside[0]
    b = a + bars * bar_sec
    if max_t is not None and b > max_t:
        # Not enough song after the anchor — try the last downbeat that still
        # leaves a full window, else give up on this section.
        if a - (b - max_t) < start - 0.05:
            return None
        b = max_t
        a = b - bars * bar_sec
    return a, b


_INTRO_OUTRO = ("intro", "outro", "ending", "count", "silence")


def _section_spans(result: Dict, n: int
                   ) -> List[Tuple[float, float, str]]:
    """Up to `n` bar-locked loop windows, one per musical section, preferring
    DISTINCT section types (verse/chorus/bridge…) and skipping intro/outro
    when there's enough body. Falls back to evenly-spread 2-bar spans when a
    song has no section analysis (older results)."""
    downs = [float(d) for d in (result.get("downbeats_s") or [])
             if isinstance(d, (int, float))]
    if len(downs) < 3:
        return []
    downs.sort()
    dur = result.get("duration_sec")
    max_t = float(dur) if isinstance(dur, (int, float)) and dur > 0 else None
    bpm = _tempo_of(result) or 120.0
    bar_sec = 240.0 / bpm            # 4 beats per bar (4/4)

    sections = _sections_of(result)
    if sections:
        body = [s for s in sections
                if not any(w in s[2] for w in _INTRO_OUTRO)] or sections
        # Prefer one of each distinct type first, then repeats to fill.
        ordered: List[Tuple[float, float, str]] = []
        seen = set()
        for s in body:
            if s[2] not in seen:
                seen.add(s[2])
                ordered.append(s)
        for s in body:
            if s not in ordered:
                ordered.append(s)
        spans: List[Tuple[float, float, str]] = []
        for a, b, label in ordered:
            if max_t is not None:
                b = min(b, max_t)
            win = _snap_window(a, b, downs, _SECTION_BARS, bar_sec, max_t)
            if win is None:
                continue
            spans.append((win[0], win[1], label or "loop"))
            if len(spans) >= n:
                break
        if spans:
            return spans

    # Fallback: strongest even-spread 2-bar spans, no labels.
    two = [(downs[i], downs[i + 2]) for i in range(0, len(downs) - 2, 2)]
    if max_t is not None:
        two = [s for s in two if s[1] <= max_t]
    if not two:
        return []
    k = min(n, len(two))
    picked = [two[int(round(i * (len(two) - 1) / max(1, k - 1)))]
              for i in range(k)]
    return [(a, b, "loop") for a, b in picked]


def _cache_key(source_id: str, stem: str, target_bpm: float,
               span: Tuple[float, float]) -> str:
    h = hashlib.sha1(
        f"{source_id}|{stem}|{round(target_bpm, 2)}|{round(span[0], 3)}|"
        f"{round(span[1], 3)}|v{BORROW_VERSION}".encode()).hexdigest()[:20]
    return f"borrow_{h}.wav"


def sample_path(fname: str) -> Optional[Path]:
    d = _cache_dir()
    if d is None:
        return None
    p = d / fname
    return p if p.exists() and p.stat().st_size > 0 else None


def _label_names(spans: List[Tuple[float, float, str]]) -> List[str]:
    """Human pad names from section labels, numbering repeats: Verse, Chorus,
    Verse 2, …. Plain 'Loop N' for the unlabelled fallback."""
    counts: Dict[str, int] = {}
    names: List[str] = []
    for i, (_a, _b, label) in enumerate(spans):
        if not label or label == "loop":
            names.append(f"Loop {i + 1}")
            continue
        counts[label] = counts.get(label, 0) + 1
        pretty = label.replace("_", " ").title()
        names.append(pretty if counts[label] == 1 else f"{pretty} {counts[label]}")
    return names


def _time_stretch(seg, sr: int, tempo_mult: float, np, sf, librosa):
    """Change tempo by `tempo_mult` (output_duration = input / tempo_mult),
    preserving pitch. ffmpeg's atempo (WSOLA, time-domain) is used first — the
    librosa phase vocoder sounded phasey/"underwater" on melodic & harmonic
    loops (fine on drums, bad on bass/chords), which is why borrowed melodic
    pads sounded submerged. Falls back to the phase vocoder if ffmpeg is
    missing. atempo takes 0.5..2.0 per stage; chain when outside (the caller's
    octave-fold keeps us in range, but be safe)."""
    if abs(tempo_mult - 1.0) <= 1e-3:
        return seg
    import shutil
    import subprocess
    import tempfile as _tf
    if shutil.which("ffmpeg"):
        try:
            stages, t = [], tempo_mult
            while t > 2.0:
                stages.append(2.0); t /= 2.0
            while t < 0.5:
                stages.append(0.5); t *= 2.0
            stages.append(t)
            chain = ",".join(f"atempo={s:.6f}" for s in stages)
            with _tf.TemporaryDirectory(prefix="tf_atempo_") as d:
                src = Path(d) / "in.wav"
                dst = Path(d) / "out.wav"
                sf.write(str(src), seg, sr, subtype="FLOAT")
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                     "-filter:a", chain, str(dst)],
                    check=True, timeout=60)
                out, _ = sf.read(str(dst), dtype="float32", always_2d=True)
                if out.shape[0] > 0:
                    return out
        except Exception:
            pass
    # Fallback: phase vocoder per channel. rate > 1 = faster/shorter.
    try:
        chans = [librosa.effects.time_stretch(
            np.ascontiguousarray(seg[:, c]), rate=tempo_mult)
            for c in range(seg.shape[1])]
        m = min(len(c) for c in chans)
        return np.stack([c[:m] for c in chans], axis=1)
    except Exception:
        return seg


def render_section_loops(source_id: str, source_result: Dict, stem: str,
                         target_bpm: float, *,
                         donor_stem: Optional[str] = None,
                         pad_base: int = 0,
                         source_tag: str = "donor",
                         stem_label: Optional[str] = None) -> List[Dict]:
    """Render one song's section loops, time-stretched to `target_bpm`, as
    loopable pads. `stem` is the logical role (drums/bass/other); `donor_stem`
    the actual key in this song's stems_paths (e.g. 'guitar_center' for
    'other'). `pad_base` offsets padIdx so two songs share one grid;
    `source_tag` ('initial'|'donor') colours the pads. `stem_label` prefixes
    the pad name ("Bass Verse") when a grid mixes several stems. Heavy."""
    donor_stem = donor_stem or stem
    try:
        import librosa
        import numpy as np
        import soundfile as sf
    except ImportError:
        return []
    out_dir = _cache_dir()
    if out_dir is None:
        return []

    src_bpm = _tempo_of(source_result)
    if not src_bpm or not target_bpm:
        return []
    # Stretch the source straight to the target tempo (atempo = target/src),
    # so the loop lands EXACTLY at the current song's tempo. The old code
    # octave-FOLDED this ratio toward 1.0 — for a donor slower than the target
    # (ratio ~2) that folded to ~1.0, i.e. NO stretch, so the donor played at
    # its own half tempo ("twice as slow"). Candidates are already octave-
    # filtered at selection; here we just hit the tempo. Chained atempo covers
    # ratios beyond 0.5–2.0.
    ratio = target_bpm / src_bpm
    if not (0.25 <= ratio <= 4.0):
        return []

    spans = _section_spans(source_result, _LOOPS_PER_SOURCE)
    if not spans:
        return []
    names = _label_names(spans)
    if stem_label:
        names = [f"{stem_label} {n}" for n in names]
    color = _COLOR_INITIAL if source_tag == "initial" else _COLOR_DONOR

    import tempfile

    from tone_forge.stem_fetch import materialize_stems

    pads: List[Dict] = []
    with tempfile.TemporaryDirectory(prefix="toneforge_borrow_") as td:
        stems = materialize_stems(source_result, Path(td), roles=[donor_stem])
        wav = stems.get(donor_stem)
        if wav is None:
            return []
        try:
            y, sr = sf.read(str(wav), dtype="float32", always_2d=True)
        except Exception:
            return []

        for i, (a, b, label) in enumerate(spans):
            fname = _cache_key(source_id, donor_stem, target_bpm, (a, b))
            dest = out_dir / fname
            if not (dest.exists() and dest.stat().st_size > 0):
                i0, i1 = int(a * sr), min(int(b * sr), y.shape[0])
                if i1 - i0 < int(0.2 * sr):
                    continue
                seg = y[i0:i1]
                # Speed the donor to the target tempo: a k-bar span at
                # donor_bpm must become k bars at target_bpm, i.e. its duration
                # scales by 1/ratio, i.e. tempo × ratio. (The old code passed
                # 1/ratio — inverted — so donors were stretched the WRONG way.)
                # The current song renders at ratio≈1 (no stretch, stays clean).
                stretched = _time_stretch(seg, sr, ratio, np, sf, librosa)
                peak = float(np.max(np.abs(stretched))) or 1.0
                stretched = (stretched / peak * 0.89).astype(np.float32)
                try:
                    tmp = dest.with_name(dest.name + ".part.wav")
                    sf.write(str(tmp), stretched, sr, subtype="PCM_16")
                    tmp.rename(dest)
                except Exception:
                    continue
            pads.append({
                "padIdx": pad_base + i,
                "name": names[i],
                "category": "DRUMS" if stem == "drums" else stem.upper(),
                "family": "percussion" if stem == "drums" else "mixed",
                "colorHint": color,
                # New: which song this pad came from, and the section it is —
                # additive fields, older clients ignore them.
                "source": source_tag,
                "sectionType": label if label != "loop" else "",
                "loopable": True,
                # File-backed loop: loopPointSec 0 tells the scheduler to loop
                # the whole file (already an integer number of bars at the
                # target tempo), bar-quantized so layers phase-lock.
                "loopPointSec": 0,
                "loopScore": 1.0,
                "defaultQuantize": "1 bar",
                "sampleFile": fname,
            })
    return pads


# Back-compat: the old drums-only entry point rendered only the donor onto
# pads 0..3. Kept so nothing that imports it breaks; delegates to the general
# path with a donor tag.
def render_borrow_loops(donor_id: str, donor_result: Dict, stem: str,
                        target_bpm: float,
                        donor_stem: Optional[str] = None) -> List[Dict]:
    return render_section_loops(
        donor_id, donor_result, stem, target_bpm,
        donor_stem=donor_stem, pad_base=0, source_tag="donor")


# ---------------------------------------------------------------------------
# Donor ranking — content-based harmonic fit + tempo proximity
# ---------------------------------------------------------------------------

def _stem_aliases(stem: str) -> Tuple[str, ...]:
    # "other" (harmonic/guitar/keys) is stored under a mix of names.
    if stem == "other":
        return ("other", "guitar_center", "guitar_sides", "guitar",
                "keys", "piano", "synth")
    return (stem,)


def borrow_candidates(entries: List[Dict], entry_id: str, stem: str,
                      target_bpm: Optional[float],
                      target_key: Optional[str] = None,
                      target_result: Optional[Dict] = None) -> List[Dict]:
    """Rank donor songs worth borrowing `stem` from. Drums: tempo proximity
    only (octave-folded). Melodic stems (bass/other): tempo AND harmonic
    compatibility — CONTENT-based (shared pitch-class material from the chord
    ribbons) when `target_result` is supplied, refined by key relationship;
    key-label only when it isn't. Best-first."""
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
            if target_result is not None:
                harm = harmonic_compat(target_result, r)
            else:
                harm = _harmonic_score(target_key, donor_key)
            if harm < 0.2:
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


def borrow_job(source_id: str, source_result: Dict, stem: str,
               target_bpm: float, donor_stem: Optional[str] = None,
               pad_base: int = 0, source_tag: str = "donor",
               stem_label: Optional[str] = None):
    """Process-pool entry point. Renders one song's section loops."""
    return render_section_loops(
        source_id, source_result, stem, target_bpm,
        donor_stem=donor_stem, pad_base=pad_base, source_tag=source_tag,
        stem_label=stem_label)
