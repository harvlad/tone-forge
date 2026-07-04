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

from tone_forge.bundle_read_fixups import (
    apply_bundle_read_fixups,
    relabel_sections_from_h2,
)


def _debug_row(
    stem: str,
    *,
    lead: float = 0.0,
    voiced: float = 0.0,
    notes: int = 0,
    duration: float = 30.0,
    pitch_median: float | None = None,
    pitch_range: float | None = None,
) -> dict[str, Any]:
    """Minimal asdict-shaped SectionFeatures row for a single stem.

    Matches the fields ``song_form_aggregates`` reads: ``stem_name``,
    ``lead_activity_score``, ``voiced_frame_ratio``, ``note_count``,
    ``duration_s``, plus the optional pitch fields. Additional fields
    on the real dataclass are ignored by the aggregator.
    """
    return {
        "stem_name": stem,
        "lead_activity_score": lead,
        "voiced_frame_ratio": voiced,
        "note_count": notes,
        "duration_s": duration,
        "pitch_median_semitones": pitch_median,
        "pitch_range_semitones": pitch_range,
    }


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


# ---------------------------------------------------------------------
# Plan D — Stage B rerun via persisted per-section debug_features
# ---------------------------------------------------------------------


def _repeating_cgamf_chords(t0: float, t1: float) -> list[dict[str, Any]]:
    """{C,G,Am,F} progression across ``[t0, t1)`` at 3.75s per chord.

    Eight chords fit in a 30s span → H2 = 1.0 → ANCHOR → Stage A
    → CHORUS. Shared across every Plan D test that wants a
    Stage A CHORUS to demote via Stage B evidence.
    """
    step = (t1 - t0) / 8.0
    chords = []
    for i, sym in enumerate(["C", "G", "Am", "F"] * 2):
        s = t0 + i * step
        chords.append(_chord(s, s + step, sym))
    return chords


def test_relabel_runs_stage_b_when_debug_features_present():
    """Stage B fires when every section carries debug_features.

    Setup:
      * Two sections, both {C,G,Am,F} → Stage A picks CHORUS.
      * Section 0 has high vocal activity (real chorus).
      * Section 1 has effectively zero vocal activity → Pass 1
        must demote it to INSTRUMENTAL.

    Without Plan D the helper wipes the persisted labels and both
    stay chorus. With Plan D the second section flips.
    """
    sections = [
        {
            "start_time": 0.0,
            "end_time": 30.0,
            "duration": 30.0,
            "type": "chorus",
            "structural_role": "ANCHOR",
            "structural_confidence": 1.0,
            "energy_mean": 0.5,
            "debug_features": [
                _debug_row("vocals", lead=0.8, voiced=0.7, notes=40),
                _debug_row("drums", notes=60, duration=30.0),
                _debug_row("bass", notes=30, duration=30.0),
                _debug_row("other", notes=20, duration=30.0),
            ],
        },
        {
            "start_time": 30.0,
            "end_time": 60.0,
            "duration": 30.0,
            "type": "chorus",
            "structural_role": "ANCHOR",
            "structural_confidence": 1.0,
            "energy_mean": 0.5,
            "debug_features": [
                # vocal_activity_score = 0.001 * 0.001 = 1e-6 —
                # comfortably below the 0.15 ceiling.
                _debug_row("vocals", lead=0.001, voiced=0.001, notes=0),
                _debug_row("drums", notes=60, duration=30.0),
                _debug_row("bass", notes=30, duration=30.0),
                _debug_row("other", notes=20, duration=30.0),
            ],
        },
    ]
    chords = _repeating_cgamf_chords(0.0, 30.0) + _repeating_cgamf_chords(
        30.0, 60.0
    )

    relabel_sections_from_h2(sections, chords)

    # Section 0 is the high-vocals CHORUS.
    assert sections[0]["type"] == "chorus", (
        f"section 0 should stay CHORUS; got {sections[0]['type']!r}"
    )
    # Section 1 has vocals ≈ 0 → Pass 1 demotes to INSTRUMENTAL.
    assert sections[1]["type"] == "instrumental", (
        f"section 1 should flip to INSTRUMENTAL via Stage B Pass 1; "
        f"got {sections[1]['type']!r}"
    )


def test_relabel_abstains_stage_b_when_debug_features_missing():
    """Legacy bundles (no debug_features) fall through to Stage A only.

    Same shape as the Stage B test but with debug_features stripped
    from section 1. Stage B abstains → both sections keep Stage A's
    CHORUS labelling.
    """
    sections = [
        {
            "start_time": 0.0,
            "end_time": 30.0,
            "duration": 30.0,
            "type": "chorus",
            "structural_role": "ANCHOR",
            "structural_confidence": 1.0,
            "energy_mean": 0.5,
            "debug_features": [
                _debug_row("vocals", lead=0.8, voiced=0.7, notes=40),
                _debug_row("drums", notes=60, duration=30.0),
            ],
        },
        {
            "start_time": 30.0,
            "end_time": 60.0,
            "duration": 30.0,
            "type": "chorus",
            "structural_role": "ANCHOR",
            "structural_confidence": 1.0,
            "energy_mean": 0.5,
            # No debug_features key → Stage B must abstain.
        },
    ]
    chords = _repeating_cgamf_chords(0.0, 30.0) + _repeating_cgamf_chords(
        30.0, 60.0
    )

    relabel_sections_from_h2(sections, chords)

    # Both sections retain the Stage A CHORUS mapping; Stage B never
    # ran because the second section lacked evidence.
    assert sections[0]["type"] == "chorus"
    assert sections[1]["type"] == "chorus"


def test_apply_bundle_read_fixups_preserves_bridge_on_non_split_section():
    """Plan E: an untouched BRIDGE-labelled section must survive the
    read-path relabel even when a *neighbouring* section gets split.

    Setup: two sections, both {C,G,Am,F} shared progression.
      * Section 0 (0-40s): a real chorus, chord-density high enough
        that Fix 4's Jaccard boundary detector splits it (the
        `test_fix4_subdivision_relabels_subsections_from_fresh_h2`
        test above pins the same trigger).
      * Section 1 (40-80s): persisted as BRIDGE / DEVELOPMENT.
        Not split. Under the pre-Plan-E behaviour the relabel that
        fires after Fix 4 flips section 1's BRIDGE to CHORUS
        because a fresh H2 over the enlarged section list sees
        ANCHOR everywhere. Under Plan E, only sub-sections carry
        ``_from_split``; section 1 is untouched, so its persisted
        BRIDGE label survives.

    Also asserts ``_from_split`` doesn't leak to the API bundle —
    it's an internal tag stripped at the end of
    ``apply_bundle_read_fixups``.
    """
    # Section 1 is < max_bridge_s (30.0) so Fix B doesn't flag it and
    # Fix C leaves it untouched. Section 1's chord content is a
    # single sustained symbol so Fix 4's Jaccard boundary detector
    # sees zero vocab shift and can't produce a split candidate.
    # Both guarantees keep the section un-tagged, isolating Plan E's
    # preserve-vs-overwrite gate as the sole variable under test.
    result = {
        "sections": [
            _section(0.0, 40.0, type_="chorus", role="ANCHOR"),
            # Persisted as BRIDGE — this is the label under test.
            _section(40.0, 65.0, type_="bridge", role="UNIQUE"),
        ],
        "chords": [
            # Section 0: same disjoint-vocab shape as the Fix 4
            # trigger test above — distinct chords in the first
            # half, {C,G,Am,F} repetitions in the second half.
            _chord(0.0, 2.5, "Bb"),
            _chord(2.5, 5.0, "D#"),
            _chord(5.0, 7.5, "F#"),
            _chord(7.5, 10.0, "G#"),
            _chord(10.0, 12.5, "Ab"),
            _chord(12.5, 15.0, "Db"),
            _chord(15.0, 17.5, "Eb"),
            _chord(17.5, 20.0, "B"),
            _chord(20.0, 22.5, "C"),
            _chord(22.5, 25.0, "G"),
            _chord(25.0, 27.5, "Am"),
            _chord(27.5, 30.0, "F"),
            _chord(30.0, 32.5, "C"),
            _chord(32.5, 35.0, "G"),
            _chord(35.0, 37.5, "Am"),
            _chord(37.5, 40.0, "F"),
            # Section 1: single sustained C — no vocab shift possible.
            _chord(40.0, 65.0, "C"),
        ],
        "beats_s": [i * 0.5 for i in range(130)],
    }

    apply_bundle_read_fixups(result)

    sections = result["sections"]
    # Fix 4 must have subdivided section 0 (pins the split trigger).
    n_from_section_0 = sum(
        1 for s in sections if float(s["start_time"]) < 40.0
    )
    assert n_from_section_0 >= 2, (
        f"Fix 4 should have split section 0; got {n_from_section_0} "
        f"section(s) covering [0, 40)."
    )

    # Section 1 is untouched: still starts at 40.0.
    section_1 = next(
        (s for s in sections if abs(float(s["start_time"]) - 40.0) < 0.001),
        None,
    )
    assert section_1 is not None, "expected the [40, 80) section intact"

    # Plan E assertion: the untouched BRIDGE label survives.
    assert section_1.get("type") == "bridge", (
        f"non-split section should preserve write-time BRIDGE label; "
        f"got type={section_1.get('type')!r}, role="
        f"{section_1.get('structural_role')!r}"
    )
    assert section_1.get("structural_role") == "UNIQUE"

    # Internal tag doesn't leak past the fixup boundary.
    for s in sections:
        assert "_from_split" not in s, (
            f"_from_split tag should be stripped before returning; "
            f"found on section start={s.get('start_time')}"
        )


def test_relabel_refines_untagged_chorus_via_stage_b_even_when_tags_present():
    """Plan E asymmetry: an untagged CHORUS section is still eligible
    for read-time Stage B refinement, even when other sections carry
    ``_from_split``.

    Setup: three sections.
      * Section 0: tagged ``_from_split`` (a sub-section from an
        upstream split), high vocals.
      * Section 1: untagged, persisted CHORUS, vanishingly low
        vocals → Stage B Pass 1 must flip to INSTRUMENTAL.
      * Section 2: untagged, persisted BRIDGE — preserved (covered
        by the neighboring-split test above; asserted here to
        confirm the asymmetry rather than a blanket overwrite).

    Pins the design point that Plan E's preservation gate is
    ontology-aware rather than binary: BRIDGE is a write-time-only
    signal (H2 must have said UNIQUE), CHORUS is a Stage A default
    that read-time Stage B can refine given fresh evidence.
    """
    sections = [
        {
            "start_time": 0.0,
            "end_time": 30.0,
            "duration": 30.0,
            "type": "chorus",
            "structural_role": "ANCHOR",
            "structural_confidence": 1.0,
            "energy_mean": 0.5,
            "_from_split": True,
            "debug_features": [
                _debug_row("vocals", lead=0.8, voiced=0.7, notes=40),
                _debug_row("drums", notes=60, duration=30.0),
                _debug_row("bass", notes=30, duration=30.0),
                _debug_row("other", notes=20, duration=30.0),
            ],
        },
        {
            "start_time": 30.0,
            "end_time": 60.0,
            "duration": 30.0,
            "type": "chorus",
            "structural_role": "ANCHOR",
            "structural_confidence": 1.0,
            "energy_mean": 0.5,
            # No _from_split — non-split original that Plan E leaves
            # eligible for Stage B refinement because type == chorus.
            "debug_features": [
                _debug_row("vocals", lead=0.001, voiced=0.001, notes=0),
                _debug_row("drums", notes=60, duration=30.0),
                _debug_row("bass", notes=30, duration=30.0),
                _debug_row("other", notes=20, duration=30.0),
            ],
        },
        {
            "start_time": 60.0,
            "end_time": 85.0,
            "duration": 25.0,
            "type": "bridge",
            "structural_role": "UNIQUE",
            "structural_confidence": 1.0,
            "energy_mean": 0.5,
            # No _from_split, non-CHORUS type → preserved.
            "debug_features": [
                _debug_row("vocals", lead=0.5, voiced=0.5, notes=20),
                _debug_row("drums", notes=60, duration=25.0),
                _debug_row("bass", notes=30, duration=25.0),
                _debug_row("other", notes=20, duration=25.0),
            ],
        },
    ]
    chords = (
        _repeating_cgamf_chords(0.0, 30.0)
        + _repeating_cgamf_chords(30.0, 60.0)
        + [_chord(60.0, 85.0, "C")]
    )

    relabel_sections_from_h2(sections, chords)

    assert sections[0]["type"] == "chorus", "tagged high-vocal chorus stays chorus"
    assert sections[1]["type"] == "instrumental", (
        f"untagged CHORUS with low vocals must still be refined by "
        f"Stage B; got {sections[1]['type']!r}"
    )
    assert sections[2]["type"] == "bridge", (
        f"untagged BRIDGE must be preserved (non-CHORUS write-time "
        f"labels are write-time-only signals); got {sections[2]['type']!r}"
    )


def test_relabel_abstains_stage_b_when_stem_name_missing():
    """Malformed debug_features (row without ``stem_name``) → abstain.

    Guards the aggregator against a row that would land in the empty-
    key bucket and misalign the per-stem sequence lengths.
    """
    bad_row = _debug_row("vocals", lead=0.001, voiced=0.001)
    bad_row.pop("stem_name")

    sections = [
        {
            "start_time": 0.0,
            "end_time": 30.0,
            "duration": 30.0,
            "type": "chorus",
            "structural_role": "ANCHOR",
            "structural_confidence": 1.0,
            "energy_mean": 0.5,
            "debug_features": [bad_row],
        },
    ]
    chords = _repeating_cgamf_chords(0.0, 30.0)

    relabel_sections_from_h2(sections, chords)

    # Stage B abstained; Stage A's CHORUS survives even though
    # vocals would have been low enough to demote had the row been
    # well-formed.
    assert sections[0]["type"] == "chorus"
