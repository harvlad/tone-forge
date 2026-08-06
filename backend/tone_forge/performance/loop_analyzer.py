"""LoopAnalyzer + seam optimizer — measure and improve loop quality.

Given a mono signal + sample-rate + a grid-aligned [start,end] window, measure
how seamless the end→start loop transition is, and (optionally) nudge the
boundaries to zero-crossings + pick a crossfade length so a pad can be held
forever with no obvious seam.

numpy-only (no librosa) — the seam metrics are cheap FFT/RMS/zero-cross
comparisons, so this is unit-testable with synthetic signals.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from .graph import GridPos, LoopQuality

_EPS = 1e-9


def _seg(y: np.ndarray, sr: int, start_s: float, end_s: float) -> Tuple[int, int]:
    a = max(0, int(round(start_s * sr)))
    b = min(len(y), int(round(end_s * sr)))
    return a, b


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2) + _EPS)) if len(x) else 0.0


def _mag_spectrum(x: np.ndarray) -> np.ndarray:
    if len(x) == 0:
        return np.zeros(1)
    w = x.astype(np.float64) * np.hanning(len(x))
    return np.abs(np.fft.rfft(w))


def _cos_sim(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    a, b = a[:n], b[:n]
    d = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / (d + _EPS)) if d > 0 else 0.0


def _nearest_zero_crossing(y: np.ndarray, idx: int, search: int) -> int:
    """Index near ``idx`` (within ±search) where the signal crosses zero going
    the same direction — makes the seam amplitude ~0 on both ends."""
    lo = max(1, idx - search)
    hi = min(len(y) - 1, idx + search)
    best, bestd = idx, search + 1
    for i in range(lo, hi):
        if y[i - 1] <= 0 <= y[i] or y[i - 1] >= 0 >= y[i]:
            d = abs(i - idx)
            if d < bestd:
                best, bestd = i, d
    return best


class LoopAnalyzer:
    """Scores a grid-aligned window for loopability; optionally optimizes seam."""

    def __init__(self, frame_ms: float = 25.0):
        self.frame_ms = frame_ms

    def analyze(
        self,
        y: np.ndarray,
        sr: int,
        pos: GridPos,
        optimize: bool = True,
    ) -> LoopQuality:
        if y.ndim > 1:
            y = y.mean(axis=1)
        y = np.asarray(y, dtype=np.float64)
        frame = max(8, int(sr * self.frame_ms / 1000.0))

        a, b = _seg(y, sr, pos.start_s, pos.end_s)
        opt_start = opt_end = None
        crossfade_ms = 0.0
        if optimize and b - a > 4 * frame:
            search = frame  # nudge up to one frame to a zero-crossing
            na = _nearest_zero_crossing(y, a, search)
            nb = _nearest_zero_crossing(y, b, search)
            if nb - na > 2 * frame:
                a, b = na, nb
                opt_start, opt_end = a / sr, b / sr

        if b - a < 2 * frame:
            return LoopQuality(confidence=0.0)

        head = y[a : a + frame]
        tail = y[b - frame : b]
        # a bit of context past the loop end for phase/continuity feel
        pre_head = y[max(0, a - frame) : a]

        # --- metrics (0..1) ---
        # attack/release stability: RMS of head vs tail should match (no jump)
        rh, rt = _rms(head), _rms(tail)
        level = 1.0 - abs(rh - rt) / (max(rh, rt) + _EPS)
        attack_stability = float(np.clip(level, 0, 1))
        release_stability = attack_stability

        # tail decay: prefer the tail not louder than the head (no chopped note)
        tail_decay = float(np.clip(1.0 - max(0.0, rt - rh) / (rh + _EPS), 0, 1))

        # zero-crossing: seam amplitude near zero on both ends
        zc = 1.0 - (abs(float(head[0])) + abs(float(tail[-1]))) / (2 * (_rms(y) + _EPS))
        zero_crossing = float(np.clip(zc, 0, 1))

        # spectral continuity: end-frame spectrum ~ start-frame spectrum
        spectral_continuity = float(np.clip(_cos_sim(_mag_spectrum(tail), _mag_spectrum(head)), 0, 1))

        # harmonic continuity: low-band spectral match (pitch/harmony carries over)
        nlow = max(1, len(_mag_spectrum(head)) // 4)
        harmonic_continuity = float(
            np.clip(_cos_sim(_mag_spectrum(tail)[:nlow], _mag_spectrum(head)[:nlow]), 0, 1)
        )

        # beat alignment: grid-aligned windows already score high; whole bars best
        beat_alignment = 1.0 if pos.is_bar_aligned else (0.8 if pos.length_beats > 0 else 0.4)

        # phase continuity: waveform shape entering the loop matches the tail
        phase_continuity = float(np.clip(_cos_sim(tail, pre_head), 0, 1)) if len(pre_head) else 0.5

        # crossfade suggestion: if the raw seam is discontinuous, propose a fade
        seam_gap = abs(float(head[0]) - float(tail[-1]))
        if seam_gap > 0.02 * (_rms(y) + _EPS) + 0.001:
            crossfade_ms = min(30.0, self.frame_ms)

        confidence = float(
            np.clip(
                0.22 * spectral_continuity
                + 0.16 * harmonic_continuity
                + 0.16 * attack_stability
                + 0.12 * zero_crossing
                + 0.12 * beat_alignment
                + 0.10 * tail_decay
                + 0.06 * phase_continuity
                + 0.06 * release_stability,
                0,
                1,
            )
        )

        return LoopQuality(
            confidence=confidence,
            attack_stability=attack_stability,
            release_stability=release_stability,
            tail_decay=tail_decay,
            zero_crossing=zero_crossing,
            spectral_continuity=spectral_continuity,
            harmonic_continuity=harmonic_continuity,
            beat_alignment=beat_alignment,
            phase_continuity=phase_continuity,
            crossfade_ms=crossfade_ms,
            optimized_start_s=opt_start,
            optimized_end_s=opt_end,
        )
