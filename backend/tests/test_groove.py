"""Groove template (performance.groove) — the micro-timing extraction:
known injected deviations must come back out, normalized late-only."""
from __future__ import annotations

from tone_forge.performance.groove import build_groove
from tone_forge.performance.drum_kit import DRUM_HITS_RESULT_KEY, HITS_VERSION

BAR = 2.0
STEP = BAR / 16


def _result(slot_offsets, n_bars=8):
    """Hats on every even slot, each slot's hits shifted by
    slot_offsets[slot] (in step fractions)."""
    downs = [1.0 + i * BAR for i in range(n_bars + 1)]
    hits = []
    for b in range(n_bars):
        t0 = downs[b]
        for slot in range(0, 16, 2):
            t = t0 + slot * STEP + slot_offsets.get(slot, 0.0) * STEP
            hits.append({"t": round(t, 5), "end": round(t + 0.1, 5),
                         "cls": "hat_closed", "strength": 0.5,
                         "isolation": 1.0})
    return {DRUM_HITS_RESULT_KEY: {"version": HITS_VERSION, "hits": hits},
            "downbeats_s": downs}


def test_recovers_injected_shuffle():
    # Off-beat 8ths (slots 2, 6, 10, 14) played 0.3 steps late = shuffle.
    r = _result({2: 0.3, 6: 0.3, 10: 0.3, 14: 0.3})
    g = build_groove(r)
    offs = g["offsetsSteps"]
    for slot in (2, 6, 10, 14):
        assert 0.25 < offs[slot] <= 0.35, (slot, offs[slot])
    for slot in (0, 4, 8, 12):
        assert offs[slot] < 0.05


def test_late_only_normalization():
    # Everything EARLY by 0.2 except slot 0 on grid → after normalization
    # slot 0 carries the delay and the early slots sit at ~0. Relative
    # feel preserved, no negative offsets on the wire.
    r = _result({s: -0.2 for s in range(2, 16, 2)})
    g = build_groove(r)
    offs = g["offsetsSteps"]
    assert min(offs) >= 0.0
    assert offs[0] > 0.15               # grid slot now reads "late"
    assert all(offs[s] < 0.05 for s in range(2, 16, 2))


def test_quantized_source_yields_zero_template():
    g = build_groove(_result({}))
    assert g is not None
    assert all(o == 0.0 for o in g["offsetsSteps"])


def test_missing_prereqs():
    assert build_groove({}) is None
    r = _result({})
    r.pop("downbeats_s")
    assert build_groove(r) is None
