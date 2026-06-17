"""Integration test for guidance-mode wire-up in ``unified_pipeline``.

The full ``UnifiedPipeline.analyze`` path requires running stem separation
and MIDI extraction on synthesised audio, which is slow and noisy enough
that any classifier failure would be masked by detector noise. This test
instead exercises the *composition* the pipeline applies between chord
detection and ``_build_result``:

    for section in sections:
        per_stem = [compute_section_features(...) for stem in midi_stems]
        decision = classify_section(per_stem)
        section.guidance_mode = decision.mode
        ...

We feed synthetic per-stem MIDI dicts shaped exactly like
``_extract_midi_ensemble`` produces (``{"notes": [{pitch, start, end,
velocity}, ...], ...}``), three ``ArrangementSection`` windows covering
a chord block → riff loop → lead phrase progression, and assert that
each section's guidance_mode comes out in the expected band.

If this test ever fires it means either the wire-up composition has
drifted from the pipeline call site or the signal extractors / classifier
thresholds have moved underneath us.
"""
from __future__ import annotations

from typing import List

from tone_forge.analysis.guidance_mode import classify_section
from tone_forge.analysis.section_features import compute_section_features
from tone_forge.analysis.sections import ArrangementSection, SectionType


# ---------------------------------------------------------------------------
# Fixture builders — mirror the shape the real pipeline persists in
# ``midi_stems[stem_type]["notes"]``: list of {pitch, start, end, velocity}.
# ---------------------------------------------------------------------------

def _note(pitch: int, start: float, end: float) -> dict:
    return {"pitch": pitch, "start": start, "end": end, "velocity": 90}


def _chord_block_notes(t0: float, bars: int = 4, bar_s: float = 2.0) -> list[dict]:
    """Four held triads, one per bar, in C/F/G/Am rotation.

    Triads → polyphony per voxel is 3, monophonic_ratio ≈ 0, chord-density
    is supplied by the synthetic chord_regions list.
    """
    triads = [
        (60, 64, 67),   # C
        (65, 69, 72),   # F
        (67, 71, 74),   # G
        (57, 60, 64),   # Am
    ]
    out: list[dict] = []
    for b in range(bars):
        t_start = t0 + b * bar_s
        t_end = t_start + bar_s
        for pitch in triads[b % len(triads)]:
            out.append(_note(pitch, t_start, t_end))
    return out


def _riff_loop_notes(t0: float, loops: int = 4, period_s: float = 2.0) -> list[dict]:
    """E2-G2-A2-D3 power-chord-root pattern, 4-note period, four loops.

    Monophonic (one note at a time), high repetition_score, no polyphony.
    """
    pattern_pitches = [40, 43, 45, 50]  # E2 G2 A2 D3
    n_in_period = len(pattern_pitches)
    note_dur = period_s / n_in_period
    out: list[dict] = []
    for k in range(loops):
        for i, p in enumerate(pattern_pitches):
            s = t0 + k * period_s + i * note_dur
            out.append(_note(p, s, s + note_dur * 0.9))
    return out


def _lead_phrase_notes(t0: float, duration_s: float = 8.0) -> list[dict]:
    """Sparse melodic phrase with wide intervals — top of guitar range."""
    # 8 notes across 8 s = 1 note/s, mean interval ~5-7 semitones
    schedule = [
        (76, 0.0, 0.7),  # E5
        (72, 0.9, 1.5),  # C5
        (79, 1.7, 2.5),  # G5
        (74, 2.7, 3.4),  # D5
        (81, 3.6, 4.4),  # A5
        (76, 4.6, 5.3),  # E5
        (83, 5.5, 6.4),  # B5
        (77, 6.6, 7.6),  # F5
    ]
    return [_note(p, t0 + s, t0 + e) for (p, s, e) in schedule]


def _make_section(
    *, kind: SectionType, start: float, end: float
) -> ArrangementSection:
    return ArrangementSection(
        type=kind,
        start_time=start,
        end_time=end,
        confidence=0.8,
    )


def _apply_pipeline_wireup(
    sections: List[ArrangementSection],
    midi_stems: dict,
    chord_regions: tuple,
) -> List[ArrangementSection]:
    """Mirror of the wire-up block in ``unified_pipeline.analyze_streaming``.

    Kept in sync with the call site so a drift in either flips this test.
    """
    stem_notes_by_name = {
        name: (data.get("notes") or []) for name, data in midi_stems.items()
    }
    out: List[ArrangementSection] = []
    for section in sections:
        per_stem = [
            compute_section_features(
                stem_name=name,
                stem_midi=notes,
                chord_regions=chord_regions,
                section_start_s=float(section.start_time),
                section_end_s=float(section.end_time),
            )
            for name, notes in stem_notes_by_name.items()
        ]
        decision = classify_section(per_stem)
        section.guidance_mode = decision.mode
        section.guidance_confidence = float(decision.confidence)
        section.guidance_reason = decision.reason
        out.append(section)
    return out


# ---------------------------------------------------------------------------
# The canonical 3-section integration regression.
# ---------------------------------------------------------------------------

def test_three_section_progression_classifies_chord_riff_lead() -> None:
    """Chord block (0-8s) → riff loop (8-16s) → lead phrase (16-24s).

    One ``other`` stem carrying the harmonic / melodic content. The
    pipeline wire-up loops sections × stems and classifies each section
    independently — this asserts the integration produces the right
    guidance label per window.
    """
    other_notes: list[dict] = []
    other_notes.extend(_chord_block_notes(t0=0.0, bars=4, bar_s=2.0))
    other_notes.extend(_riff_loop_notes(t0=8.0, loops=4, period_s=2.0))
    other_notes.extend(_lead_phrase_notes(t0=16.0, duration_s=8.0))

    midi_stems = {
        "other": {"notes": other_notes, "note_count": len(other_notes)},
    }

    # Chord regions only exist under the chord block — at 0.5/sec the
    # density signal for that section is ~0.5, and the riff/lead
    # sections see zero chord regions (because the chord detector
    # wouldn't fire confidently there).
    chord_regions = (
        {"start_s": 0.0, "end_s": 2.0, "symbol": "C"},
        {"start_s": 2.0, "end_s": 4.0, "symbol": "F"},
        {"start_s": 4.0, "end_s": 6.0, "symbol": "G"},
        {"start_s": 6.0, "end_s": 8.0, "symbol": "Am"},
    )

    sections = [
        _make_section(kind=SectionType.VERSE,  start=0.0,  end=8.0),
        _make_section(kind=SectionType.VERSE,  start=8.0,  end=16.0),
        _make_section(kind=SectionType.BRIDGE, start=16.0, end=24.0),
    ]

    classified = _apply_pipeline_wireup(sections, midi_stems, chord_regions)

    assert [s.guidance_mode for s in classified] == ["chord", "riff", "lead"]
    for s in classified:
        assert s.guidance_confidence >= 0.5, (
            f"{s.type.value} section confidence too low: "
            f"{s.guidance_confidence:.2f} ({s.guidance_reason})"
        )
        # Reason string must include the contributing stem so the JAM
        # UI can later surface "why".
        assert "other=" in s.guidance_reason


def test_section_to_dict_round_trips_guidance_fields() -> None:
    """Persisted AnalysisResult must carry the new fields downstream."""
    s = _make_section(kind=SectionType.VERSE, start=0.0, end=8.0)
    s.guidance_mode = "riff"
    s.guidance_confidence = 0.73
    s.guidance_reason = "riff: other=riff(0.73)"

    d = s.to_dict()
    assert d["guidance_mode"] == "riff"
    assert d["guidance_confidence"] == 0.73
    assert d["guidance_reason"] == "riff: other=riff(0.73)"


def test_legacy_section_defaults_to_chord_for_bundle_compat() -> None:
    """An ArrangementSection built without guidance fields (e.g. from a
    pre-milestone bundle round-trip or the SectionDetector path that
    doesn't run the classifier) must default to chord/0.0/empty so the
    JAM UI silently falls back to the chord ribbon.
    """
    s = _make_section(kind=SectionType.VERSE, start=0.0, end=8.0)
    assert s.guidance_mode == "chord"
    assert s.guidance_confidence == 0.0
    assert s.guidance_reason == ""

    d = s.to_dict()
    assert d["guidance_mode"] == "chord"
    assert d["guidance_confidence"] == 0.0
    assert d["guidance_reason"] == ""


def test_empty_midi_stems_silently_yields_chord_default() -> None:
    """A section with no stems contributing notes must vote chord/0.0 —
    matches ``classify_section([])`` behaviour and the JAM UI fallback.
    """
    sections = [_make_section(kind=SectionType.INTRO, start=0.0, end=4.0)]
    classified = _apply_pipeline_wireup(sections, midi_stems={}, chord_regions=())
    assert classified[0].guidance_mode == "chord"
    assert classified[0].guidance_confidence == 0.0
