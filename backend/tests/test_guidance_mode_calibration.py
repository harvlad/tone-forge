"""Guidance-mode calibration regression tests.

Locks the failure modes observed in the real-song calibration run
into permanent tests. Each test feeds synthetic MIDI streams through
the *real* ``compute_section_features`` → ``classify_section`` path,
so the tests exercise the actual feature extractors — the same
extractors the live pipeline uses — rather than hand-rolled
``SectionFeatures``.

Test naming convention: tests assert the *desired* outcome listed in
the calibration directive. A failing test here means a feature-quality
problem the directive flagged is still alive in the classifier. These
tests are expected to fail today; the directive is to lock the
failures, document them, and only then tune.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the fixtures package importable as `fixtures.guidance_mode` from
# this test file. (`backend/tests` has no __init__.py — same pattern
# the other test files use.)
sys.path.insert(0, str(Path(__file__).parent))

from fixtures.guidance_mode import (  # noqa: E402
    ALL_FIXTURES,
    GuidanceFixture,
    fixture_drums_pitched_artifacts,
    fixture_low_chord_density_no_riff,
    fixture_monophonic_midi_chord_block,
    fixture_riff_minority_vote,
    fixture_sparse_lead_phrase,
)

from tone_forge.analysis.guidance_mode import (  # noqa: E402
    GuidanceThresholds,
    classify_section,
)
from tone_forge.analysis.section_features import (  # noqa: E402
    SectionFeatures,
    compute_section_features,
)


# ---------------------------------------------------------------------------
# Shared runner — pipes a fixture through the real extractor + classifier
# ---------------------------------------------------------------------------

def _features_for(fixture: GuidanceFixture) -> list[SectionFeatures]:
    return [
        compute_section_features(
            stem_name=stem_name,
            stem_midi=notes,
            chord_regions=tuple(fixture.chord_regions),
            section_start_s=fixture.section_start_s,
            section_end_s=fixture.section_end_s,
            beats_s=None,
        )
        for stem_name, notes in fixture.stems.items()
    ]


def _classify(fixture: GuidanceFixture):
    feats = _features_for(fixture)
    return feats, classify_section(feats, GuidanceThresholds())


# ---------------------------------------------------------------------------
# Fixture 1 — chord progression delivered as monophonic streams
# ---------------------------------------------------------------------------

def test_monophonic_midi_chord_block_classifies_chord() -> None:
    fixture = fixture_monophonic_midi_chord_block()
    feats, decision = _classify(fixture)

    # Sanity: per-stem polyphony should be the pinned mono floor.
    assert all(
        abs(f.polyphony_score - 1.0 / 6.0) < 1e-6 for f in feats
    ), (
        "polyphony_score should be the constant 1/6 floor when every "
        "stem is monophonic — this is the failure mode this fixture "
        f"locks: {[round(f.polyphony_score, 3) for f in feats]}"
    )
    # Sanity: chord_density signal is healthy (4 chords / 8s).
    assert all(
        abs(f.chord_density_per_s - 0.5) < 1e-6 for f in feats
    ), [round(f.chord_density_per_s, 3) for f in feats]

    assert decision.mode == "chord", (
        f"monophonic-streams chord block must classify chord; "
        f"got {decision.mode} ({decision.reason})"
    )


# ---------------------------------------------------------------------------
# Fixture 2 — drums pitched artifacts must not dominate
# ---------------------------------------------------------------------------

def test_drums_pitched_artifacts_do_not_flip_classification() -> None:
    fixture = fixture_drums_pitched_artifacts()
    feats, decision = _classify(fixture)

    drums_feat = next(f for f in feats if f.stem_name == "drums")
    # Sanity: drums-stem feature artifacts are present (pcDiv elevated
    # by the synthesised pitched notes; note_count high).
    assert drums_feat.note_count >= 15, drums_feat
    assert drums_feat.pitch_class_diversity > 0.3, (
        f"fixture should produce a drums-like pcDiv (0.4-0.7 band); "
        f"got {drums_feat.pitch_class_diversity:.3f}"
    )

    assert decision.mode == "chord", (
        f"drums spurious-notes stem must not flip a genuine chord "
        f"progression away from chord; got {decision.mode} "
        f"({decision.reason})"
    )


# ---------------------------------------------------------------------------
# Fixture 3 — one riff stem must beat five baseline-noise stems
# ---------------------------------------------------------------------------

def test_riff_minority_vote_wins_against_baseline_chord_noise() -> None:
    fixture = fixture_riff_minority_vote()
    feats, decision = _classify(fixture)

    riff_feat = next(f for f in feats if f.stem_name == "other")
    # Sanity: the riff stem actually has a repetition signal worth
    # voting on.
    assert riff_feat.repetition_score >= 0.5, (
        f"riff stem should expose repetition >= 0.5; got "
        f"{riff_feat.repetition_score:.3f}"
    )
    # Sanity: chord_density is low-but-nonzero (mimics the LMIP regime
    # where the chord detector still emits sparse regions even on a
    # riff-driven section).
    assert 0.0 < feats[0].chord_density_per_s < 0.3, feats[0].chord_density_per_s
    # Sanity: every baseline stem is above the voiced floor so it's
    # actually casting a vote (not auto-silenced).
    from tone_forge.analysis.guidance_mode import GuidanceThresholds as _T
    voiced_floor = _T().voiced_floor
    for f in feats:
        if f.stem_name == "other":
            continue
        assert f.voiced_frame_ratio > voiced_floor, (
            f"baseline stem {f.stem_name} should be above voiced_floor "
            f"to actually cast a vote; got {f.voiced_frame_ratio:.3f}"
        )

    assert decision.mode == "riff", (
        f"one riff-confident stem must beat five baseline-noise stems; "
        f"got {decision.mode} ({decision.reason})"
    )


# ---------------------------------------------------------------------------
# LMIP s1 numerical mirror — hand-rolled SectionFeatures
# ---------------------------------------------------------------------------

def test_lmip_s1_numerical_mirror_classifies_riff() -> None:
    """Lock the actual Let's Make It Pain s1 (7.43-13.00s) failure.

    Hand-constructs the six per-stem ``SectionFeatures`` from the
    calibration diagnostic numbers verbatim and asserts the
    classifier produces ``riff``. Pre-fix this returns ``chord``.

    Diagnostic dump:
        drums   0.84 13  1.00 0.00 0.17 0.58 0.35 0.18 -> chord(0.26)
        bass    1.00  1  1.00 0.00 0.17 0.02 0.00 0.18 -> chord(0.26)
        other   0.88 12  1.00 0.67 0.17 0.43 0.13 0.18 -> riff(0.67)
        vocals  0.54  8  1.00 0.00 0.17 0.68 0.42 0.18 -> chord(0.28)
        guitar  0.27  3  1.00 0.00 0.17 0.50 0.00 0.18 -> chord(0.26)
        piano   0.75 12  1.00 0.00 0.17 0.77 0.28 0.18 -> chord(0.26)

    Aggregator math (pre-fix):
      riff vote  = 0.88 × 5.57 × 0.67 = 3.28
      chord vote = (0.84 + 1.00 + 0.54 + 0.27 + 0.75) × 5.57 × ~0.27
                 = 3.40 × 5.57 × 0.27 ≈ 5.11
    → chord wins ~1.5×. The five baseline chord-floor votes (each
    just the polyphony=0.17 + 0.5×0.18 floor) outweigh one real riff.

    This test expects ``riff``. Failing today is the lock.
    """
    section_dur = 13.0 - 7.43  # 5.57s
    stems = [
        ("drums",  0.84, 13, 1.00, 0.00, 0.17, 0.58, 0.35),
        ("bass",   1.00,  1, 1.00, 0.00, 0.17, 0.02, 0.00),
        ("other",  0.88, 12, 1.00, 0.67, 0.17, 0.43, 0.13),
        ("vocals", 0.54,  8, 1.00, 0.00, 0.17, 0.68, 0.42),
        ("guitar", 0.27,  3, 1.00, 0.00, 0.17, 0.50, 0.00),
        ("piano",  0.75, 12, 1.00, 0.00, 0.17, 0.77, 0.28),
    ]
    cdens = 0.18
    feats = [
        SectionFeatures(
            stem_name=name,
            chord_density_per_s=cdens,
            chord_count_in_section=int(cdens * section_dur),
            monophonic_ratio=mono,
            repetition_score=rep,
            repetition_period_beats=None,
            polyphony_score=poly,
            lead_activity_score=lead,
            voiced_frame_ratio=voi,
            note_count=notes,
            duration_s=section_dur,
            pitch_class_diversity=pcd,
        )
        for name, voi, notes, mono, rep, poly, lead, pcd in stems
    ]
    decision = classify_section(feats, GuidanceThresholds())
    assert decision.mode == "riff", (
        f"LMIP s1 numerical mirror must classify riff; got "
        f"{decision.mode} (conf={decision.confidence:.2f}, "
        f"reason={decision.reason!r}). This failure locks the "
        f"baseline-chord-floor-outvotes-real-riff bug."
    )


# ---------------------------------------------------------------------------
# Fixture 4 — low chord density + no riff → lead, not chord
# ---------------------------------------------------------------------------

def test_low_chord_density_no_riff_classifies_lead() -> None:
    fixture = fixture_low_chord_density_no_riff()
    feats, decision = _classify(fixture)

    f = feats[0]
    # Sanity on the fixture's signal profile.
    assert f.chord_density_per_s == 0.0, f
    assert f.repetition_score < 0.3, f.repetition_score
    assert f.pitch_class_diversity > 0.4, f.pitch_class_diversity

    assert decision.mode == "lead", (
        f"sparse single-stem melody with no chord density and no "
        f"repetition must classify lead; got {decision.mode} "
        f"({decision.reason})"
    )


# ---------------------------------------------------------------------------
# Fixture 5 — canonical lead phrase
# ---------------------------------------------------------------------------

def test_sparse_lead_phrase_classifies_lead() -> None:
    fixture = fixture_sparse_lead_phrase()
    feats, decision = _classify(fixture)

    f = feats[0]
    assert f.chord_density_per_s == 0.0, f
    assert f.repetition_score < 0.3, f.repetition_score
    # High pitch-class diversity is the whole point — confirm the
    # extractor sees it. (9 notes across 9 pitch classes lands ~0.82
    # rather than the theoretical 0.92 for 9-of-12 because the entropy
    # is weighted by clipped duration and our fixture's note durations
    # vary slightly.)
    assert f.pitch_class_diversity > 0.80, (
        f"sparse-lead fixture should produce pcDiv > 0.80; got "
        f"{f.pitch_class_diversity:.3f}"
    )

    assert decision.mode == "lead", (
        f"canonical lead phrase must classify lead; got {decision.mode} "
        f"({decision.reason})"
    )


# ---------------------------------------------------------------------------
# Signal-quality diagnostics (always-passing inspection tests)
# ---------------------------------------------------------------------------

def test_diag_dump_feature_vectors_per_fixture(capsys) -> None:
    """Pretty-print per-stem feature vectors for every fixture.

    This is not an assertion test — it exists so ``pytest -s`` on this
    file produces the same view the calibration diagnostic did, only
    deterministically and against synthetic inputs. The fixture
    failure tests above are the actual regression surface.
    """
    print()
    print(f"{'fixture':<34} {'stem':<12} "
          f"{'voi':>5} {'note#':>5} {'mono':>5} {'rep':>5} {'poly':>5} "
          f"{'lead':>5} {'pcDiv':>5} {'cdens':>5} -> mode (conf)")
    for builder in ALL_FIXTURES:
        fixture = builder()
        feats, decision = _classify(fixture)
        for f in feats:
            print(
                f"  {fixture.name:<32} {f.stem_name:<12} "
                f"{f.voiced_frame_ratio:>5.2f} {f.note_count:>5d} "
                f"{f.monophonic_ratio:>5.2f} {f.repetition_score:>5.2f} "
                f"{f.polyphony_score:>5.2f} {f.lead_activity_score:>5.2f} "
                f"{f.pitch_class_diversity:>5.2f} "
                f"{f.chord_density_per_s:>5.2f}"
            )
        print(
            f"  {fixture.name:<32} {'->':<12} "
            f"expected={fixture.expected_mode:<5} "
            f"got={decision.mode:<5} conf={decision.confidence:.2f}"
        )
        print()


def test_diag_signal_separability_summary() -> None:
    """Print which signals separate the fixtures.

    Specifically the questions the directive asks:

    1. Does monophonic_ratio carry information beyond noise?
    2. Does polyphony_score carry information beyond noise?
    3. Does chord_density_per_s alone separate chord from non-chord?
    4. Do drums materially affect the classification outcome?

    Always passes — output is informational. Run with ``pytest -s`` to
    see the table.
    """
    print()
    rows = []
    for builder in ALL_FIXTURES:
        fixture = builder()
        feats, decision = _classify(fixture)
        mono_vals = [f.monophonic_ratio for f in feats if f.voiced_frame_ratio > 0.1]
        poly_vals = [f.polyphony_score for f in feats if f.voiced_frame_ratio > 0.1]
        cdens_vals = [f.chord_density_per_s for f in feats]
        rows.append({
            "fixture": fixture.name,
            "expected": fixture.expected_mode,
            "got": decision.mode,
            "mono_min": min(mono_vals) if mono_vals else 0.0,
            "mono_max": max(mono_vals) if mono_vals else 0.0,
            "poly_min": min(poly_vals) if poly_vals else 0.0,
            "poly_max": max(poly_vals) if poly_vals else 0.0,
            "cdens_max": max(cdens_vals) if cdens_vals else 0.0,
        })
    print(f"{'fixture':<34} {'exp':<5} {'got':<5} "
          f"{'mono[min..max]':>16} {'poly[min..max]':>16} {'cdens_max':>10}")
    for r in rows:
        print(
            f"  {r['fixture']:<32} {r['expected']:<5} {r['got']:<5} "
            f"{r['mono_min']:.2f}..{r['mono_max']:.2f}   "
            f"{r['poly_min']:.2f}..{r['poly_max']:.2f}   "
            f"{r['cdens_max']:>10.3f}"
        )


# ---------------------------------------------------------------------------
# Drum-removal sensitivity (always-passing diagnostic — informational)
# ---------------------------------------------------------------------------

def test_diag_drums_removal_sensitivity() -> None:
    """Print classifier outcome with and without drums for fixture 2.

    Lets us see numerically whether drums actually do or don't
    materially affect the decision. Informational, no asserts.
    """
    fixture = fixture_drums_pitched_artifacts()
    feats_all = _features_for(fixture)
    feats_no_drums = [f for f in feats_all if f.stem_name != "drums"]
    d_all = classify_section(feats_all, GuidanceThresholds())
    d_no_drums = classify_section(feats_no_drums, GuidanceThresholds())
    print()
    print(f"  drums_included: mode={d_all.mode} conf={d_all.confidence:.3f}")
    print(f"  drums_removed : mode={d_no_drums.mode} conf={d_no_drums.confidence:.3f}")
    print(f"  delta_conf    : {d_no_drums.confidence - d_all.confidence:+.3f}")
