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
    pc_diversity: float = 1.0,
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
        pitch_class_diversity=pc_diversity,
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


# ---------------------------------------------------------------------------
# Engine fix #8: pitch-class-diversity discount on lead score
# ---------------------------------------------------------------------------

def test_low_pc_diversity_shrinks_per_stem_lead_score() -> None:
    """SLTS verse regression. A monophonic bass stem with high
    lead-activity but only ~4 chord roots (low pitch-class diversity)
    must have its lead score discounted toward zero.

    Single-stem aggregator confidence normalises to 1.0 regardless
    (one vote → 100% of vote weight), so we read the per-stem score
    out of the reason string, which the classifier emits in
    ``stem=mode(score)`` form.

    Expected math:
      score_lead(high pc) = 0.95 * 0.70 * 1.000 ≈ 0.665
      score_lead(low pc)  = 0.95 * 0.70 * 0.558 ≈ 0.371
    """
    import math
    import re
    pc_div_4 = math.log(4) / math.log(12)
    sf_low = _sf(
        stem="bass",
        mono=0.95, rep=0.10, poly=0.0, lead=0.70,
        chord_density=0.0,
        pc_diversity=pc_div_4,
    )
    sf_high = _sf(
        stem="bass",
        mono=0.95, rep=0.10, poly=0.0, lead=0.70,
        chord_density=0.0,
        pc_diversity=1.0,
    )
    d_low = classify_section([sf_low])
    d_high = classify_section([sf_high])

    def _score(reason: str) -> float:
        m = re.search(r"bass=lead\(([\d.]+)\)", reason)
        assert m, f"no per-stem lead score in reason {reason!r}"
        return float(m.group(1))

    score_low = _score(d_low.reason)
    score_high = _score(d_high.reason)
    assert score_low < score_high, (
        f"low pc_diversity must shrink lead score "
        f"(got {score_low:.3f} vs high-pc {score_high:.3f})"
    )
    # And it should land in the expected band: 0.665 * 0.558 ≈ 0.371.
    assert 0.30 < score_low < 0.45, f"unexpected score_low={score_low:.3f}"
    assert 0.60 < score_high < 0.70, f"unexpected score_high={score_high:.3f}"


def test_low_pc_diversity_bass_loses_to_chord_pad_in_vote() -> None:
    """Full SLTS verse shape: bass riff + other chord pad. Pre-fix the
    bass voted lead with confidence ~0.665 and out-weighed a
    moderately-voiced chord pad. Post-fix the bass lead score is
    discounted by pc_diversity and the chord pad wins the section.
    """
    import math
    pc_div_4 = math.log(4) / math.log(12)
    bass_riff = _sf(
        stem="bass",
        mono=0.95, rep=0.10, poly=0.0, lead=0.70,
        voiced=1.0, duration=8.0,
        pc_diversity=pc_div_4,
    )
    chord_pad = _sf(
        stem="other",
        chord_density=0.5, mono=0.02, poly=0.6, rep=0.05, lead=0.05,
        voiced=0.8, duration=8.0,
        pc_diversity=1.0,
    )
    d = classify_section([bass_riff, chord_pad])
    assert d.mode == "chord", f"expected chord; got {d.mode} ({d.reason})"


def test_high_pc_diversity_keeps_real_lead_classified_lead() -> None:
    """Sanity check: a real lead (varied melody, ~all chromatic pcs)
    must still classify as lead after the discount. Without this
    guarantee fix #8 would over-correct and demote every monophonic
    stem to chord."""
    real_lead = _sf(
        stem="vocals",
        mono=0.95, rep=0.10, poly=0.0, lead=0.85,
        chord_density=0.0,
        pc_diversity=0.92,   # nearly all pcs visited
    )
    d = classify_section([real_lead])
    assert d.mode == "lead"
    assert d.confidence > 0.5
