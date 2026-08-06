"""PhraseAnalyzer — cut a stem into musical phrases, on the grid.

Never cut by arbitrary time. A phrase is a grid-aligned region whose length is a
musically-sane number of bars (prefer 4, then 2, then 8, then 1) that tiles a
section. Within a section the analyzer picks the tiling length that best matches
the section duration and the stem's activity, then emits bar-aligned Phrase
objects with per-phrase energy + onset density.

Core is numpy-only (per-beat RMS + onset novelty) so it's unit-testable; if
librosa is importable (backend env) it upgrades onset detection.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

from .grid import MusicalGrid
from .graph import GridPos, Phrase

try:  # richer onsets on the backend; degrade gracefully otherwise
    import librosa  # type: ignore

    _HAVE_LIBROSA = True
except Exception:  # pragma: no cover
    _HAVE_LIBROSA = False

_PITCHED_STEMS = {"other", "bass", "vocals", "guitar", "guitar_left", "guitar_right", "piano"}
# Preferred phrase lengths in bars, best first.
_PHRASE_BARS = (4, 2, 8, 1)


def _rms_per_beat(y: np.ndarray, sr: int, beats_s: Sequence[float]) -> np.ndarray:
    out = []
    for i in range(len(beats_s) - 1):
        a, b = int(beats_s[i] * sr), int(beats_s[i + 1] * sr)
        seg = y[max(0, a):max(a + 1, b)]
        out.append(float(np.sqrt(np.mean(seg**2) + 1e-9)) if len(seg) else 0.0)
    return np.asarray(out)


def _onsets_s(y: np.ndarray, sr: int) -> np.ndarray:
    if _HAVE_LIBROSA:
        try:
            return librosa.onset.onset_detect(y=y, sr=sr, units="time", backtrack=True)
        except Exception:
            pass
    # numpy fallback: spectral-flux peaks
    hop = 512
    S = np.abs(np.fft.rfft(np.stack(
        [y[i:i + 1024] * np.hanning(1024)
         for i in range(0, max(0, len(y) - 1024), hop)]
    ), axis=1)) if len(y) > 1024 else np.zeros((1, 1))
    flux = np.sqrt(np.sum(np.clip(np.diff(S, axis=0), 0, None) ** 2, axis=1)) if S.shape[0] > 1 else np.zeros(1)
    if flux.size == 0:
        return np.array([])
    thr = flux.mean() + flux.std()
    idx = np.where((flux > thr) & (flux == np.maximum.reduce(
        [np.r_[flux[1:], 0], flux, np.r_[0, flux[:-1]]])))[0]
    return (idx * hop + 512) / sr


class PhraseAnalyzer:
    def __init__(self):
        pass

    def analyze(
        self,
        y: np.ndarray,
        sr: int,
        grid: MusicalGrid,
        stem: str,
        sections: Optional[Sequence[Tuple[float, float, str]]] = None,
    ) -> List[Phrase]:
        if y.ndim > 1:
            y = y.mean(axis=1)
        y = np.asarray(y, dtype=np.float64)
        pitched = stem in _PITCHED_STEMS
        onsets = _onsets_s(y, sr)

        # Section spans (fallback: the whole track as one span).
        spans: List[Tuple[float, float]] = (
            [(s, e) for (s, e, _lbl) in sections] if sections
            else [(0.0, grid.duration_s)]
        )

        phrases: List[Phrase] = []
        bp = grid.beat_period_s
        bpb = grid.beats_per_bar
        for (s0, s1) in spans:
            span = s1 - s0
            if span < bp:  # too short for a phrase
                continue
            # pick the phrase length (bars) that tiles the section best
            phrase_bars = self._pick_phrase_bars(span, bpb, bp)
            step = phrase_bars * bpb * bp
            t = grid.snap_to_bar(s0) if grid.downbeats else s0
            while t + step <= s1 + 1e-3:
                pos = grid.make_pos(t, t + step, snap="bar")
                phrases.append(self._phrase(y, sr, pos, stem, pitched, onsets, bp))
                t += step
            # trailing partial region → one shorter phrase if >= 1 bar
            rem = s1 - t
            if rem >= bpb * bp - 1e-3:
                pos = grid.make_pos(t, s1, snap="bar")
                phrases.append(self._phrase(y, sr, pos, stem, pitched, onsets, bp))
        return phrases

    def _pick_phrase_bars(self, span_s: float, beats_per_bar: int, beat_period: float) -> int:
        bar_s = beats_per_bar * beat_period
        n_bars = span_s / bar_s if bar_s > 0 else 4
        for pb in _PHRASE_BARS:
            if n_bars >= pb:
                return pb
        return 1

    def _phrase(
        self, y: np.ndarray, sr: int, pos: GridPos, stem: str, pitched: bool,
        onsets: np.ndarray, bp: float,
    ) -> Phrase:
        a, b = int(pos.start_s * sr), int(pos.end_s * sr)
        seg = y[max(0, a):max(a + 1, b)]
        energy = float(np.sqrt(np.mean(seg**2) + 1e-9)) if len(seg) else 0.0
        n_on = int(np.sum((onsets >= pos.start_s) & (onsets < pos.end_s))) if onsets.size else 0
        beats = max(1.0, pos.length_beats)
        onset_density = n_on / beats
        return Phrase(
            stem=stem, pos=pos, onset_density=onset_density,
            pitched=pitched, energy=energy,
        ).with_id()
