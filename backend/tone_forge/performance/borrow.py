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

BORROW_VERSION = 7      # bumped: borrow now serves each song's CURATED AUTO-KIT (render_kit_loops), not N section-loops per stem
_LOOPS_PER_SOURCE = 8   # half of a 16-pad grid per song (initial | donor)
# Curated-kit borrow: how many pads AutoKitBuilder is asked for per song. The
# donor/host kits mirror what loading the song DIRECTLY gives (~12 stem-spread,
# quality-gated pads) instead of the old 8-loops-per-stem-per-song flood (≤64).
_KIT_PADS_PER_SOURCE = 12
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
               span: Tuple[float, float],
               target_key: Optional[str] = None) -> str:
    # target_key is APPENDED only when present so the untargeted default render
    # keeps the byte-identical filename it has always had — a transposed /
    # retimed render lands in its own cache slot and never collides with (or
    # overwrites) the true default. target_bpm is already part of the key, so a
    # donor stretched to a user target BPM is likewise cached separately.
    base = (f"{source_id}|{stem}|{round(target_bpm, 2)}|{round(span[0], 3)}|"
            f"{round(span[1], 3)}|v{BORROW_VERSION}")
    if target_key:
        base += f"|k={target_key}"
    h = hashlib.sha1(base.encode()).hexdigest()[:20]
    return f"borrow_{h}.wav"


def _transpose_steps(donor_key: Optional[str],
                     target_key: Optional[str]) -> int:
    """Shortest SIGNED semitone move from the donor's tonic to the target's,
    octave-equivalent so we never shift more than a tritone in either
    direction (range [-5, +6]). 0 when either key is unparseable — we simply
    don't transpose rather than guess. Only the pitch class matters here; mode
    (major/minor) is a colour, not a transposition, so a G-minor donor asked
    for 'G major' stays put (0 steps) and a C→G ask moves +7 → folds to -5."""
    dk, tk = _parse_key(donor_key), _parse_key(target_key)
    if dk is None or tk is None:
        return 0
    diff = (tk[0] - dk[0]) % 12          # 0..11 semitones up from donor
    if diff > 6:
        diff -= 12                       # take the shorter downward path
    return diff


def _pitch_shift(seg, sr: int, n_steps: int, np, librosa):
    """Offline pitch-shift by `n_steps` semitones, length-preserving. Uses
    ffmpeg's resample path first — `asetrate` (raises pitch AND speed) →
    `aresample` (restore the rate) → `atempo` (restore the duration) — the same
    SoundTouch/WSOLA-grade engine `_time_stretch` already trusts for tempo.
    The librosa phase vocoder it used to use sounded "underwater"/phasey on
    bass and chords, which is exactly the material key-conform now moves by
    default, so the vocoder is the FALLBACK, not the primary. A no-op at 0
    steps; falls back to the untouched segment on any error (a wrong-pitch loop
    beats a crash)."""
    if n_steps == 0:
        return seg
    import shutil
    import subprocess
    import tempfile as _tf
    factor = 2.0 ** (n_steps / 12.0)           # > 1 = shift up
    if shutil.which("ffmpeg"):
        try:
            import soundfile as sf
            # atempo restores duration; chain to stay in its 0.5..2.0 window
            # (a ±6-semitone shift is 0.707..1.414, in range, but be safe).
            inv, stages = 1.0 / factor, []
            t = inv
            while t > 2.0:
                stages.append(2.0); t /= 2.0
            while t < 0.5:
                stages.append(0.5); t *= 2.0
            stages.append(t)
            atempo = ",".join(f"atempo={s:.6f}" for s in stages)
            chain = f"asetrate={int(round(sr * factor))},aresample={sr},{atempo}"
            with _tf.TemporaryDirectory(prefix="tf_pitch_") as d:
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
    # Fallback: librosa phase vocoder per channel.
    try:
        chans = [librosa.effects.pitch_shift(
            np.ascontiguousarray(seg[:, c]), sr=sr, n_steps=float(n_steps))
            for c in range(seg.shape[1])]
        m = min(len(c) for c in chans)
        return np.stack([c[:m] for c in chans], axis=1)
    except Exception:
        return seg


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


def _fold_ratio(src_bpm: float, target_bpm: float) -> Optional[float]:
    """Octave-folded tempo ratio the render applies to bring a donor loop to
    ``target_bpm``. Picks m in {0.5, 1, 2} that brings ``target/src`` closest to
    1.0 — the exact metric borrow_candidates folds with, so selection and render
    agree — keeping the actual WSOLA stretch inside its clean ±50% band (a
    half-time donor's 4-bar loop = 8 host bars, still on the downbeat grid).
    None when the raw ratio is so far off (< 0.25× or > 4×) that even folding
    can't rescue it."""
    raw_ratio = target_bpm / src_bpm
    if not (0.25 <= raw_ratio <= 4.0):
        return None
    _, m = min((abs(raw_ratio * mm - 1.0), mm) for mm in (0.5, 1.0, 2.0))
    return raw_ratio * m


def _render_segment(y, sr: int, a: float, b: float, ratio: float,
                    n_steps: int, dest, np, sf, librosa) -> bool:
    """Cut [a, b] from the loaded stem, time-stretch by ``ratio`` (tempo lock),
    pitch-shift ``n_steps`` semitones (key conform), peak-normalize, and write
    ``dest`` atomically. Returns True when ``dest`` is a usable file (already
    cached OR freshly written), False to skip this pad. The single DSP path both
    section-loop and curated-kit borrow share — same stretch→shift→seam bake."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    i0, i1 = int(a * sr), min(int(b * sr), y.shape[0])
    if i1 - i0 < int(0.2 * sr):
        return False
    seg = y[i0:i1]
    # Speed the donor to the target tempo (stretch), THEN conform the PITCH to
    # the target key (off the hot path at 0 steps). Order is stretch→shift: the
    # shift is length-preserving, so it leaves the bar-locked duration intact.
    stretched = _time_stretch(seg, sr, ratio, np, sf, librosa)
    stretched = _pitch_shift(stretched, sr, n_steps, np, librosa)
    peak = float(np.max(np.abs(stretched))) or 1.0
    stretched = (stretched / peak * 0.89).astype(np.float32)
    try:
        tmp = dest.with_name(dest.name + ".part.wav")
        sf.write(str(tmp), stretched, sr, subtype="PCM_16")
        tmp.rename(dest)
    except Exception:
        return False
    return True


def render_section_loops(source_id: str, source_result: Dict, stem: str,
                         target_bpm: float, *,
                         donor_stem: Optional[str] = None,
                         pad_base: int = 0,
                         source_tag: str = "donor",
                         stem_label: Optional[str] = None,
                         target_key: Optional[str] = None) -> List[Dict]:
    """Render one song's section loops, time-stretched to `target_bpm`, as
    loopable pads. `stem` is the logical role (drums/bass/other); `donor_stem`
    the actual key in this song's stems_paths (e.g. 'guitar_center' for
    'other'). `pad_base` offsets padIdx so two songs share one grid;
    `source_tag` ('initial'|'donor') colours the pads. `stem_label` prefixes
    the pad name ("Bass Verse") when a grid mixes several stems. Heavy.

    `target_key` (optional, e.g. 'G minor') pitch-shifts BORROWED loops to that
    key. It applies ONLY when source_tag == 'donor': the host/primary song's
    own pads (source_tag == 'initial') are NEVER transposed, so the play-along
    recording always stays true and only the added parts conform. None (the
    default) means no transpose — today's behaviour, bit-for-bit."""
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
    # Stretch the donor toward the target tempo, but OCTAVE-FOLD the ratio the
    # same way selection does (borrow_candidates below). Selection admits a
    # donor on its folded distance — a 70 BPM donor into a 140 host scores 0 via
    # m=0.5 ("play it half-time") — so rendering must honor that fold. A brief
    # attempt (796501d7) applied the RAW ratio here instead, which meant those
    # half/double-tempo donors got WSOLA-stretched by the full ~2×, shredding
    # bass/chord material ("horrible"). Folding keeps the actual stretch inside
    # WSOLA's clean ±50% range while the loop stays integer-bar and grid-locked
    # (a half-time donor's 4-bar loop = 8 host bars, still on the downbeat grid).
    ratio = _fold_ratio(src_bpm, target_bpm)
    if ratio is None:
        return []

    # Transpose is a DONOR-only affordance: never touch the host's own audio.
    # n_steps stays 0 (and the cache key stays untargeted) unless the caller
    # asked for a key AND this is a borrowed source — so the true default and
    # the host pads keep their existing, un-shifted, byte-identical renders.
    donor_key = source_result.get("detected_key") or source_result.get("key")
    n_steps = 0
    if target_key and source_tag == "donor":
        n_steps = _transpose_steps(donor_key, target_key)
    # Only vary the cache slot when we actually diverge from the default render
    # (a real transpose). n_steps == 0 → identical audio → reuse the untargeted
    # cache entry rather than duplicate it.
    cache_key = target_key if n_steps != 0 else None

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
            fname = _cache_key(source_id, donor_stem, target_bpm, (a, b),
                               cache_key)
            dest = out_dir / fname
            # Speed the donor to the target tempo (tempo × ratio → k bars at
            # target_bpm), then conform pitch (donor-only, 0 steps = no-op). The
            # host renders at ratio≈1 (no stretch, stays clean). Shared DSP path.
            if not _render_segment(y, sr, a, b, ratio, n_steps, dest,
                                   np, sf, librosa):
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
               stem_label: Optional[str] = None,
               target_key: Optional[str] = None):
    """Process-pool entry point. Renders one song's section loops. `target_key`
    is forwarded for the donor-only transpose (None = today's behaviour)."""
    return render_section_loops(
        source_id, source_result, stem, target_bpm,
        donor_stem=donor_stem, pad_base=pad_base, source_tag=source_tag,
        stem_label=stem_label, target_key=target_key)


# ---------------------------------------------------------------------------
# Curated-kit borrow — the song's AutoKit pads, conformed to the host
# ---------------------------------------------------------------------------

def _kit_pad_stem(pad: Dict) -> str:
    """The stem role a curated kit pad plays (stemSlice.stemRole — the actual
    key in stems_paths, e.g. 'drums', 'bass', 'guitar_center', 'vocals')."""
    ss = pad.get("stemSlice") or {}
    role = ss.get("stemRole")
    return role if isinstance(role, str) else ""


def _logical_stem(role: str) -> str:
    """Collapse a concrete stem role to its logical family so clients colour by
    category and the pitchless (drums) gate is unambiguous. 'guitar_center' →
    'other', 'drums' → 'drums', bass/vocals unchanged."""
    r = (role or "").lower()
    if "drum" in r:
        return "drums"
    if r == "bass":
        return "bass"
    if r in ("vocals", "vocal"):
        return "vocals"
    return "other"


def render_kit_loops(source_id: str, source_result: Dict, target_bpm: float, *,
                     source_tag: str = "donor",
                     target_key: Optional[str] = None,
                     source_name: Optional[str] = None,
                     skill: str = "intermediate",
                     pads: int = _KIT_PADS_PER_SOURCE,
                     pad_base: int = 0) -> List[Dict]:
    """Render ONE song's CURATED AUTO-KIT as borrow pads.

    This is the honest fix for the borrow "flood": instead of emitting
    ``_LOOPS_PER_SOURCE`` section loops per stem per song (up to 64 lower-
    curation pads), we serve exactly the ~12 pads the song gives when loaded
    DIRECTLY — the same AutoKitBuilder selection (stem-spread + quality-gated),
    each pad's loop region conformed to the host via the SAME borrow DSP
    (octave-folded tempo stretch + key-conform pitch shift + seam bake + cache).

    Reuse, not reimplementation:
      * SELECTION  — ``serve.kit_payload`` → AutoKitBuilder (identical to the
        GET /api/song/{id}/kit path). Each kit pad carries its stemRole, its
        loop region [loopStartSec, loopEndSec], score, name and category.
      * CONFORM    — ``_fold_ratio`` + ``_render_segment`` (the exact stretch/
        shift/normalize path ``render_section_loops`` uses).

    ``target_key`` conforms BORROWED (source_tag == 'donor') HARMONIC/MELODIC
    pads to that key; DRUMS pads are pitchless (never transposed) and the host's
    own pads (source_tag == 'initial') are never transposed — same doctrine as
    render_section_loops, applied PER PAD because one kit mixes several stems.
    ``pad_base`` offsets padIdx so host + donor share one grid; ``source_name``
    is stamped on every pad so clients label by song. Heavy (stem download +
    DSP) — call off the event loop."""
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
    ratio = _fold_ratio(src_bpm, target_bpm)
    if ratio is None:
        return []

    # SELECTION: the same curated kit a direct load produces. serve.kit_payload
    # prefers the worker-derived graph (no stems needed) and folds usage
    # feedback — byte-for-byte the /api/song/{id}/kit pad set.
    from tone_forge.performance import serve as _serve
    try:
        kit = _serve.kit_payload(source_id, source_result, skill=skill,
                                 pads=pads)
    except Exception:
        return []
    kit_pads = [p for p in (kit.get("pads") or []) if isinstance(p, dict)]
    if not kit_pads:
        return []

    donor_key = source_result.get("detected_key") or source_result.get("key")
    roles = sorted({_kit_pad_stem(p) for p in kit_pads if _kit_pad_stem(p)})
    if not roles:
        return []

    import tempfile

    from tone_forge.stem_fetch import materialize_stems

    color = _COLOR_INITIAL if source_tag == "initial" else _COLOR_DONOR
    out_pads: List[Dict] = []
    loaded: Dict[str, object] = {}   # role → (y, sr) | None (decode failed)
    with tempfile.TemporaryDirectory(prefix="toneforge_borrowkit_") as td:
        stems = materialize_stems(source_result, Path(td), roles=roles)
        for pad in kit_pads:
            role = _kit_pad_stem(pad)
            a = pad.get("loopStartSec")
            b = pad.get("loopEndSec")
            if (role not in stems
                    or not isinstance(a, (int, float))
                    or not isinstance(b, (int, float)) or b <= a):
                continue
            if role not in loaded:
                wav = stems.get(role)
                try:
                    y, sr = sf.read(str(wav), dtype="float32", always_2d=True)
                    loaded[role] = (y, sr)
                except Exception:
                    loaded[role] = None
            entry = loaded.get(role)
            if entry is None:
                continue
            y, sr = entry

            logical = _logical_stem(role)
            # Per-pad transpose: donor-only, harmonic/melodic only (drums
            # pitchless). One kit mixes stems, so the gate lives here, not at
            # the call site as it did for section loops.
            n_steps = 0
            if target_key and source_tag == "donor" and logical not in _PITCHLESS:
                n_steps = _transpose_steps(donor_key, target_key)
            cache_key = target_key if n_steps != 0 else None

            fname = _cache_key(source_id, role, target_bpm,
                               (float(a), float(b)), cache_key)
            dest = out_dir / fname
            if not _render_segment(y, sr, float(a), float(b), ratio, n_steps,
                                   dest, np, sf, librosa):
                continue
            out_pads.append({
                "padIdx": pad_base + len(out_pads),
                "name": pad.get("name") or f"Loop {len(out_pads) + 1}",
                "category": pad.get("category") or logical.upper(),
                "family": pad.get("family") or "mixed",
                # colorHint stays blue/amber BY SOURCE so the initial/donor
                # split reads at a glance (unchanged client contract); `stem`/
                # `category` let a client colour by instrument category instead.
                "colorHint": color,
                "source": source_tag,
                "sourceName": source_name or "",
                "stem": logical,
                "stemRole": role,
                "sectionType": "",
                "loopable": bool(pad.get("loopable", True)),
                # File-backed loop: whole file is an integer number of bars at
                # the target tempo, bar-quantized so layers phase-lock.
                "loopPointSec": 0,
                # Real quality signals from the curated kit — the compact (16)
                # best-of-both layout ranks pads by these (was a flat 1.0).
                "loopScore": pad.get("loopScore", 1.0),
                **({"performanceScore": pad["performanceScore"]}
                   if isinstance(pad.get("performanceScore"), (int, float))
                   else {}),
                "defaultQuantize": "1 bar",
                "sampleFile": fname,
            })
    return out_pads


def kit_borrow_job(source_id: str, source_result: Dict, target_bpm: float,
                   source_tag: str = "donor",
                   target_key: Optional[str] = None,
                   source_name: Optional[str] = None,
                   pad_base: int = 0, pads: int = _KIT_PADS_PER_SOURCE):
    """Process-pool entry point for curated-kit borrow (one song's AutoKit
    conformed to the host). Module-level + picklable args so it runs in the
    render ProcessPool."""
    return render_kit_loops(
        source_id, source_result, target_bpm, source_tag=source_tag,
        target_key=target_key, source_name=source_name, pad_base=pad_base,
        pads=pads)
