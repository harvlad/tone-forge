"""Integration test for the full H2 → role → Stage A → Stage B pipeline.

Exercises the chain that lives in ``unified_pipeline.py`` and
``local_engine/analysis_worker.py``:

    extract_h2(bundle)
        →  classify_roles(...)
        →  derive_section_types(...)             [Stage A]
        →  aggregate_song_form(per_stem, energy) [Stage B prep]
        →  refine_section_types(...)             [Stage B]

Uses synthetic chord+section bundles and hand-crafted per-stem
``SectionFeatures``-shaped dicts. No audio, no MIDI.
"""

from __future__ import annotations

from typing import Any

from tone_forge.analysis.section_naming import derive_section_types
from tone_forge.analysis.sections import SectionType
from tone_forge.analysis.song_form import refine_section_types
from tone_forge.analysis.song_form_aggregates import aggregate_song_form
from tone_forge.song_form.h2 import extract_h2
from tone_forge.song_form.role_classifier import classify_roles


def _chord(start_s: float, end_s: float, symbol: str) -> dict[str, Any]:
    return {"start_s": start_s, "end_s": end_s, "symbol": symbol}


def _section(start_s: float, end_s: float, name: str = "") -> dict[str, Any]:
    s: dict[str, Any] = {"start_s": start_s, "end_s": end_s}
    if name:
        s["name"] = name
    return s


def _vocals_row(
    *, lead_activity_score: float, voiced_frame_ratio: float
) -> dict[str, float]:
    """Per-section vocal stem feature row (dict-shaped duck type)."""
    return {
        "lead_activity_score": lead_activity_score,
        "voiced_frame_ratio": voiced_frame_ratio,
        "note_count": 0.0,
        "duration_s": 4.0,
    }


def _drums_row(*, note_count: float, duration_s: float = 4.0) -> dict[str, float]:
    """Per-section drum stem feature row (dict-shaped duck type)."""
    return {
        "lead_activity_score": 0.0,
        "voiced_frame_ratio": 0.0,
        "note_count": note_count,
        "duration_s": duration_s,
    }


def _pop_bundle() -> dict[str, Any]:
    """5-section pop arrangement: INTRO / CHORUS / BRIDGE / CHORUS / OUTRO.

    s1 and s3 share the [C, F, G, Am] trigram → ANCHOR.
    s0, s2, s4 are UNIQUE.
    """
    chords = [
        # s0: G D A — unique
        _chord(0.0, 1.0, "G"),
        _chord(1.0, 2.0, "D"),
        _chord(2.0, 3.0, "A"),
        # s1: C F G Am — chord seq X
        _chord(3.0, 4.0, "C"),
        _chord(4.0, 5.0, "F"),
        _chord(5.0, 6.0, "G"),
        _chord(6.0, 7.0, "Am"),
        # s2: Am F C G — unique
        _chord(7.0, 8.0, "Am"),
        _chord(8.0, 9.0, "F"),
        _chord(9.0, 10.0, "C"),
        _chord(10.0, 11.0, "G"),
        # s3: C F G Am — chord seq X (recurs)
        _chord(11.0, 12.0, "C"),
        _chord(12.0, 13.0, "F"),
        _chord(13.0, 14.0, "G"),
        _chord(14.0, 15.0, "Am"),
        # s4: Em Bm A — unique
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
    return {"chords": chords, "sections": sections}


def _stage_a_for_pop_bundle() -> tuple[SectionType, ...]:
    """Run extract_h2 → classify_roles → derive_section_types
    on the pop bundle, returning Stage A labels."""
    bundle = _pop_bundle()
    h2 = extract_h2(bundle)
    assert not h2.degenerate
    decisions = classify_roles(h2.per_section, h2.h2_sep)
    return derive_section_types(decisions)


def test_stage_b_pop_bundle_no_refinement_with_uniform_signals():
    """Stage A produces the canonical pop labels; Stage B with
    uniform per-stem signals should leave them untouched."""
    stage_a = _stage_a_for_pop_bundle()
    assert stage_a == (
        SectionType.INTRO,
        SectionType.CHORUS,
        SectionType.BRIDGE,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )
    per_stem = {
        "vocals": [
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),
        ] * 5,
        "drums": [_drums_row(note_count=16.0)] * 5,
    }
    energy_means = [0.5, 0.6, 0.5, 0.6, 0.5]
    aggregates = aggregate_song_form(per_stem, energy_means)
    refined = refine_section_types(stage_a, aggregates)
    assert refined == stage_a


def test_stage_b_pop_bundle_instrumental_chorus_via_low_vocals():
    """A CHORUS with no vocal activity becomes INSTRUMENTAL."""
    stage_a = _stage_a_for_pop_bundle()
    per_stem = {
        "vocals": [
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),  # s0
            _vocals_row(lead_activity_score=0.02, voiced_frame_ratio=0.05),  # s1
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),  # s2
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),  # s3
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),  # s4
        ],
        "drums": [_drums_row(note_count=16.0)] * 5,
    }
    energy_means = [0.5, 0.6, 0.5, 0.6, 0.5]
    aggregates = aggregate_song_form(per_stem, energy_means)
    refined = refine_section_types(stage_a, aggregates)
    assert refined[1] is SectionType.INSTRUMENTAL
    assert refined[3] is SectionType.CHORUS


def test_stage_b_pop_bundle_breakdown_via_low_drum_density():
    """A non-edge section with a drum-density dip becomes BREAKDOWN.

    The pop bundle's middle section (s2) is BRIDGE in Stage A; with a
    sharp drum dropout, Stage B reclassifies it as BREAKDOWN.
    """
    stage_a = _stage_a_for_pop_bundle()
    per_stem = {
        "vocals": [
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),
        ] * 5,
        "drums": [
            _drums_row(note_count=16.0),  # s0
            _drums_row(note_count=16.0),  # s1
            _drums_row(note_count=1.0),   # s2 — heavy dropout
            _drums_row(note_count=16.0),  # s3
            _drums_row(note_count=16.0),  # s4
        ],
    }
    energy_means = [0.5, 0.6, 0.5, 0.6, 0.5]
    aggregates = aggregate_song_form(per_stem, energy_means)
    refined = refine_section_types(stage_a, aggregates)
    assert refined[2] is SectionType.BREAKDOWN


def test_stage_b_no_vocals_stem_keeps_chorus_labels():
    """Regression for the JAM "every section labeled instrumental" bug.

    When ``midi_stems`` contains [guitar, bass, drums] only (no vocals
    stem at all), ``aggregate_song_form`` produces
    ``vocal_activity_score == 0.0`` for every section. The Stage B
    INSTRUMENTAL rule must NOT fire on this case; Stage A's CHORUS
    labels survive.
    """
    stage_a = _stage_a_for_pop_bundle()
    per_stem = {
        "guitar": [_drums_row(note_count=8.0)] * 5,
        "bass": [_drums_row(note_count=8.0)] * 5,
        "drums": [_drums_row(note_count=16.0)] * 5,
    }
    energy_means = [0.5, 0.6, 0.5, 0.6, 0.5]
    aggregates = aggregate_song_form(per_stem, energy_means)
    refined = refine_section_types(stage_a, aggregates)
    # Stage A's chorus labels (s1, s3) must survive.
    assert refined[1] is SectionType.CHORUS
    assert refined[3] is SectionType.CHORUS
    # No INSTRUMENTAL at all.
    assert SectionType.INSTRUMENTAL not in refined


def test_stage_b_riff_uniform_song_low_energy_edges_demoted_to_intro_outro():
    """Regression for the JAM "every section labeled chorus" bug
    (Birds of Tokyo — "If This Ship Sinks").

    A riff-uniform song where every section shares the same chord
    progression makes H2 see ANCHOR everywhere; Stage A maps every
    section to CHORUS. Stage B's Pass 4 (edge-demotion via
    energy_z) should demote a clearly-lower-energy first/last
    section back to INTRO/OUTRO.
    """
    # Stage A forced to all-CHORUS to mimic the all-ANCHOR H2
    # output observed on the Birds-of-Tokyo bundle.
    stage_a = (
        SectionType.CHORUS,
        SectionType.CHORUS,
        SectionType.CHORUS,
        SectionType.CHORUS,
        SectionType.CHORUS,
    )
    per_stem = {
        "vocals": [
            _vocals_row(lead_activity_score=0.2, voiced_frame_ratio=0.6),  # intro: quiet vocals
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),
            _vocals_row(lead_activity_score=0.2, voiced_frame_ratio=0.6),  # outro
        ],
        "drums": [_drums_row(note_count=16.0)] * 5,
    }
    # Section 0 (intro) much lower energy than the body; section 4 (outro) tails off.
    energy_means = [0.145, 0.65, 0.75, 0.70, 0.20]
    aggregates = aggregate_song_form(per_stem, energy_means)
    refined = refine_section_types(stage_a, aggregates)
    assert refined[0] is SectionType.INTRO
    assert refined[-1] is SectionType.OUTRO
    # Middle three remain CHORUS.
    assert refined[1] is SectionType.CHORUS
    assert refined[2] is SectionType.CHORUS
    assert refined[3] is SectionType.CHORUS


def test_pop_punk_all_chorus_stage_a_gets_verse_via_pass_4():
    """End-to-end regression for the pop-punk-shape bug.

    Motivating case: Paramore "That's What You Get" (session
    ``5fff8bd2``) — verse and chorus share the same chord
    progression, so H2 chord-trigram recurrence collapses to
    ANCHOR on every section and Stage A ships an all-CHORUS
    tuple. The old uniform-mode role classifier + a Stage B
    that lacked a CHORUS→VERSE pass rendered 14 pills all
    labelled ``chorus`` in the JAM UI.

    This test constructs a synthetic 8-section bundle where
    every section shares the same [C, G, Am, F] progression
    (guaranteeing Stage A produces all CHORUS) and encodes 3
    verse sections at indices 1, 3, 5 with clearly lower
    ``vocal_activity_score`` and ``energy_z`` than the 5
    chorus sections. Pass 4 must demote those three back to
    VERSE.
    """
    # 8 identical-progression sections. Every chord trigram
    # recurs in every section → H2 sees ANCHOR everywhere →
    # Stage A ships all CHORUS.
    chords: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    progression = ("C", "G", "Am", "F")
    for si in range(8):
        base = si * 4.0
        for ci, sym in enumerate(progression):
            chords.append(_chord(base + ci, base + ci + 1, sym))
        sections.append(_section(base, base + 4.0, f"s{si}"))
    bundle = {"chords": chords, "sections": sections}

    h2 = extract_h2(bundle)
    decisions = classify_roles(h2.per_section, h2.h2_sep)
    stage_a = derive_section_types(decisions)
    assert stage_a == (SectionType.CHORUS,) * 8

    # 5 chorus-like sections + 3 verse-like sections at indices
    # 1, 3, 5. Verses have moderate vocals (still above Pass 1's
    # silence ceiling) and clearly lower energy_mean so their
    # ``energy_z`` sits well below the chorus median.
    per_stem = {
        "vocals": [
            _vocals_row(lead_activity_score=0.75, voiced_frame_ratio=0.85),  # 0 chorus
            _vocals_row(lead_activity_score=0.40, voiced_frame_ratio=0.75),  # 1 VERSE
            _vocals_row(lead_activity_score=0.75, voiced_frame_ratio=0.85),  # 2 chorus
            _vocals_row(lead_activity_score=0.40, voiced_frame_ratio=0.75),  # 3 VERSE
            _vocals_row(lead_activity_score=0.75, voiced_frame_ratio=0.85),  # 4 chorus
            _vocals_row(lead_activity_score=0.40, voiced_frame_ratio=0.75),  # 5 VERSE
            _vocals_row(lead_activity_score=0.75, voiced_frame_ratio=0.85),  # 6 chorus
            _vocals_row(lead_activity_score=0.75, voiced_frame_ratio=0.85),  # 7 chorus
        ],
        "drums": [_drums_row(note_count=16.0)] * 8,
    }
    energy_means = [0.75, 0.20, 0.75, 0.20, 0.75, 0.20, 0.75, 0.75]
    aggregates = aggregate_song_form(per_stem, energy_means)
    refined = refine_section_types(stage_a, aggregates)

    assert refined == (
        SectionType.CHORUS,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.CHORUS,
    )


def test_stage_b_defensive_no_op_with_empty_per_stem():
    """Empty per-stem dict + empty energy_means → empty aggregates →
    refine_section_types returns Stage A verbatim (defensive)."""
    stage_a = _stage_a_for_pop_bundle()
    aggregates = aggregate_song_form({}, [])
    refined = refine_section_types(stage_a, aggregates)
    assert refined == stage_a


def test_stage_b_prechorus_via_explicit_chain():
    """End-to-end: a verse-before-chorus chain ramps into PRECHORUS.

    Constructs a Stage A label sequence directly (rather than going
    through extract_h2) because PRECHORUS detection requires a VERSE
    label and the pop bundle's H2 output is CHORUS/BRIDGE only.
    """
    stage_a = (
        SectionType.INTRO,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )
    per_stem = {
        "vocals": [
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),
        ] * 6,
        "drums": [_drums_row(note_count=16.0)] * 6,
    }
    # Second verse (s3) ramps sharply into the second chorus (s4).
    energy_means = [0.1, 0.4, 0.5, 0.3, 0.8, 0.2]
    aggregates = aggregate_song_form(per_stem, energy_means)
    refined = refine_section_types(stage_a, aggregates)
    assert refined == (
        SectionType.INTRO,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.PRECHORUS,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )


# --- Insufficient-data end-to-end (H2 spec §4 field flow) -------------------


def _insufficient_span_bundle() -> dict[str, Any]:
    """6-section bundle: chorus / verse / no-chord span x2 / chorus / outro.

    Sections 2 and 3 span 15s each with **zero** chord symbols in
    their windows — the Paramore-shaped instrumental-gap case.
    Without the ``per_section_insufficient`` fix those two sections
    would collapse to ``h == 0.0`` → UNIQUE → BRIDGE at Stage A.
    With the fix they abstain to DEVELOPMENT → VERSE, and Stage B
    passes them through.

    Chorus sections share a {C,F,G,Am} progression so they land as
    ANCHOR. The verse (s1) uses a distinct {D,A,Bm,G} progression
    that recurs nowhere else (UNIQUE — but at a non-edge position,
    Stage A maps it to BRIDGE). We put a real verse there so the
    test focuses on the insufficient-data disambiguation, not on
    verse-vs-bridge for a single-shot section.
    """
    chords = [
        # s0 chorus: C F G Am
        _chord(0.0, 1.0, "C"), _chord(1.0, 2.0, "F"),
        _chord(2.0, 3.0, "G"), _chord(3.0, 4.0, "Am"),
        # s1 verse: D A Bm G (distinct)
        _chord(4.0, 5.0, "D"), _chord(5.0, 6.0, "A"),
        _chord(6.0, 7.0, "Bm"), _chord(7.0, 8.0, "G"),
        # s2 no-chord gap 8-23
        # s3 no-chord gap 23-38
        # s4 chorus: C F G Am
        _chord(38.0, 39.0, "C"), _chord(39.0, 40.0, "F"),
        _chord(40.0, 41.0, "G"), _chord(41.0, 42.0, "Am"),
        # s5 outro: E B — 2 chords only (would also be insufficient
        # but placed at the edge so the naming layer maps it to OUTRO)
        _chord(42.0, 43.0, "E"), _chord(43.0, 44.0, "B"),
    ]
    sections = [
        _section(0.0, 4.0, "s0"),
        _section(4.0, 8.0, "s1"),
        _section(8.0, 23.0, "s2"),
        _section(23.0, 38.0, "s3"),
        _section(38.0, 42.0, "s4"),
        _section(42.0, 44.0, "s5"),
    ]
    return {"chords": chords, "sections": sections}


def _stage_a_for_insufficient_bundle() -> tuple[SectionType, ...]:
    bundle = _insufficient_span_bundle()
    h2 = extract_h2(bundle)
    assert not h2.degenerate
    # Flag propagation: sections 2 and 3 must be flagged insufficient
    # by the extractor. This is the whole point of the field.
    assert h2.per_section_insufficient[2] is True
    assert h2.per_section_insufficient[3] is True
    decisions = classify_roles(
        h2.per_section,
        h2.h2_sep,
        per_section_insufficient=h2.per_section_insufficient,
    )
    return derive_section_types(decisions)


def test_no_chord_span_labelled_verse_not_bridge():
    """Insufficient-data sections (zero chords in span) must NOT
    land as BRIDGE. They abstain to DEVELOPMENT (via H2 spec §4
    flag) and Stage A maps DEVELOPMENT → VERSE.

    This is the regression that closes the Paramore bug: 30s
    instrumental-gap spans were being labelled ``bridge`` because
    the H2 hard floor collapsed all zero values to UNIQUE.
    """
    stage_a = _stage_a_for_insufficient_bundle()
    # Sections 2 and 3 are the insufficient-data spans.
    assert stage_a[2] is SectionType.VERSE, (
        f"insufficient span s2 should be VERSE (from abstain path); "
        f"got {stage_a[2]}"
    )
    assert stage_a[3] is SectionType.VERSE, (
        f"insufficient span s3 should be VERSE; got {stage_a[3]}"
    )
    assert stage_a[2] is not SectionType.BRIDGE
    assert stage_a[3] is not SectionType.BRIDGE


def test_no_chord_span_survives_stage_b_refinement_as_verse():
    """End-to-end: Stage A VERSE for insufficient sections
    survives Stage B refinement with plausible per-stem signals.

    Pass 1 only demotes CHORUS → INSTRUMENTAL; it never touches
    VERSE. Passes 2/2b would only promote to PRECHORUS if the
    section immediately precedes a CHORUS with a strong ramp.
    With low energy across the insufficient span (typical of a
    quiet build) the sections stay VERSE — not BRIDGE.
    """
    stage_a = _stage_a_for_insufficient_bundle()
    per_stem = {
        "vocals": [
            _vocals_row(lead_activity_score=0.5, voiced_frame_ratio=0.7),  # s0
            _vocals_row(lead_activity_score=0.5, voiced_frame_ratio=0.7),  # s1
            _vocals_row(lead_activity_score=0.5, voiced_frame_ratio=0.7),  # s2
            _vocals_row(lead_activity_score=0.5, voiced_frame_ratio=0.7),  # s3
            _vocals_row(lead_activity_score=0.5, voiced_frame_ratio=0.7),  # s4
            _vocals_row(lead_activity_score=0.5, voiced_frame_ratio=0.7),  # s5
        ],
        "drums": [_drums_row(note_count=16.0)] * 6,
    }
    energy_means = [0.5, 0.4, 0.3, 0.3, 0.6, 0.2]
    aggregates = aggregate_song_form(per_stem, energy_means)
    refined = refine_section_types(stage_a, aggregates)
    # The two insufficient-data sections must not be BRIDGE either
    # before or after Stage B refinement. This is the whole point
    # of the abstain fix — before it, the H2 hard floor collapsed
    # both zeros to UNIQUE and Stage A mapped both to BRIDGE.
    #
    # Section 1 (s1) is intentionally allowed to be BRIDGE — its
    # 4 distinct chords appear nowhere else in the bundle, so its
    # H2 is a *genuine* 0.0 (recurrence tested and rejected), not
    # an abstain. That's the "genuine UNIQUE" path and it must
    # keep working.
    assert refined[2] is not SectionType.BRIDGE
    assert refined[3] is not SectionType.BRIDGE
