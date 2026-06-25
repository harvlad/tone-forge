"""Hermetic unit tests for ``song_form_aggregates.aggregate_song_form``.

Builds per-stem feature rows as plain dicts (the function accepts
dict- or attribute-style rows). No audio, no MIDI, no pipeline.
"""

from __future__ import annotations

from tone_forge.analysis.song_form_aggregates import (
    SongFormAggregates,
    aggregate_song_form,
)


def _vocals_row(lead: float = 0.0, voiced: float = 0.0) -> dict:
    return {"lead_activity_score": lead, "voiced_frame_ratio": voiced}


def _drums_row(note_count: int = 0, duration_s: float = 1.0) -> dict:
    return {"note_count": note_count, "duration_s": duration_s}


def test_empty_input_returns_empty_tuple():
    assert aggregate_song_form({}, []) == ()


def test_section_count_from_energy_means_when_stems_missing():
    """When no stems are provided, n still comes from energy_means."""
    energies = [0.1, 0.2, 0.3]
    out = aggregate_song_form({}, energies)
    assert len(out) == 3
    # All-zero aggregates except for the ramp.
    assert all(a.vocal_activity_score == 0.0 for a in out)
    assert all(a.drum_density_per_s == 0.0 for a in out)
    assert all(a.drum_density_z == 0.0 for a in out)


def test_missing_vocals_stem_yields_zero_vocal_scores():
    """No vocals key in per_stem_features → vocal_activity_score == 0
    for every section. Other signals computed normally."""
    drums = [_drums_row(10, 2.0), _drums_row(20, 2.0), _drums_row(15, 2.0)]
    out = aggregate_song_form(
        {"drums": drums},
        energy_means=[0.5, 0.6, 0.7],
    )
    assert len(out) == 3
    assert all(a.vocal_activity_score == 0.0 for a in out)
    # Drum density should be 5, 10, 7.5.
    assert out[0].drum_density_per_s == 5.0
    assert out[1].drum_density_per_s == 10.0
    assert out[2].drum_density_per_s == 7.5


def test_vocal_activity_is_lead_times_voiced():
    vocals = [
        _vocals_row(lead=0.5, voiced=0.8),
        _vocals_row(lead=0.0, voiced=0.9),  # zero lead → zero
        _vocals_row(lead=0.4, voiced=0.0),  # zero voiced → zero
    ]
    out = aggregate_song_form({"vocals": vocals}, [0.1, 0.2, 0.3])
    assert abs(out[0].vocal_activity_score - 0.4) < 1e-9
    assert out[1].vocal_activity_score == 0.0
    assert out[2].vocal_activity_score == 0.0


def test_drum_density_z_is_zero_for_no_drum_song():
    """Median drum density below the no-drum floor → z is 0.0
    everywhere (BREAKDOWN must not fire on acoustic ballads)."""
    drums = [
        _drums_row(0, 2.0),
        _drums_row(0, 2.0),
        _drums_row(0, 2.0),
        _drums_row(0, 2.0),
    ]
    out = aggregate_song_form({"drums": drums}, [0.1, 0.1, 0.1, 0.1])
    assert all(a.drum_density_z == 0.0 for a in out)


def test_drum_density_z_flags_outlier_section():
    """Section with much lower drum density than the rest gets a
    strongly negative z-score. Sections at the median sit at z ≈ 0."""
    drums = [
        _drums_row(20, 2.0),   # 10 hits/s
        _drums_row(20, 2.0),   # 10 hits/s
        _drums_row(0,  2.0),   # 0 hits/s  — breakdown candidate
        _drums_row(20, 2.0),   # 10 hits/s
        _drums_row(20, 2.0),   # 10 hits/s
    ]
    out = aggregate_song_form({"drums": drums}, [0.5] * 5)
    # Section 2 should be the smallest z.
    zs = [a.drum_density_z for a in out]
    assert zs[2] == min(zs)
    assert zs[2] < -1.0
    # Median rows sit at z = 0 (MAD-scaled).
    assert abs(zs[0]) < 1e-9
    assert abs(zs[4]) < 1e-9


def test_energy_ramp_forward_difference_normalised():
    """ramp[i] = (energy[i+1] - energy[i]) / max(energy[i], floor).
    Last section's ramp is 0.0 by convention."""
    energies = [0.1, 0.2, 0.4, 0.8, 0.4]
    out = aggregate_song_form({}, energies)
    ramps = [a.energy_ramp_into_next for a in out]
    # (0.2 - 0.1) / 0.1 = 1.0
    assert abs(ramps[0] - 1.0) < 1e-9
    # (0.4 - 0.2) / 0.2 = 1.0
    assert abs(ramps[1] - 1.0) < 1e-9
    # (0.8 - 0.4) / 0.4 = 1.0
    assert abs(ramps[2] - 1.0) < 1e-9
    # (0.4 - 0.8) / 0.8 = -0.5
    assert abs(ramps[3] - (-0.5)) < 1e-9
    # Last section: 0.0
    assert ramps[4] == 0.0


def test_energy_ramp_handles_zero_denominator():
    """A section with energy_mean ≈ 0 must not blow up the ramp."""
    energies = [0.0, 0.5, 1.0]
    out = aggregate_song_form({}, energies)
    # First ramp uses the floor (1e-3), so it's a huge positive
    # number — but finite, not inf/nan.
    assert out[0].energy_ramp_into_next > 100.0
    assert out[0].energy_ramp_into_next < 1e6  # bounded


def test_case_insensitive_stem_lookup():
    """Stem-name lookup is case-insensitive (pipelines emit any of
    "Vocals", "vocals", "VOCALS")."""
    vocals = [_vocals_row(lead=0.6, voiced=1.0)]
    out_lower = aggregate_song_form({"vocals": vocals}, [0.5])
    out_title = aggregate_song_form({"Vocals": vocals}, [0.5])
    out_upper = aggregate_song_form({"VOCALS": vocals}, [0.5])
    assert out_lower[0].vocal_activity_score == 0.6
    assert out_title[0].vocal_activity_score == 0.6
    assert out_upper[0].vocal_activity_score == 0.6


def test_works_with_attribute_style_rows():
    """SectionFeatures dataclass instances are attribute-style.
    Constructs a tiny stand-in via a simple class."""

    class Row:
        def __init__(self, lead: float, voiced: float):
            self.lead_activity_score = lead
            self.voiced_frame_ratio = voiced

    vocals = [Row(0.5, 0.8), Row(0.0, 0.0)]
    out = aggregate_song_form({"vocals": vocals}, [0.1, 0.2])
    assert abs(out[0].vocal_activity_score - 0.4) < 1e-9
    assert out[1].vocal_activity_score == 0.0


def test_shape_mismatch_per_stem_shorter_than_n_does_not_raise():
    """If a per-stem feature sequence is shorter than n, the missing
    sections get 0.0 for that stem's derived signals."""
    vocals = [_vocals_row(lead=0.5, voiced=1.0)]  # length 1
    out = aggregate_song_form({"vocals": vocals}, [0.1, 0.2, 0.3])
    assert len(out) == 3
    assert out[0].vocal_activity_score == 0.5
    assert out[1].vocal_activity_score == 0.0
    assert out[2].vocal_activity_score == 0.0


def test_energy_z_flags_low_energy_intro():
    """A clearly-lower-energy first section gets a strongly
    negative energy_z. Median sections sit near zero."""
    # Energies: intro low, body uniform, outro low.
    energies = [0.1, 0.6, 0.7, 0.65, 0.6, 0.7, 0.1]
    out = aggregate_song_form({}, energies)
    zs = [a.energy_z for a in out]
    # First section: clearly negative.
    assert zs[0] < -1.0
    # Last section: clearly negative.
    assert zs[-1] < -1.0
    # Middle sections: near zero.
    assert abs(zs[2]) < 1.0


def test_energy_z_is_zero_for_constant_song():
    """Constant energy across all sections → MAD and stdev both
    zero → energy_z falls back to 0.0 everywhere."""
    energies = [0.5, 0.5, 0.5, 0.5]
    out = aggregate_song_form({}, energies)
    assert all(a.energy_z == 0.0 for a in out)


def test_energy_z_has_no_density_floor():
    """Unlike drums, energy_z must not zero-out on quiet songs.
    A low-but-uniform-bumpy energy track still produces non-zero
    z-scores so a quieter intro section gets demoted."""
    # All energies well below the drum-density floor (0.10), but
    # one section is clearly lower than the rest.
    energies = [0.001, 0.05, 0.05, 0.05]
    out = aggregate_song_form({}, energies)
    zs = [a.energy_z for a in out]
    # First section is the outlier — z must be negative.
    assert zs[0] < 0.0
    # Median sections near zero.
    assert abs(zs[2]) < 1e-6


def test_aggregates_frozen():
    """SongFormAggregates is frozen — mutation must raise."""
    a = SongFormAggregates()
    try:
        a.vocal_activity_score = 0.5  # type: ignore[misc]
    except (AttributeError, Exception):
        return
    raise AssertionError("SongFormAggregates should be frozen")
