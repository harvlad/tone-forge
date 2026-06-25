"""Integration test for the full H2 → role → SectionType pipeline.

Exercises the chain that lives in
``unified_pipeline.py`` and ``local_engine/analysis_worker.py``:

    extract_h2(bundle)  →  classify_roles(...)  →  derive_section_types(...)

Uses synthetic chord+section bundles. No audio, no MIDI.
"""

from __future__ import annotations

from typing import Any

from tone_forge.analysis.section_naming import derive_section_types
from tone_forge.analysis.sections import SectionType
from tone_forge.song_form.h2 import extract_h2
from tone_forge.song_form.role_classifier import classify_roles


def _chord(start_s: float, end_s: float, symbol: str) -> dict[str, Any]:
    return {"start_s": start_s, "end_s": end_s, "symbol": symbol}


def _section(start_s: float, end_s: float, name: str = "") -> dict[str, Any]:
    s: dict[str, Any] = {"start_s": start_s, "end_s": end_s}
    if name:
        s["name"] = name
    return s


def test_pop_arrangement_full_chain_intro_chorus_bridge_chorus_outro():
    # 5-section pop arrangement with one recurring chorus:
    #   s0 (intro): [G, D, A]            — unique
    #   s1 (chorus): [C, F, G, Am]       — chord seq X
    #   s2 (bridge): [Am, F, C, G]       — unique
    #   s3 (chorus): [C, F, G, Am]       — chord seq X (recurs)
    #   s4 (outro): [Em, Bm, A]          — unique
    #
    # Trigrams (n=3, full_seq length ≥ 6):
    #   s1 (C,F,G) and (F,G,Am) recur in s3   → s1, s3 H2 = 1.0 → ANCHOR
    #   s0, s2, s4 trigrams don't recur       → H2 ≈ 0.0      → UNIQUE
    #
    # Expected derived types:
    #   INTRO, CHORUS, BRIDGE, CHORUS, OUTRO
    chords = [
        # s0: G D A
        _chord(0.0, 1.0, "G"),
        _chord(1.0, 2.0, "D"),
        _chord(2.0, 3.0, "A"),
        # s1: C F G Am
        _chord(3.0, 4.0, "C"),
        _chord(4.0, 5.0, "F"),
        _chord(5.0, 6.0, "G"),
        _chord(6.0, 7.0, "Am"),
        # s2: Am F C G
        _chord(7.0, 8.0, "Am"),
        _chord(8.0, 9.0, "F"),
        _chord(9.0, 10.0, "C"),
        _chord(10.0, 11.0, "G"),
        # s3: C F G Am
        _chord(11.0, 12.0, "C"),
        _chord(12.0, 13.0, "F"),
        _chord(13.0, 14.0, "G"),
        _chord(14.0, 15.0, "Am"),
        # s4: Em Bm A
        _chord(15.0, 16.0, "Em"),
        _chord(16.0, 17.0, "Bm"),
        _chord(17.0, 18.0, "A"),
    ]
    sections = [
        _section(0.0, 3.0, "s0"),
        _section(3.0, 7.0, "s1"),
        _section(7.0, 11.0, "s2"),
        _section(11.0, 15.0, "s3"),
        _section(15.0, 18.0, "s4"),
    ]
    bundle = {"chords": chords, "sections": sections}

    h2 = extract_h2(bundle)
    assert not h2.degenerate
    assert len(h2.per_section) == 5

    decisions = classify_roles(h2.per_section, h2.h2_sep)
    derived = derive_section_types(decisions)

    assert derived == (
        SectionType.INTRO,
        SectionType.CHORUS,
        SectionType.BRIDGE,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )


def test_through_composed_all_unique_yields_intro_bridges_outro():
    # 4-section through-composed arrangement: every section has its own
    # chord progression with no shared trigrams. Each section is UNIQUE.
    # Expected derived types: INTRO, BRIDGE, BRIDGE, OUTRO.
    chords = [
        # s0: distinct
        _chord(0.0, 1.0, "C"),
        _chord(1.0, 2.0, "D"),
        _chord(2.0, 3.0, "E"),
        # s1: distinct
        _chord(3.0, 4.0, "F"),
        _chord(4.0, 5.0, "G"),
        _chord(5.0, 6.0, "A"),
        # s2: distinct
        _chord(6.0, 7.0, "B"),
        _chord(7.0, 8.0, "C#"),
        _chord(8.0, 9.0, "D#"),
        # s3: distinct
        _chord(9.0, 10.0, "F#"),
        _chord(10.0, 11.0, "G#"),
        _chord(11.0, 12.0, "A#"),
    ]
    sections = [
        _section(0.0, 3.0, "s0"),
        _section(3.0, 6.0, "s1"),
        _section(6.0, 9.0, "s2"),
        _section(9.0, 12.0, "s3"),
    ]
    bundle = {"chords": chords, "sections": sections}

    h2 = extract_h2(bundle)
    assert not h2.degenerate
    assert len(h2.per_section) == 4

    decisions = classify_roles(h2.per_section, h2.h2_sep)
    derived = derive_section_types(decisions)

    assert derived == (
        SectionType.INTRO,
        SectionType.BRIDGE,
        SectionType.BRIDGE,
        SectionType.OUTRO,
    )


def test_development_role_maps_to_verse():
    # Bypass extract_h2 and feed classify_roles a hand-crafted H2 vector
    # that produces a mix including DEVELOPMENT — covers the V (verse)
    # branch of the type derivation, which is hard to construct via
    # extract_h2 because we'd need partial-recurrence engineering.
    #
    # role_classifier thresholds (defaults):
    #   anchor_floor=0.66  →  h>=0.66 → ANCHOR
    #   unique_ceiling=0.20 → 0 < h < 0.20 → UNIQUE
    #   in-between (0.20 ≤ h < 0.66) → DEVELOPMENT
    #
    # H2 vector [0.05, 0.45, 0.95, 0.45, 0.05] with h2_sep above
    # uniform_floor (0.25) puts:
    #   s0 → UNIQUE      → INTRO  (first)
    #   s1 → DEVELOPMENT → VERSE
    #   s2 → ANCHOR      → CHORUS
    #   s3 → DEVELOPMENT → VERSE
    #   s4 → UNIQUE      → OUTRO  (last)
    h2_vec = (0.05, 0.45, 0.95, 0.45, 0.05)
    h2_sep = 0.5  # above uniform_floor — no escape mode

    decisions = classify_roles(h2_vec, h2_sep)
    roles = tuple(d.role for d in decisions)
    assert roles == ("UNIQUE", "DEVELOPMENT", "ANCHOR", "DEVELOPMENT", "UNIQUE")

    derived = derive_section_types(decisions)
    assert derived == (
        SectionType.INTRO,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.VERSE,
        SectionType.OUTRO,
    )
