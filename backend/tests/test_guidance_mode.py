"""Unit tests for ``analysis.guidance_mode``.

Operates on hand-constructed ``SectionFeatures`` so the classifier is
tested in isolation from the signal extractors. The aggregator is
tested by feeding multi-stem feature lists.
"""
from __future__ import annotations

from tone_forge.analysis.guidance_mode import (
    GuidanceDecision,
    GuidanceThresholds,
    classify_section,
)
from tone_forge.analysis.section_features import SectionFeatures


def _sf(
    *,
    stem: str = "other",
    chord_density: float = 0.0,
    mono: float = 0.0,
    rep: float = 0.0,
    poly: float = 0.0,
    lead: float = 0.0,
    voiced: float = 1.0,
    duration: float = 8.0,
    notes: int = 16,
) -> SectionFeatures:
    return SectionFeatures(
        stem_name=stem,
        chord_density_per_s=chord_density,
        chord_count_in_section=int(chord_density * duration),
        monophonic_ratio=mono,
        repetition_score=rep,
        repetition_period_beats=None,
        polyphony_score=poly,
        lead_activity_score=lead,
        voiced_frame_ratio=voiced,
        note_count=notes,
        duration_s=duration,
    )


# ---------------------------------------------------------------------------
# Single-stem decisions
# ---------------------------------------------------------------------------

def test_pure_chord_stem_classifies_chord() -> None:
    sf = _sf(chord_density=0.5, mono=0.02, poly=0.6, rep=0.1, lead=0.1)
    d = classify_section([sf])
    assert d.mode == "chord"
    assert d.confidence > 0.5


def test_pure_riff_stem_classifies_riff() -> None:
    sf = _sf(mono=0.95, rep=0.85, poly=0.05, lead=0.4, chord_density=0.0)
    d = classify_section([sf])
    assert d.mode == "riff"
    assert d.confidence > 0.5


def test_pure_lead_stem_classifies_lead() -> None:
    sf = _sf(mono=0.95, rep=0.1, poly=0.05, lead=0.85, chord_density=0.0)
    d = classify_section([sf])
    assert d.mode == "lead"
    assert d.confidence > 0.5


def test_silent_stem_falls_back_to_chord_zero_confidence() -> None:
    sf = _sf(voiced=0.0, mono=0.0, rep=0.0, poly=0.0, lead=0.0)
    d = classify_section([sf])
    assert d.mode == "chord"
    assert d.confidence == 0.0


def test_tie_falls_back_to_chord() -> None:
    # score_chord == 0.30 (poly only), score_riff == 0.30 (mono*rep),
    # within default tie_margin=0.08 → chord.
    sf = _sf(mono=0.6, rep=0.5, poly=0.3, lead=0.0, chord_density=0.0)
    d = classify_section([sf])
    assert d.mode == "chord"


def test_tie_margin_knob_flips_decision() -> None:
    # Construct a stem where riff edges out chord by 0.05. With
    # default tie_margin=0.08 → chord. With tie_margin=0.02 → riff.
    sf = _sf(mono=0.7, rep=0.5, poly=0.3, lead=0.0, chord_density=0.0)
    # score_chord = 0.3 + 0.5*0 = 0.30
    # score_riff  = 0.7 * 0.5    = 0.35
    # diff 0.05; tie at margin 0.08 → chord, margin 0.02 → riff
    d_default = classify_section([sf])
    assert d_default.mode == "chord"
    d_tight = classify_section([sf], GuidanceThresholds(tie_margin=0.02))
    assert d_tight.mode == "riff"


# ---------------------------------------------------------------------------
# Multi-stem aggregation
# ---------------------------------------------------------------------------

def test_chord_pad_dominates_quiet_riff_bass() -> None:
    """Loud chord stem + quiet bass riff → chord wins on vote weight."""
    chord_pad = _sf(
        stem="other",
        chord_density=0.5, mono=0.02, poly=0.6, rep=0.05, lead=0.05,
        voiced=1.0, duration=8.0,
    )
    bass_riff = _sf(
        stem="bass",
        mono=0.95, rep=0.85, poly=0.05, lead=0.4,
        # Bass only voiced 20% of the section → low vote weight.
        voiced=0.2, duration=8.0,
    )
    d = classify_section([chord_pad, bass_riff])
    assert d.mode == "chord"
    assert "other=chord" in d.reason
    assert "bass=riff" in d.reason


def test_loud_riff_beats_quiet_chord_pad() -> None:
    """Inverse of the above: riff stem dominant in voicing → riff wins."""
    chord_pad = _sf(
        stem="other",
        chord_density=0.5, mono=0.02, poly=0.6, rep=0.05, lead=0.05,
        voiced=0.15, duration=8.0,
    )
    bass_riff = _sf(
        stem="bass",
        mono=0.95, rep=0.85, poly=0.05, lead=0.4,
        voiced=1.0, duration=8.0,
    )
    d = classify_section([bass_riff, chord_pad])
    assert d.mode == "riff"


def test_all_silent_stems_yields_chord_zero() -> None:
    silent_a = _sf(stem="bass", voiced=0.0)
    silent_b = _sf(stem="other", voiced=0.0)
    d = classify_section([silent_a, silent_b])
    assert d.mode == "chord"
    assert d.confidence == 0.0
    assert "all_silent" in d.reason


def test_empty_per_stem_yields_chord_default() -> None:
    d = classify_section([])
    assert isinstance(d, GuidanceDecision)
    assert d.mode == "chord"
    assert d.confidence == 0.0


def test_reason_includes_every_stem() -> None:
    d = classify_section([
        _sf(stem="bass", mono=0.95, rep=0.85),
        _sf(stem="other", chord_density=0.5, poly=0.6),
        _sf(stem="vocals", mono=0.95, lead=0.85),
    ])
    for stem in ("bass", "other", "vocals"):
        assert f"{stem}=" in d.reason
