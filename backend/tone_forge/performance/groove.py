"""Groove transfer — the song's micro-timing as a reusable feel.

The hits table stores where the drummer ACTUALLY played; the beat grid
stores where the math says they should have. The signed difference,
histogrammed onto a 16-slot bar grid, is the song's groove fingerprint —
the laid-back snare, the rushed hats, the shuffle nobody programmed.
Applied to a step sequencer it makes a rigid pattern swing like the record.

Wire template: 16 per-slot offsets in STEP FRACTIONS (client multiplies by
its own step duration, so the feel survives tempo changes). Offsets are
normalized so the earliest slot sits at 0 — the client clock can only
delay steps (its swing mechanism holds a step past its grid boundary; it
cannot fire early), so the whole groove shifts to non-negative delays,
which preserves every RELATIVE relationship, and relative is all feel is.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .drum_kit import DRUM_HITS_RESULT_KEY

logger = logging.getLogger(__name__)

GROOVE_VERSION = 1

# A slot needs this many hits across the song before its median means
# anything; sparse slots inherit 0 (grid) rather than one hit's noise.
_MIN_SLOT_HITS = 4
# Clamp per-slot delay: 0.45 steps is already a hard shuffle; anything
# larger is a mis-binned hit, not feel.
_MAX_OFFSET_STEPS = 0.45


def build_groove(result: Dict) -> Optional[Dict]:
    """Groove template from the persisted hits table + downbeat grid.
    None when either prerequisite is missing (caller maps to 422)."""
    table = result.get(DRUM_HITS_RESULT_KEY)
    hits = table.get("hits") if isinstance(table, dict) else None
    downs = [float(d) for d in (result.get("downbeats_s") or [])
             if isinstance(d, (int, float))]
    if not hits or len(downs) < 3:
        return None

    # Per-slot signed deviations, in step fractions. All classes pool into
    # one timeline — the groove is the ensemble feel, and hat-heavy slots
    # dominating is correct (hats carry the subdivision).
    devs: List[List[float]] = [[] for _ in range(16)]
    n_bars = len(downs) - 1
    for h in hits:
        t = float(h["t"])
        for i in range(n_bars):
            a, b = downs[i], downs[i + 1]
            if a <= t < b and b > a:
                pos = (t - a) / (b - a) * 16.0
                slot = int(pos + 0.5) % 16
                frac = pos - round(pos)  # signed, in steps (±0.5)
                devs[slot].append(frac)
                break

    import statistics

    raw = [statistics.median(d) if len(d) >= _MIN_SLOT_HITS else 0.0
           for d in devs]
    counts = [len(d) for d in devs]
    if not any(counts[i] >= _MIN_SLOT_HITS for i in range(16)):
        return None

    # Late-only normalization (see module docstring), then clamp.
    base = min(raw)
    offsets = [round(min(max(r - base, 0.0), _MAX_OFFSET_STEPS), 4)
               for r in raw]
    if all(o < 0.01 for o in offsets):
        # Perfectly quantized source (or grid-locked EDM): an all-zero
        # template is honest — the client toggle just does nothing audible.
        offsets = [0.0] * 16

    return {
        "version": GROOVE_VERSION,
        "offsetsSteps": offsets,
        "slotHitCounts": counts,
        "bars": n_bars,
    }
