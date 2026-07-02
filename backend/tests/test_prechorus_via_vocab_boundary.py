"""Round-2 Fix 4 — integration with Pass 2b (CHORUS→CHORUS PRECHORUS).

End-to-end: Fix 4's chord-vocab boundary detector splits one long
CHORUS block into a pair of adjacent sub-sections. When both children
inherit the parent's CHORUS label (as they do in the unified_pipeline
split-and-inherit logic), Pass 2b promotes the first child to
PRECHORUS via the vocab-narrowing + rising-ramp rule.

This is the shape the live probe on session 1313168e was missing:
the pre-chorus F#5 vamp sits inside the same CHORUS block as the
following full 6-chord chorus progression, so no boundary → no
PRECHORUS label. Fix 4 introduces the boundary; Pass 2b promotes.
"""
from __future__ import annotations

import numpy as np

from tone_forge.analysis.chord_vocab_boundaries import (
    detect_chord_vocab_boundaries,
)
from tone_forge.analysis.section_naming import SectionType
from tone_forge.analysis.song_form import refine_section_types
from tone_forge.analysis.song_form_aggregates import SongFormAggregates


def _section(start_s: float, end_s: float) -> dict:
    return {"start_time": start_s, "end_time": end_s, "type": "chorus"}


def _chord(start_s: float, end_s: float, symbol: str) -> dict:
    return {
        "start_s": start_s,
        "end_s": end_s,
        "symbol": symbol,
        "confidence": 0.7,
    }


def _beats(step: float = 0.5, count: int = 200) -> np.ndarray:
    return np.arange(count, dtype=np.float64) * step


def test_fix4_boundary_then_pass2b_promotes_prechorus():
    """One long CHORUS block, harmonic-vocab shift at ~20s: Fix 4
    detects the boundary, we split (simulating the pipeline's
    split-and-inherit), then Pass 2b promotes the first sub-section
    to PRECHORUS."""
    # Original section pre-split — 40s CHORUS block spanning 0-40s.
    original_sections = [_section(0.0, 40.0)]
    # Pre-chorus half: F# only (narrow vocab).
    # Chorus half: {C, G, Am, F} progression (broad vocab, superset
    # of {F#}? No — {C,G,Am,F} does NOT contain F#. Pass 2b requires
    # the pre-chorus vocab to be a PROPER SUBSET of the chorus
    # vocab. Rebuild with C, G, Am, F on both sides but pre-chorus
    # only uses {F} — subset of {C, G, Am, F}.
    chords = [
        # Pre-chorus half: F vamp only (subset of chorus vocab).
        *(_chord(t, t + 2.0, "F") for t in np.arange(0.0, 20.0, 2.0)),
        # Chorus half: full 4-chord progression.
    ]
    t = 20.0
    for _ in range(5):
        for sym in ("C", "G", "Am", "F"):
            chords.append(_chord(t, t + 1.0, sym))
            t += 1.0

    # Step 1: Fix 4 detects the boundary.
    boundaries = detect_chord_vocab_boundaries(
        original_sections, chords, _beats(step=0.5, count=100),
    )
    assert boundaries, (
        "Fix 4 should detect the F-only → {C,G,Am,F} vocab shift"
    )

    # Step 2: Simulate pipeline split-and-inherit — apply the
    # boundaries to yield 2 CHORUS sub-sections.
    split_time = boundaries[0]["time_s"]
    sections = [
        _section(0.0, split_time),
        _section(split_time, 40.0),
    ]

    # Step 3: Bucket chords into per-section lists (matches
    # unified_pipeline.py's Pass 2b input shape).
    chords_per_section: list[list[dict]] = [[], []]
    for c in chords:
        mid = 0.5 * (c["start_s"] + c["end_s"])
        if mid < split_time:
            chords_per_section[0].append(c)
        else:
            chords_per_section[1].append(c)

    # Step 4: Build the minimal SongFormAggregates the Pass 2b rule
    # needs: rising energy ramp into the chorus half. Vocals
    # activity above the INSTRUMENTAL floor so Pass 1 doesn't demote
    # both to INSTRUMENTAL before Pass 2b runs.
    aggregates = (
        SongFormAggregates(
            vocal_activity_score=0.4,
            drum_density_per_s=0.0,
            drum_density_z=0.0,
            energy_ramp_into_next=0.5,   # rising into the chorus
            energy_z=0.0,
        ),
        SongFormAggregates(
            vocal_activity_score=0.6,
            drum_density_per_s=0.0,
            drum_density_z=0.0,
            energy_ramp_into_next=0.0,
            energy_z=0.5,
        ),
    )

    stage_a_types = (SectionType.CHORUS, SectionType.CHORUS)

    refined = refine_section_types(
        stage_a_types, aggregates,
        chords_per_section=chords_per_section,
    )

    assert refined[0] is SectionType.PRECHORUS, (
        f"Pass 2b should promote the first sub-section to PRECHORUS; "
        f"got {refined[0]!r}"
    )
    assert refined[1] is SectionType.CHORUS, (
        f"second sub-section should stay CHORUS; got {refined[1]!r}"
    )
