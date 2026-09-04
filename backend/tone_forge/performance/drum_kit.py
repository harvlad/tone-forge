"""Drum Kit builder — the song's own drum hits laid out as a playable kit.

Where the Auto Kit gives one "Drums beat" loop pad, this builder slices the
DRUM STEM into classified one-shot hits (kick / snare / hats / toms / cymbals)
and lays them on the 16-pad grid like an MPC kit, plus a bottom row of groove
loops so the user can layer a running beat under their finger drumming.

Two-stage design mirroring the performance graph:

  1. ``detect_drum_hits`` — heavy DSP over the drum-stem WAV. Runs where the
     stem audio is local (analysis worker, dev box, or the /kit route's
     process-pool backfill after ``stem_fetch.materialize_stems``). The result
     is a small JSON-able hits table persisted under ``DRUM_HITS_RESULT_KEY``
     in the analysis result, so the no-GPU prod box serves kits without ever
     touching audio again.
  2. ``build_drum_kit`` — cheap per-request assembly of the persisted hits
     table into the frozen SamplePack manifest shape (SamplePack.swift). Pads
     carry ``stemSlice`` windows into the drums stem; clients already decode
     stem-slice pads locally, so no server-side audio rendering is needed.

Classification is deliberately signal-level (band energy + decay + flatness),
not learned: the input is an isolated drum stem, where "which band dominates
and how fast it dies" separates the kit pieces well enough for pad labeling.
The same band framing as ``extract_drum_midi`` (kick from the RAW signal —
HPSS throws tonal 808 kicks into the harmonic component).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Persisted-result key, analog of serve.GRAPH_RESULT_KEY. Version gates the
# table: bump HITS_VERSION to invalidate stale tables after algorithm changes.
DRUM_HITS_RESULT_KEY = "drum_hits"
HITS_VERSION = 1

_SR = 22050

# Per-class desired tail (seconds): how much ring a pad should carry when the
# song gives it room. Slices always cut at the next onset regardless, so these
# are ceilings, not guarantees.
_TAIL_SEC = {
    "kick": 0.5, "snare": 0.6, "tom": 0.7,
    "hat_closed": 0.25, "hat_open": 1.0, "cymbal": 2.5, "perc": 0.5,
}
_MIN_HIT_SEC = 0.06

# Pad colors follow the Contribute-mode onset color language (kick red,
# snare yellow, hat cyan, cymbal violet) as hex so every client tints the
# grid identically from colorHint — same convention as the Auto Kit.
_CLASS_HEX = {
    "kick": "#EF4444", "snare": "#F59E0B", "tom": "#F97316",
    "hat_closed": "#06B6D4", "hat_open": "#0EA5E9", "cymbal": "#A855F7",
    "perc": "#64748B",
}
_CLASS_LABEL = {
    "kick": "Kick", "snare": "Snare", "tom": "Tom",
    "hat_closed": "Hat Closed", "hat_open": "Hat Open",
    "cymbal": "Cymbal", "perc": "Perc",
}

# 12 one-shot slots (padIdx 0..11), preference-ordered per slot. Row thinking:
# row 0 kicks+snares, row 1 hats+cymbal, row 2 toms/perc — the classic finger
# drumming layout. Unfillable slots fall back through their preference list,
# then to the best remaining unused hits of any class.
_ONE_SHOT_SLOTS: List[List[str]] = [
    ["kick"], ["kick"], ["snare"], ["snare"],
    ["hat_closed"], ["hat_closed"], ["hat_open", "hat_closed"], ["cymbal", "hat_open"],
    ["tom", "perc"], ["tom", "perc"], ["perc", "snare"], ["perc", "hat_closed"],
]
# Open + closed hats choke each other, like a real hat: closing chokes the ring.
_HAT_CHOKE_GROUP = 1
_GROOVE_PADS = 4  # bottom row: bar-loop grooves to layer under the one-shots


# ---------------------------------------------------------------------------
# Stage 1 — hit detection + classification (heavy, needs the WAV)
# ---------------------------------------------------------------------------

def detect_drum_hits(wav_path: Path) -> Dict:
    """Onset-detect + classify the drum stem into a compact hits table.

    Returns ``{"version": 1, "hits": [{t, end, cls, strength, isolation}]}``.
    ``t`` is the backtracked attack time; ``end`` is where the hit's audio
    stops being usable (next onset or class tail, whichever is sooner).
    Empty hits list (silent stem, failed load) returns a valid empty table
    rather than raising — callers decide whether that's an error.
    """
    empty = {"version": HITS_VERSION, "hits": []}
    try:
        import librosa
        import numpy as np
        from scipy import signal as _sig
    except ImportError:
        return empty

    try:
        y, sr = librosa.load(str(wav_path), sr=_SR, mono=True)
    except Exception:
        return empty
    if y.size == 0:
        return empty
    if not np.all(np.isfinite(y)):
        y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)
    duration = float(y.shape[0] / sr)

    hop = 512

    def _band(lo: float, hi: float, src) -> Optional["np.ndarray"]:
        nyq = sr / 2
        lo_n, hi_n = max(lo / nyq, 0.001), min(hi / nyq, 0.999)
        if lo_n >= hi_n:
            return None
        try:
            sos = _sig.butter(4, [lo_n, hi_n], btype="band", output="sos")
            return _sig.sosfiltfilt(sos, src).astype(np.float32)
        except Exception:
            return None

    # Kick band from the RAW signal (see module docstring); the rest from the
    # percussive component so sustained tonal bleed doesn't smear the ratios.
    try:
        _, y_perc = librosa.effects.hpss(y, margin=3.0)
        y_perc = np.nan_to_num(y_perc.astype(np.float32))
    except Exception:
        y_perc = y
    filtered: Dict[str, "np.ndarray"] = {}
    for name, fy in (
        ("low", _band(20, 120, y)),
        ("lowmid", _band(120, 350, y_perc)),
        ("mid", _band(350, 4000, y_perc)),
        ("high", _band(5000, 11000, y_perc)),
    ):
        if fy is not None:
            filtered[name] = fy

    # Per-band onset detection, then union. A single full-band envelope
    # (median-aggregated) buries quiet high-hat ticks under kick/snare energy
    # — the same reason extract_drum_midi detects per band. Low band uses an
    # RMS-derivative envelope (soft/rounded kicks have no sharp flux edge).
    def _onsets(y_band, use_rms: bool = False) -> "np.ndarray":
        try:
            if use_rms:
                # RMS-derivative envelope — catches soft/rounded kicks whose
                # attack has no sharp spectral-flux edge.
                rms = librosa.feature.rms(y=y_band, hop_length=hop)[0]
                env = np.maximum(np.diff(rms, prepend=0), 0).astype(np.float64)
            else:
                # np.max, not median: the input is already band-limited, so
                # only a handful of mel bands carry energy — a median across
                # all 128 collapses to zero and no onset ever fires.
                env = librosa.onset.onset_strength(
                    y=y_band, sr=sr, hop_length=hop, aggregate=np.max)
            env = np.nan_to_num(np.asarray(env, dtype=np.float64))
            # Normalize OURSELVES and pass normalize=False: onset_detect's
            # default normalization rescales the envelope to [0, 1] AFTER a
            # caller-supplied delta is fixed, so any delta computed in raw
            # envelope units silently becomes orders of magnitude too strict.
            peak = float(env.max())
            if peak <= 0:
                return np.asarray([], dtype=int)
            env = env / peak
            return librosa.onset.onset_detect(
                onset_envelope=env, sr=sr, hop_length=hop,
                pre_max=2, post_max=2, pre_avg=3, post_avg=3,
                delta=0.05, wait=int(sr * 0.03 / hop), normalize=False)
        except Exception:
            return np.asarray([], dtype=int)

    frames = set()
    for name, use_rms in (("low", True), ("mid", False), ("high", False)):
        if name in filtered:
            frames.update(int(f) for f in _onsets(filtered[name], use_rms))
            if use_rms:  # union both envelopes for the kick band
                frames.update(int(f) for f in _onsets(filtered[name], False))
    if not frames:
        return empty
    # Merge near-coincident band onsets (a snare fires mid + high) into one
    # hit: within 30 ms keep the earliest frame.
    merged: List[int] = []
    for f in sorted(frames):
        if merged and (f - merged[-1]) * hop / sr < 0.03:
            continue
        merged.append(f)
    peak_frames = np.asarray(merged, dtype=int)
    peak_times = librosa.frames_to_time(peak_frames, sr=sr, hop_length=hop)

    # Backtrack each peak to the local energy minimum for the audible slice
    # start (pre-attack trim).
    global_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    try:
        start_frames = librosa.onset.onset_backtrack(peak_frames, global_env)
        t_starts = [float(t) for t in
                    librosa.frames_to_time(start_frames, sr=sr, hop_length=hop)]
    except Exception:
        t_starts = [float(t) for t in peak_times]

    onset_times = [float(t) for t in peak_times]
    hits: List[Dict] = []
    for i, (t_peak, t_start) in enumerate(zip(onset_times, t_starts)):
        # Backtrack occasionally walks deep into preceding silence; keep the
        # trim within one hop-ish of the attack so pads still fire instantly.
        t_start = min(max(t_start, t_peak - 0.06), t_peak)
        nxt = onset_times[i + 1] if i + 1 < len(onset_times) else duration
        gap = max(0.0, nxt - t_peak)
        # Feature window: from the attack to the next onset, capped at 1.5 s —
        # enough to measure decay class without averaging in the next hit.
        w0 = int(t_start * sr)
        w1 = int(min(nxt, t_peak + 1.5) * sr)
        if w1 - w0 < int(_MIN_HIT_SEC * sr):
            continue
        seg = y[w0:w1]

        # Band ratios over the ATTACK only (60 ms from the peak): the class
        # lives in the hit's attack + early body — a longer window folds in
        # the previous hit's ring and the room, and misclassifies quiet hats
        # sitting between loud backbeats.
        a0 = int(t_peak * sr)
        a1 = min(w1, a0 + int(0.06 * sr))
        att = y[a0:a1]
        total = float(np.sqrt(np.mean(att ** 2)) + 1e-12)
        ratios = {}
        for name, fy in filtered.items():
            b = fy[a0:a1]
            ratios[name] = float(np.sqrt(np.mean(b ** 2))) / total if b.size else 0.0
        dominant = max(ratios, key=ratios.get) if ratios else "mid"

        # Decay: time for the 10 ms-frame envelope to fall below 10% of peak.
        frame = max(1, int(0.010 * sr))
        n_frames = max(1, seg.size // frame)
        env = np.sqrt(np.mean(
            seg[: n_frames * frame].reshape(n_frames, frame) ** 2, axis=1))
        peak_env = float(env.max()) if env.size else 0.0
        below = np.where(env < 0.1 * peak_env)[0] if peak_env > 0 else np.array([])
        decay = float(below[0] * frame / sr) if below.size else float(seg.size / sr)

        try:
            flat = float(np.mean(librosa.feature.spectral_flatness(y=seg)))
        except Exception:
            flat = 0.3

        if dominant == "low":
            cls = "kick"
        elif dominant == "high":
            cls = "hat_closed" if decay < 0.25 else (
                "hat_open" if decay < 0.7 else "cymbal")
        elif dominant == "mid":
            cls = "snare"
        elif dominant == "lowmid":
            # Toms are tonal lowmid thumps with little top; snare wires put
            # real energy in the high band. (Flatness alone can't split them
            # — band-filtered noise is never spectrally flat.)
            cls = "tom" if ratios.get("high", 0.0) < 0.1 and flat <= 0.1 else "snare"
        else:
            cls = "perc"

        hits.append({
            "t": round(t_start, 4),
            "end": round(min(nxt - 0.01, t_peak + _TAIL_SEC[cls]), 4),
            "cls": cls,
            "strength": round(peak_env, 6),
            # Isolation: did the song leave room for this hit's natural tail?
            # Clean-tailed exemplars beat loud-but-buried ones at pick time.
            "isolation": round(min(1.0, gap / _TAIL_SEC[cls]), 4),
        })

    # Normalize strength to [0, 1] across the song so scoring is level-free.
    if hits:
        smax = max(h["strength"] for h in hits) or 1.0
        for h in hits:
            h["strength"] = round(h["strength"] / smax, 4)
    hits = [h for h in hits if h["end"] - h["t"] >= _MIN_HIT_SEC]
    return {"version": HITS_VERSION, "hits": hits}


def ensure_drum_hits(entry_id: str, result: Dict) -> Tuple[Optional[Dict], bool]:
    """Hits table for a persisted result, computing + attaching when absent.

    Mirrors ``serve.ensure_graph``: prefer the stored table; else find the
    drums stem (local path, or fetch the presigned R2 URL), run detection,
    and attach under ``DRUM_HITS_RESULT_KEY``. Returns (table_or_None,
    attached) — ``attached`` means the caller should persist the mutated
    result so detection runs once per song. Heavy; call off the event loop.
    """
    stored = result.get(DRUM_HITS_RESULT_KEY)
    if isinstance(stored, dict) and stored.get("version") == HITS_VERSION:
        return stored, False

    import tempfile

    from tone_forge.stem_fetch import materialize_stems

    with tempfile.TemporaryDirectory(prefix="toneforge_drumkit_") as td:
        stems = materialize_stems(result, Path(td), roles=["drums"])
        wav = stems.get("drums")
        if wav is None:
            return None, False
        table = detect_drum_hits(wav)
    result[DRUM_HITS_RESULT_KEY] = table
    return table, True


def ensure_hits_job(entry_id: str, result: Dict) -> Optional[Dict]:
    """Process-pool entry point (the /kit route's backfill path — same
    reasoning as ``ableton_kit_export.ensure_graph_job``: the DSP holds the
    GIL and starves uvicorn's event loop even via to_thread)."""
    table, attached = ensure_drum_hits(entry_id, result)
    return table if attached else None


# ---------------------------------------------------------------------------
# Stage 2 — kit assembly (cheap, table → SamplePack manifest)
# ---------------------------------------------------------------------------

def select_one_shots(hits: List[Dict]) -> List[Tuple[str, str, Dict]]:
    """Deterministic slot-fill over the hits table → [(cls, label, hit), …].

    Shared by the manifest builder AND the sample renderer
    (``drum_kit_render``) so pad N in the manifest and rendered file N are
    always the same exemplar — the two run in different processes and must
    agree without coordination.
    """
    by_cls: Dict[str, List[Dict]] = {}
    for h in hits:
        by_cls.setdefault(h["cls"], []).append(h)
    for group in by_cls.values():
        group.sort(key=lambda h: h["strength"] * (0.4 + 0.6 * h["isolation"]),
                   reverse=True)

    used: set = set()

    def _take(cls: str) -> Optional[Dict]:
        """Best unused exemplar of a class. Second/third picks of the same
        class must sit ≥ 2 s from already-used ones — repeated onsets of the
        same bar are near-identical audio, and variety is the point of a
        second kick pad."""
        for h in by_cls.get(cls, ()):  # ranked best-first above
            key = (h["cls"], h["t"])
            if key in used:
                continue
            if any(uc == cls and abs(ut - h["t"]) < 2.0 for uc, ut in used):
                continue
            used.add(key)
            return h
        return None

    picks: List[Tuple[str, str, Dict]] = []
    counts: Dict[str, int] = {}
    for prefs in _ONE_SHOT_SLOTS:
        pick = None
        for cls in prefs:
            pick = _take(cls)
            if pick:
                break
        if pick is None:  # slot starved → best remaining hit of any class
            for cls in sorted(by_cls, key=lambda c: len(by_cls[c]), reverse=True):
                pick = _take(cls)
                if pick:
                    break
        if pick is None:
            continue
        cls = pick["cls"]
        counts[cls] = counts.get(cls, 0) + 1
        label = _CLASS_LABEL[cls] + (f" {counts[cls]}" if counts[cls] > 1 else "")
        picks.append((cls, label, pick))
    return picks


def build_drum_kit(entry_id: str, result: Dict,
                   sample_files: Optional[Dict[int, str]] = None) -> Dict:
    """Assemble the persisted hits table into a 16-pad SamplePack: 12
    classified one-shots + a bottom row of bar-loop grooves.

    ``sample_files`` (padIdx → filename in the render cache) attaches a
    ``sampleUrl`` to each rendered one-shot: the median-stacked, cleaned
    composite (see ``drum_kit_render``). Clients that understand it play
    the file; everyone else falls back to the raw ``stemSlice`` window.

    Raises ValueError when the song has no usable hits table — the route
    maps that to an HTTP error the client can surface.
    """
    table = result.get(DRUM_HITS_RESULT_KEY)
    hits = table.get("hits") if isinstance(table, dict) else None
    if not hits:
        raise ValueError("No drum hits available for this song")

    pads: List[Dict] = []
    for cls, label, pick in select_one_shots(hits):
        pad_idx = len(pads)
        fname = (sample_files or {}).get(pad_idx)
        pads.append({
            "padIdx": pad_idx,
            "name": label,
            "category": "DRUMS",
            "family": "percussion",
            "colorHint": _CLASS_HEX[cls],
            "stemSlice": {"stemRole": "drums",
                          "startSec": pick["t"], "endSec": pick["end"]},
            "loopable": False,
            # Finger drumming: pads must speak on the tap, not the next bar.
            "defaultQuantize": "off",
            "performanceScore": round(
                pick["strength"] * (0.4 + 0.6 * pick["isolation"]), 3),
            **({"chokeGroup": _HAT_CHOKE_GROUP}
               if cls in ("hat_closed", "hat_open") else {}),
            **({"sampleUrl": f"/api/song/{entry_id}/drum-sample/{fname}"}
               if fname else {}),
        })

    for g in _groove_pads(result, start_idx=len(pads)):
        pads.append(g)

    if not pads:
        raise ValueError("No drum hits available for this song")

    return {
        "manifestVersion": 2,
        "packId": f"drumkit-{entry_id}",
        "name": "Drum Kit",
        "family": "percussion",
        "paletteHint": "song",
        "pads": pads,
        "provenance": (
            f"drum_kit hits=v{HITS_VERSION} n={len(hits)} "
            f"oneshots={sum(1 for p in pads if not p.get('loopable'))}"
        ),
    }


def _groove_pads(result: Dict, start_idx: int) -> List[Dict]:
    """Bottom-row groove loops: 2-bar spans between downbeats, spread across
    the song, skipping intro/outro material (same rationale as the Auto Kit's
    drum anchor — boundary bars aren't a consistent beat). Empty when the
    song has no downbeat grid; the kit is then all one-shots."""
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
    n = min(_GROOVE_PADS, len(body))
    picks = [body[int(round(i * (len(body) - 1) / max(1, n - 1)))]
             for i in range(n)]

    out = []
    for i, (a, b) in enumerate(picks):
        label = ""
        for sa, sb, sl in sections:
            if sa <= a < sb and sl:
                label = sl.title()
                break
        out.append({
            "padIdx": start_idx + i,
            "name": f"Groove {label or i + 1}",
            "category": "DRUMS",
            "family": "percussion",
            "colorHint": "#3B82F6",
            "stemSlice": {"stemRole": "drums",
                          "startSec": round(a, 4), "endSec": round(b, 4)},
            "loopStartSec": round(a, 4),
            "loopEndSec": round(b, 4),
            "loopScore": 0.5,
            "loopable": True,
            "defaultQuantize": "1 bar",
        })
    return out
