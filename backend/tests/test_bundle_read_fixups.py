"""Read-path fixup regression tests.

Covers the write-vs-read divergence in
`tone_forge.bundle_read_fixups.apply_bundle_read_fixups`. The API's
`/api/session/{id}` endpoint runs these fixups on persisted history
records before assembling a bundle for the Jam UI. Any label drift
between raw persisted state and the fixed-up state ships straight to
the client, so it needs coverage.
"""
from __future__ import annotations

from typing import Any

from tone_forge.bundle_read_fixups import apply_bundle_read_fixups


def _chord(start_s: float, end_s: float, symbol: str) -> dict[str, Any]:
    return {
        "start_s": start_s,
        "end_s": end_s,
        "symbol": symbol,
        "confidence": 0.7,
    }


def _section(start: float, end: float, *, type_: str, role: str) -> dict[str, Any]:
    """Build a persisted-shape section row.

    Persisted history uses ``start_time`` / ``end_time`` (not
    ``start_s`` / ``end_s``); the fixups run over the persisted shape,
    not the bundle-serialiser shape.
    """
    return {
        "start_time": start,
        "end_time": end,
        "duration": end - start,
        "type": type_,
        "structural_role": role,
        "structural_confidence": 1.0,
    }


def test_fix4_subdivision_relabels_subsections_from_fresh_h2():
    """After Fix 4 subdivides on a vocab shift, both sub-sections
    must carry labels derived from the new-boundary H2 vector,
    not stale copies of the parent's label.

    Setup: one CHORUS-labelled section from 0-40s.
      * [0-20s]: 8 distinct chords, all unique to the song, so
        every trigram appears exactly once → H2 = 0.0
        (genuine UNIQUE, not insufficient-data abstain).
      * [20-40s]: repeated {C,G,Am,F} progression → H2 = 1.0
        → ANCHOR.
    Under the old read-path (no relabel after Fix 4) the whole
    span keeps the parent CHORUS/ANCHOR. Under the fix, the
    first sub-section flips to UNIQUE/bridge.

    Chord density in the first half must be high enough that
    Fix 4's sub-sections still hold >= n_used symbols each; the
    subdivider carves the parent into ~10-second slices, so a
    dense per-slice chord count keeps the H2 abstain path out
    of this test and pins the assertion on the genuine-UNIQUE
    branch.
    """
    result = {
        # Persisted history stores boundaries under start_time/end_time.
        "sections": [_section(0.0, 40.0, type_="chorus", role="ANCHOR")],
        "chords": [
            # Eight distinct chords once each in the first half —
            # no intra-section trigram repeats and no overlap with
            # the second half. Dense enough that Fix 4's ~10s
            # sub-slices each still hold >= 3 symbols.
            _chord(0.0, 2.5, "Bb"),
            _chord(2.5, 5.0, "D#"),
            _chord(5.0, 7.5, "F#"),
            _chord(7.5, 10.0, "G#"),
            _chord(10.0, 12.5, "Ab"),
            _chord(12.5, 15.0, "Db"),
            _chord(15.0, 17.5, "Eb"),
            _chord(17.5, 20.0, "B"),
            # {C,G,Am,F} progression for the second half.
            _chord(20.0, 22.5, "C"),
            _chord(22.5, 25.0, "G"),
            _chord(25.0, 27.5, "Am"),
            _chord(27.5, 30.0, "F"),
            _chord(30.0, 32.5, "C"),
            _chord(32.5, 35.0, "G"),
            _chord(35.0, 37.5, "Am"),
            _chord(37.5, 40.0, "F"),
        ],
        # Beats every 0.5s across 40s covers the whole span.
        "beats_s": [i * 0.5 for i in range(80)],
    }

    apply_bundle_read_fixups(result)

    sections = result["sections"]
    # Fix 4 must have subdivided the single input into >= 2 sub-sections
    # at the disjoint-vocab seam near 20s.
    assert len(sections) >= 2, (
        f"Fix 4 should have subdivided at the disjoint-vocab seam; "
        f"got {len(sections)} section(s): "
        f"{[(s['start_time'], s['end_time'], s.get('type')) for s in sections]}"
    )

    # Sub-section covering the first-half distinct-chord region.
    first_half = next(
        (s for s in sections if float(s["start_time"]) < 5.0), None
    )
    assert first_half is not None, (
        "expected a sub-section covering the distinct-chord region"
    )
    # First-half trigrams appear nowhere else → H2 = 0.0 → UNIQUE.
    # The key assertion is that the label is NOT the stale 'chorus'
    # / 'ANCHOR' inherited from the parent (which is what happens
    # without the relabel-after-Fix-4 call).
    assert first_half.get("structural_role") == "UNIQUE", (
        f"First half should be UNIQUE after Fix 4 subdivision + "
        f"relabel; got role={first_half.get('structural_role')!r}, "
        f"type={first_half.get('type')!r}"
    )
    assert first_half.get("type") != "chorus", (
        f"First half should not retain the stale parent 'chorus' "
        f"label after Fix 4 subdivision; got type="
        f"{first_half.get('type')!r}"
    )


def test_apply_bundle_read_fixups_is_noop_when_result_has_no_sections():
    """Guard: empty / missing sections must not crash the fixup chain."""
    for result in ({}, {"sections": None}, {"sections": []}):
        apply_bundle_read_fixups(result)  # must not raise


def test_apply_bundle_read_fixups_leaves_labels_alone_without_chords():
    """Without chords the H2 relabel path can't run — the stored
    labels survive. This pins the failure mode as 'abstain' rather
    than 'wipe labels'.
    """
    result = {
        "sections": [_section(0.0, 20.0, type_="verse", role="DEVELOPMENT")],
        # No 'chords' key at all.
    }
    apply_bundle_read_fixups(result)
    assert result["sections"][0]["type"] == "verse"
    assert result["sections"][0]["structural_role"] == "DEVELOPMENT"


def test_relabel_after_fix4_uses_insufficient_flag_for_no_chord_sections():
    """Section with zero chords in its span must not land as BRIDGE.

    Setup: two sections, both persisted as CHORUS/ANCHOR (stale
    inherited labels).
      * Section 0 (0-30s): 8 chords over a repeating {C,G,Am,F}
        progression → H2 = 1.0 → ANCHOR (recognised chorus).
      * Section 1 (30-60s): **zero chords** — the Paramore-shaped
        instrumental gap. Before the fix the classifier's hard
        floor collapses ``h == 0.0`` to UNIQUE, and Stage A maps
        UNIQUE → BRIDGE.

    Under the fix, the H2 extractor emits ``insufficient=True`` for
    section 1 and the classifier routes it to DEVELOPMENT with low
    confidence. Stage A then maps DEVELOPMENT → VERSE, not BRIDGE.
    """
    result = {
        "sections": [
            _section(0.0, 30.0, type_="chorus", role="ANCHOR"),
            _section(30.0, 60.0, type_="chorus", role="ANCHOR"),
        ],
        "chords": [
            _chord(0.0, 3.75, "C"),
            _chord(3.75, 7.5, "G"),
            _chord(7.5, 11.25, "Am"),
            _chord(11.25, 15.0, "F"),
            _chord(15.0, 18.75, "C"),
            _chord(18.75, 22.5, "G"),
            _chord(22.5, 26.25, "Am"),
            _chord(26.25, 30.0, "F"),
            # Section 1: intentionally empty — instrumental gap.
        ],
        "beats_s": [i * 0.5 for i in range(120)],
    }

    apply_bundle_read_fixups(result)

    # Section 1 must NOT retain the stale 'chorus' / ANCHOR labels
    # (relabel ran) and must NOT flip to bridge/UNIQUE (the abstain
    # path routed it to DEVELOPMENT instead of UNIQUE).
    second = next(
        (s for s in result["sections"] if float(s["start_time"]) >= 30.0),
        None,
    )
    assert second is not None, "expected a section covering 30-60s"
    assert second.get("structural_role") != "UNIQUE", (
        f"insufficient-data section should abstain to DEVELOPMENT, "
        f"got role={second.get('structural_role')!r}"
    )
    assert second.get("type") != "bridge", (
        f"insufficient-data section should not land as bridge; "
        f"got type={second.get('type')!r}"
    )
