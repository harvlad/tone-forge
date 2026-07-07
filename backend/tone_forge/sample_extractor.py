"""Song-DNA chop extractors for the mobile Samples panel.

The mobile app's ``Contribute → Samples → Song DNA`` section shows one
virtual sample pack per extractor kind: **Guitar stabs / FX tails /
Ambient textures / Transitions**. Each pack is populated by scanning
the song's non-drum stems for regions that fit that sonic character.

This module is the analysis side of that feature. It sits *next to*
``contribute_chops`` rather than being folded into ``build_chops``
because:

  * The four extractors here take audio-level features (attack time,
    spectral decay, RMS variance, boundary gradients) as their
    primary signal, whereas ``build_chops`` mostly reads pre-computed
    timings (beats, chords, sections).
  * They target the ``other`` and ``guitar_*`` stems specifically;
    they'd be no-ops on drums/bass/vocals.
  * Each extractor returns a *different* number of pads (guitar stabs
    might yield 12, ambient textures 4) so we don't want to force
    them through the 24-cap diversity selector that ``build_chops``
    applies uniformly.

Emitted chop schema matches ``contribute_chops`` verbatim
(``startSec``, ``endSec``, ``kind``, ``colorHint``, …) so the mobile
client can render them through the same pad-grid code path.

Robustness contract: every extractor accepts a possibly-missing WAV
path and returns ``[]`` on any failure (import error, missing file,
librosa exception, empty audio). Never raises for well-formed input.
The bundle route calls each extractor in a ``try`` block anyway, but
keeping the failure mode consistent means partial DNA is always
better than crashed DNA.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Palette hints — matches ``contribute_chops`` conventions so the
# mobile client's colour lookup table stays a single source of truth.
_COLOR_GUITAR_STAB = "orange"
_COLOR_FX_TAIL = "violet"
_COLOR_AMBIENT = "cyan"
_COLOR_TRANSITION = "yellow"

# Per-pack cap. The mobile SamplePadGrid is 4×4 = 16 slots. We keep 12
# so the top row can host a "hero" pad picked by the diversity
# selector, but the current UI just shows the first 16.
MAX_PACK_PADS = 16

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_song_dna(
    *,
    analysis_result: Dict[str, Any],
    stem_wav_paths: Optional[Dict[str, Path]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Compute all four Song-DNA sample packs for one song.

    Args:
        analysis_result: The persisted analysis dict — reads
            ``sections``, ``beats_s``, ``downbeats_s``. Same shape as
            what ``contribute_chops.build_chops`` consumes.
        stem_wav_paths: Optional map from stem name → local Path. When
            a stem's WAV isn't available on disk (e.g. still remote
            on R2, or missing entirely) the extractors that depend on
            it return ``[]``.

    Returns:
        A dict keyed by pack kind:

            {
                "guitar_stab":     {"name": "Guitar Stabs",     "kind": "guitar_stab",     "colorHint": "orange", "pads": [...chops]},
                "fx_tail":         {"name": "FX Tails",         "kind": "fx_tail",         "colorHint": "violet", "pads": [...chops]},
                "ambient_texture": {"name": "Ambient Textures", "kind": "ambient_texture", "colorHint": "cyan",   "pads": [...chops]},
                "transition":      {"name": "Transitions",      "kind": "transition",      "colorHint": "yellow", "pads": [...chops]},
            }

        Each ``pads`` list contains 0..MAX_PACK_PADS chop dicts. Packs
        with no pads are still present in the return value so the
        mobile client can render an empty-state chip instead of
        assuming the kind isn't supported.
    """
    stem_wav_paths = stem_wav_paths or {}
    other_wav = _first_present_path(stem_wav_paths, "other", "guitar", "guitar_center", "guitar_sides")

    return {
        "guitar_stab": _pack(
            name="Guitar Stabs",
            kind="guitar_stab",
            color=_COLOR_GUITAR_STAB,
            pads=extract_guitar_stabs(other_wav, analysis_result),
        ),
        "fx_tail": _pack(
            name="FX Tails",
            kind="fx_tail",
            color=_COLOR_FX_TAIL,
            pads=extract_fx_tails(other_wav, analysis_result),
        ),
        "ambient_texture": _pack(
            name="Ambient Textures",
            kind="ambient_texture",
            color=_COLOR_AMBIENT,
            pads=extract_ambient_textures(other_wav, analysis_result),
        ),
        "transition": _pack(
            name="Transitions",
            kind="transition",
            color=_COLOR_TRANSITION,
            # Transitions read timing arrays only (no WAV required)
            # so they still populate when the ``other`` stem is
            # missing on disk.
            pads=extract_transitions(analysis_result, stem_wav_paths.get("mix")),
        ),
    }


def _pack(name: str, kind: str, color: str, pads: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Wrap the pad list in the pack manifest the mobile client
    expects. Indices are (re)assigned here so the caller doesn't have
    to remember to number them."""
    numbered: List[Dict[str, Any]] = []
    for i, p in enumerate(pads[:MAX_PACK_PADS]):
        out = dict(p)
        out["idx"] = i
        numbered.append(out)
    return {"name": name, "kind": kind, "colorHint": color, "pads": numbered}


# ---------------------------------------------------------------------------
# Guitar stabs
# ---------------------------------------------------------------------------

def extract_guitar_stabs(
    stem_wav_path: Optional[Path],
    analysis_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Onset detection on the ``other`` stem, filtered to only keep
    onsets that punch out of their local background AND decay quickly.

    A "stab" is characterised by a bright, transient hit — think a
    palm-muted chord ping or a horn-punch — so we want onsets that
    (a) stand out relative to the 200 ms of audio immediately before
    them (dynamic contrast > 2.5×) and (b) rapidly decay to a small
    fraction of their peak within 200 ms (rejects sustained pads and
    slow swells whose "peak" is just the top of a plateau).

    Comparing to a *pre-onset* background rather than the median of
    all detected onset peaks is important: when a stem has ONLY
    stabs on it, the median-of-peaks filter would reject every stab
    (they're all equally loud). Dynamic contrast works regardless of
    onset density.
    """
    y, sr = _load_wav(stem_wav_path)
    if y is None:
        return []
    try:
        import librosa
        import numpy as np
    except ImportError:
        return []

    hop = 512
    try:
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr, hop_length=hop)
    except Exception:
        return []
    if len(onset_frames) == 0:
        return []

    onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop)

    peak_window = int(0.05 * sr)   # 50 ms: transient head
    bg_window = int(0.2 * sr)      # 200 ms: pre-onset background
    tail_window = int(0.2 * sr)    # 200 ms: post-transient tail

    sections = _sections_of(analysis_result)
    stabs: List[Dict[str, Any]] = []
    y_abs = np.abs(y)
    for t in onset_times:
        onset_sample = int(t * sr)
        # Peak in the head of the transient.
        p_end = min(onset_sample + peak_window, y.shape[0])
        if p_end <= onset_sample:
            continue
        peak = float(np.max(y_abs[onset_sample:p_end]))
        if peak <= 1e-4:
            continue
        # Background right before the onset. Guard against onsets at
        # the very start of the file where there's nothing preceding.
        bg_start = max(0, onset_sample - bg_window)
        if onset_sample - bg_start < int(0.02 * sr):
            # <20 ms of history is not enough to characterise the
            # background — skip conservatively.
            continue
        bg = float(np.mean(y_abs[bg_start:onset_sample])) + 1e-6
        if peak < 2.5 * bg:
            continue
        # Tail: 150–350 ms after the onset. A stab decays fast, so
        # the tail's average amplitude should be a small fraction of
        # the peak. If the tail is still loud, this is a sustained
        # note, not a stab.
        tail_start = min(onset_sample + int(0.15 * sr), y.shape[0])
        tail_end = min(tail_start + tail_window, y.shape[0])
        if tail_end > tail_start:
            tail_avg = float(np.mean(y_abs[tail_start:tail_end]))
            if tail_avg > 0.4 * peak:
                continue
        end = min(float(t) + 0.3, y.shape[0] / sr)
        if end - float(t) < 0.05:
            continue
        stabs.append(_chop(
            start=float(t),
            end=end,
            kind="guitar_stab",
            color=_COLOR_GUITAR_STAB,
            section_label=_section_label_at(sections, float(t)),
        ))
    # Even-spread downsample so the pad grid covers the whole song.
    return _downsample_even(stabs, MAX_PACK_PADS)


# ---------------------------------------------------------------------------
# FX tails
# ---------------------------------------------------------------------------

def extract_fx_tails(
    stem_wav_path: Optional[Path],
    analysis_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Post-onset tail slices where the spectral energy decays over
    > 0.6 s. Captures reverb wash, delay throws, cymbal-like sustain
    tails.

    Definition of "decay time": from the peak RMS in the 100 ms
    following the onset, how long until RMS falls below 40% of that
    peak. Onsets with decay < 0.6 s are stab-like (already covered by
    the stabs extractor) and get rejected here.
    """
    y, sr = _load_wav(stem_wav_path)
    if y is None:
        return []
    try:
        import librosa
        import numpy as np
    except ImportError:
        return []

    hop = 512
    try:
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr, hop_length=hop)
    except Exception:
        return []
    if len(onset_frames) == 0:
        return []
    onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop)

    # RMS envelope on the same hop grid.
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)

    sections = _sections_of(analysis_result)
    tails: List[Dict[str, Any]] = []
    # Iterate; each onset defines a candidate tail whose end is either
    # the next onset or `onset + 4 s`, whichever is sooner.
    for i, t in enumerate(onset_times):
        next_t = float(onset_times[i + 1]) if i + 1 < len(onset_times) else float(y.shape[0] / sr)
        # Peak RMS in the 100 ms after the onset.
        peak_end_t = min(t + 0.1, next_t)
        seg = _rms_slice(rms, times, t, peak_end_t)
        if seg.size == 0:
            continue
        peak = float(np.max(seg))
        if peak <= 1e-4:
            continue
        # From peak onward, look for the frame where RMS drops below
        # 40% of peak. That's the tail end.
        search_end = min(t + 4.0, next_t)
        search = _rms_slice(rms, times, t, search_end)
        if search.size == 0:
            continue
        # Time within the search slice, in seconds relative to `t`.
        rel = np.arange(search.size) * hop / sr
        below = np.where(search < 0.4 * peak)[0]
        if below.size == 0:
            # Never decays inside the search window — treat as very
            # long tail and clamp to 4 s (or next onset).
            decay_end = search_end
        else:
            decay_end = min(t + float(rel[below[0]]), search_end)
        decay = decay_end - t
        if decay < 0.6:
            continue
        tails.append(_chop(
            start=float(t),
            end=float(decay_end),
            kind="fx_tail",
            color=_COLOR_FX_TAIL,
            section_label=_section_label_at(sections, float(t)),
        ))
    return _downsample_even(tails, MAX_PACK_PADS)


# ---------------------------------------------------------------------------
# Ambient textures
# ---------------------------------------------------------------------------

def extract_ambient_textures(
    stem_wav_path: Optional[Path],
    analysis_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Sustained low-novelty regions on the ``other`` stem: contiguous
    spans where the RMS envelope's local variance sits below a
    fraction of the mean RMS. These are pads, drones, room tone —
    material that's *musically flat* but sonically usable as a
    loopable bed.

    Minimum span length is 1.5 s so we don't emit "one silent gap
    between drum hits". Adjacent qualifying frames are merged.
    """
    y, sr = _load_wav(stem_wav_path)
    if y is None:
        return []
    try:
        import librosa
        import numpy as np
    except ImportError:
        return []

    hop = 512
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    if rms.size == 0:
        return []
    frame_time = hop / sr

    # Local variance on a rolling window (1 s ≈ sr/hop frames).
    win_frames = max(1, int(1.0 / frame_time))
    if rms.size < win_frames * 2:
        return []
    # Rolling mean + rolling variance via cumulative sums for O(N).
    cumsum = np.cumsum(np.concatenate([[0], rms]))
    cumsum_sq = np.cumsum(np.concatenate([[0], rms ** 2]))
    n = rms.size
    var = np.zeros(n, dtype=np.float32)
    for i in range(n):
        lo = max(0, i - win_frames // 2)
        hi = min(n, lo + win_frames)
        m = cumsum[hi] - cumsum[lo]
        sq = cumsum_sq[hi] - cumsum_sq[lo]
        w = hi - lo
        if w <= 1:
            continue
        mean = m / w
        var[i] = float(max(0.0, sq / w - mean * mean))

    mean_rms = float(np.mean(rms))
    if mean_rms <= 1e-6:
        return []
    # "Low-novelty" = variance < 5% of mean-RMS squared. Also require
    # the underlying signal to be present (>10% of mean RMS) so we
    # don't grab dead silence.
    novelty_thresh = (0.05 * mean_rms) ** 2
    presence_thresh = 0.1 * mean_rms
    qualifying = (var < novelty_thresh) & (rms > presence_thresh)

    sections = _sections_of(analysis_result)
    min_span_frames = int(1.5 / frame_time)
    textures: List[Dict[str, Any]] = []
    i = 0
    while i < n:
        if not qualifying[i]:
            i += 1
            continue
        j = i
        while j < n and qualifying[j]:
            j += 1
        if j - i >= min_span_frames:
            start_t = i * frame_time
            end_t = j * frame_time
            # Cap the emitted chop to 8 s — anything longer just wastes
            # sample memory; the mobile client can loop the region.
            end_t = min(end_t, start_t + 8.0)
            textures.append(_chop(
                start=float(start_t),
                end=float(end_t),
                kind="ambient_texture",
                color=_COLOR_AMBIENT,
                section_label=_section_label_at(sections, float(start_t)),
            ))
        i = j
    return _downsample_even(textures, MAX_PACK_PADS)


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

def extract_transitions(
    analysis_result: Dict[str, Any],
    mix_wav_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Slices centred on section boundaries — fills, build-ups,
    breakdowns. These are the moments a mix "turns over" and they're
    highly useful as sample material because they carry the song's
    dramatic energy without being locked to a single chord.

    We don't need audio to compute these: sections + downbeats +
    energy hints in the analysis result are enough. If a mix WAV is
    supplied we could rank boundaries by RMS gradient, but the base
    behaviour returns one 2-second chop per section boundary already.
    """
    sections = _sections_of(analysis_result)
    if not sections:
        return []
    duration = float(analysis_result.get("duration_sec") or 0.0)
    if duration <= 0.0 and sections:
        duration = float(sections[-1][1])

    # Boundaries = every section end that isn't the last section.
    # We centre a ~2 s slice on each boundary (1 s before / 1 s after).
    # Filter to boundaries where the two adjacent sections differ in
    # label (otherwise it's the same block cut in half by segmenter
    # noise, and the boundary isn't musically real).
    transitions: List[Dict[str, Any]] = []
    for i in range(len(sections) - 1):
        _, cur_end, cur_label = sections[i]
        nxt_start, _, nxt_label = sections[i + 1]
        # Boundary time = midpoint of the possibly-tiny gap between
        # this section's end and the next section's start.
        boundary = 0.5 * (cur_end + nxt_start)
        if boundary <= 0.0 or boundary >= duration:
            continue
        if (cur_label or "") == (nxt_label or "") and cur_label:
            # Same label on both sides — not a real transition.
            continue
        start = max(0.0, boundary - 1.0)
        end = min(duration, boundary + 1.0)
        if end - start < 0.5:
            continue
        transitions.append(_chop(
            start=start,
            end=end,
            kind="transition",
            color=_COLOR_TRANSITION,
            # Label the transition with the incoming section — the
            # rising side is usually more musically interesting than
            # the trailing side.
            section_label=nxt_label or cur_label,
        ))

    # If a mix WAV is available, rank by RMS gradient so the top slices
    # are the most dramatic. Falls through silently on any failure.
    if mix_wav_path is not None:
        ranked = _rank_transitions_by_gradient(transitions, mix_wav_path)
        if ranked is not None:
            transitions = ranked

    return transitions[:MAX_PACK_PADS]


def _rank_transitions_by_gradient(
    transitions: List[Dict[str, Any]],
    mix_wav_path: Path,
) -> Optional[List[Dict[str, Any]]]:
    """Load the mix WAV, compute RMS envelope, score each transition
    by the absolute RMS gradient across its boundary, and return the
    transitions re-sorted with the largest gradients first (then
    re-sorted by time so playback order matches song order).

    Returns None on any failure so the caller can fall back to the
    time-ordered list.
    """
    y, sr = _load_wav(mix_wav_path)
    if y is None:
        return None
    try:
        import librosa
        import numpy as np
    except ImportError:
        return None

    hop = 512
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    if rms.size == 0:
        return None

    def _score(chop: Dict[str, Any]) -> float:
        centre = 0.5 * (float(chop["startSec"]) + float(chop["endSec"]))
        pre_start = max(0.0, centre - 1.0)
        pre_end = centre
        post_start = centre
        post_end = min(y.shape[0] / sr, centre + 1.0)
        pre = _rms_slice(rms, np.arange(len(rms)) * hop / sr, pre_start, pre_end)
        post = _rms_slice(rms, np.arange(len(rms)) * hop / sr, post_start, post_end)
        if pre.size == 0 or post.size == 0:
            return 0.0
        return abs(float(np.mean(post)) - float(np.mean(pre)))

    scored = [(c, _score(c)) for c in transitions]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = [c for c, _ in scored[:MAX_PACK_PADS]]
    top.sort(key=lambda c: float(c["startSec"]))
    return top


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _chop(
    *,
    start: float,
    end: float,
    kind: str,
    color: str,
    section_label: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a chop dict in the schema the mobile client / launchpad
    UI already know how to render. ``idx`` is filled in later by the
    pack wrapper."""
    return {
        "startSec": round(float(start), 4),
        "endSec": round(float(end), 4),
        "durationSec": round(float(end - start), 4),
        "kind": kind,
        "root": None,
        "sectionLabel": section_label,
        "chordSymbol": None,
        "colorHint": color,
    }


def _load_wav(path: Optional[Path]) -> Tuple[Optional[Any], int]:
    """Load a mono 22.05 kHz waveform. Returns (None, 0) on any
    failure so callers can bail without a try/except each time."""
    if path is None:
        return None, 0
    p = Path(path)
    if not p.exists():
        return None, 0
    try:
        import librosa
    except ImportError:
        return None, 0
    try:
        y, sr = librosa.load(str(p), sr=22050, mono=True)
    except Exception:
        return None, 0
    if y.size == 0:
        return None, 0
    return y, int(sr)


def _rms_slice(rms: Any, times: Any, t_start: float, t_end: float) -> Any:
    """Slice an RMS envelope by time (seconds). Assumes ``times``
    is a monotonically increasing array aligned with ``rms``."""
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        return []
    lo = int(np.searchsorted(times, t_start, side="left"))
    hi = int(np.searchsorted(times, t_end, side="right"))
    lo = max(0, min(lo, rms.size))
    hi = max(lo, min(hi, rms.size))
    return rms[lo:hi]


def _sections_of(result: Dict[str, Any]) -> List[Tuple[float, float, Optional[str]]]:
    """Coerce the possibly-inconsistent section list into
    (start_s, end_s, label) tuples. Handles both ``start_time`` /
    ``end_time`` (legacy) and ``start_s`` / ``end_s`` (canonical)
    field names."""
    raw = result.get("sections")
    if not isinstance(raw, list):
        return []
    out: List[Tuple[float, float, Optional[str]]] = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        start = s.get("start_s")
        if start is None:
            start = s.get("start_time")
        if start is None:
            start = s.get("start")
        end = s.get("end_s")
        if end is None:
            end = s.get("end_time")
        if end is None:
            end = s.get("end")
        if start is None or end is None:
            continue
        try:
            s0 = float(start)
            s1 = float(end)
        except (TypeError, ValueError):
            continue
        label = s.get("label") or s.get("type") or s.get("name")
        out.append((s0, s1, str(label) if label else None))
    out.sort(key=lambda t: t[0])
    return out


def _section_label_at(
    sections: Sequence[Tuple[float, float, Optional[str]]],
    t: float,
) -> Optional[str]:
    """Return the label of the section whose span contains ``t``,
    or None if no section covers the point."""
    for s0, s1, label in sections:
        if s0 <= t < s1:
            return label
    return None


def _first_present_path(
    stem_wav_paths: Dict[str, Path],
    *keys: str,
) -> Optional[Path]:
    """Return the first present, existing stem path from ``keys``."""
    for k in keys:
        p = stem_wav_paths.get(k)
        if p is None:
            continue
        if Path(p).exists():
            return p
    return None


def _downsample_even(
    chops: Sequence[Dict[str, Any]],
    target: int,
) -> List[Dict[str, Any]]:
    """Evenly-spaced downsample by index so the pad grid covers the
    whole song rather than clustering all the pads in the first N
    onsets."""
    n = len(chops)
    if n <= target:
        return list(chops)
    return [chops[int(round(i * (n - 1) / (target - 1)))] for i in range(target)]
