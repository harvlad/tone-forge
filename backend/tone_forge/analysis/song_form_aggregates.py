"""Per-section song-form aggregates derived from per-stem features.

Stage B (refinement) of the H2-first section-naming pipeline consumes
these aggregates to disambiguate section types that H2 alone cannot —
INSTRUMENTAL (chorus without vocals), PRECHORUS (verse ramping into
chorus), BREAKDOWN (drum-density dip).

The composition layer (``unified_pipeline.py``,
``local_engine/analysis_worker.py``) already computes per-stem
``SectionFeatures`` rows in guidance-mode classification; this module
reduces those rows into a small fixed-shape ``SongFormAggregates``
record per section. No audio I/O, no RNG.

Boundary
--------
Imports only stdlib + numpy. Per-stem features cross in as a plain
``Mapping[str, Sequence]`` of duck-typed objects exposing
``lead_activity_score``, ``voiced_frame_ratio``, ``note_count``, and
``duration_s``. The concrete ``SectionFeatures`` dataclass is
structurally compatible; we do not import it so callers can pass a
lighter test fixture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np


# Stem name conventions used by the analysis pipeline. Lower-cased so
# the lookup is robust to "Vocals" / "VOCALS" feeding in.
_VOCAL_STEM_NAMES: frozenset[str] = frozenset({"vocals", "vocal"})
_DRUM_STEM_NAMES: frozenset[str] = frozenset({"drums", "drum"})

# Songs with median drum density below this floor are treated as
# "no-drum" songs; ``drum_density_z`` returns 0.0 across the board
# so Stage B's BREAKDOWN rule doesn't fire on an acoustic ballad.
_DRUM_DENSITY_FLOOR: float = 0.10

# Energy floor for the ramp denominator. Without this an all-silent
# intro (energy_mean ≈ 0) yields ``ramp = inf`` and Stage B sees a
# spurious PRECHORUS / BUILDUP everywhere.
_ENERGY_RAMP_DENOM_FLOOR: float = 1e-3


@dataclass(frozen=True)
class SongFormAggregates:
    """Per-section signals derived from per-stem ``SectionFeatures``.

    Fields default to 0.0 when a particular signal is unavailable
    (e.g. vocals stem missing). Consumers treat 0.0 as "no evidence"
    and abstain rather than firing a confidently-wrong relabel.

    All fields are scalars to keep the cross-module surface flat —
    Stage B's ``refine_section_types`` can consume a tuple of these
    without unpacking nested structures.
    """

    vocal_activity_score: float = 0.0
    """``lead_activity_score × voiced_frame_ratio`` on the vocals stem.

    0.0 when the vocals stem is absent or has no voiced frames.
    Used by Stage B to flip CHORUS → INSTRUMENTAL when below a
    song-level ceiling."""

    drum_density_per_s: float = 0.0
    """Drums-stem ``note_count / duration_s``. 0.0 when drums stem is
    absent. Raw rate; ``drum_density_z`` is the song-relative
    standardised score that Stage B actually consumes for
    BREAKDOWN detection."""

    drum_density_z: float = 0.0
    """Robust z-score of ``drum_density_per_s`` across the song
    (median + MAD). 0.0 when the song's median density is below
    ``_DRUM_DENSITY_FLOOR`` (no-drum songs) so BREAKDOWN doesn't
    fire on tracks with no drums to drop out of."""

    energy_ramp_into_next: float = 0.0
    """Relative change from this section's ``energy_mean`` to the
    next section's. ``(next - this) / max(this, floor)``. 0.0 for
    the last section in the song. Used for PRECHORUS detection
    (when next is CHORUS) and BUILDUP transition annotation."""

    energy_z: float = 0.0
    """Robust z-score of ``energy_mean`` across the song (median +
    MAD with stdev fallback). 0.0 when the song's energies are
    constant or empty. Used by Stage B's edge-demotion rule to
    catch riff-uniform songs where H2 sees ANCHOR everywhere and
    Stage A maps every section to CHORUS: a clearly-lower-energy
    edge gets demoted to INTRO/OUTRO."""

    vocal_pitch_median_semitones: float = 0.0
    """Median MIDI pitch (semitones) of vocals-stem notes in this
    section. 0.0 = "no evidence" — either the vocals stem is
    absent, this section has no overlapping vocal notes, or the
    upstream ``SectionFeatures`` row carried ``None`` for the
    pitch field. Consumers (Stage B's Pass 4b) treat 0.0 as
    abstain rather than firing on a phantom pitch dip."""

    vocal_pitch_range_semitones: float = 0.0
    """p90-p10 spread (semitones) of vocals-stem note pitches in
    this section. 0.0 = "no evidence" under the same conditions
    as ``vocal_pitch_median_semitones``. Combined with the
    median via an AND-gate in Pass 4b so that a chorus-labelled
    section only demotes to VERSE when the singer both sits
    lower AND has less pitch mobility than the intra-CHORUS
    cohort."""


def _get_attr_or_key(obj: Any, name: str, default: float = 0.0) -> float:
    """Read a field by either attribute or mapping key.

    Mirrors ``section_features._note_pitch`` and friends — the
    pipelines pass real ``SectionFeatures`` instances, but tests
    sometimes pass dicts for convenience.
    """
    if isinstance(obj, Mapping):
        return float(obj.get(name, default))
    return float(getattr(obj, name, default))


def _stem_features_for(
    per_stem_features: Mapping[str, Sequence[Any]],
    names: frozenset[str],
) -> Sequence[Any] | None:
    """Return the first matching stem's per-section features, or None.

    Lookup is case-insensitive on the stem-name key.
    """
    for key, rows in per_stem_features.items():
        if key.lower() in names:
            return rows
    return None


def _vocal_activity(row: Any) -> float:
    """``lead_activity_score × voiced_frame_ratio``.

    Both factors live in [0, ∞) (lead activity) and [0, 1] (voiced
    ratio); their product is positive and unbounded above but in
    practice well under 1.0 for typical vocal lanes. We don't clamp;
    Stage B compares against a tuned ceiling.
    """
    lead = _get_attr_or_key(row, "lead_activity_score", 0.0)
    voiced = _get_attr_or_key(row, "voiced_frame_ratio", 0.0)
    return lead * voiced


def _optional_float(row: Any, name: str) -> float:
    """Read a field that may be ``None`` on the source row.

    ``_get_attr_or_key`` coerces to ``float`` unconditionally,
    which crashes on ``None``. ``SectionFeatures`` emits ``None``
    for ``pitch_median_semitones`` / ``pitch_range_semitones``
    when the section has no vocal-note evidence; we surface that
    as 0.0 = "abstain" to match the aggregate-field convention.
    """
    if isinstance(row, Mapping):
        raw = row.get(name)
    else:
        raw = getattr(row, name, None)
    if raw is None:
        return 0.0
    return float(raw)


def _pitch_median(row: Any) -> float:
    """Vocals-row pitch median (semitones), 0.0 when unavailable."""
    return _optional_float(row, "pitch_median_semitones")


def _pitch_range(row: Any) -> float:
    """Vocals-row pitch spread (p90-p10 semitones), 0.0 when
    unavailable."""
    return _optional_float(row, "pitch_range_semitones")


def _drum_density(row: Any) -> float:
    note_count = _get_attr_or_key(row, "note_count", 0.0)
    duration = _get_attr_or_key(row, "duration_s", 0.0)
    if duration <= 0.0:
        return 0.0
    return note_count / duration


def _robust_z_scores_core(values: Sequence[float]) -> tuple[float, ...]:
    """Median + MAD z-scores for a sequence of floats.

    Returns 0.0 for every entry when:
      - The sequence is empty.
      - The MAD is zero AND stdev is zero (constant input).

    Robust to outliers (the all-quiet outro doesn't drag the median).
    """
    if not values:
        return ()
    arr = np.asarray(values, dtype=np.float64)
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median)))
    if mad > 0.0:
        # 1.4826 scales MAD to match the standard deviation under a
        # Gaussian; lets ceiling thresholds be interpreted as
        # "≈ N standard deviations below median".
        scale = 1.4826 * mad
    else:
        # Bimodal distribution: most sections share an exact value
        # and one or two outliers differ. MAD collapses to zero;
        # fall back to plain stdev so the outlier still scores.
        std = float(np.std(arr))
        if std <= 0.0:
            return tuple(0.0 for _ in values)
        scale = std
    return tuple(float((v - median) / scale) for v in arr)


def _robust_z_scores(values: Sequence[float]) -> tuple[float, ...]:
    """Drum-density z-scores: ``_robust_z_scores_core`` plus a
    no-drum-song guard.

    Returns 0.0 for every entry when the median is below
    ``_DRUM_DENSITY_FLOOR`` (no-drum song) so BREAKDOWN doesn't fire
    on an acoustic ballad.
    """
    if not values:
        return ()
    arr = np.asarray(values, dtype=np.float64)
    median = float(np.median(arr))
    if median < _DRUM_DENSITY_FLOOR:
        return tuple(0.0 for _ in values)
    return _robust_z_scores_core(values)


def aggregate_song_form(
    per_stem_features: Mapping[str, Sequence[Any]],
    energy_means: Sequence[float],
) -> tuple[SongFormAggregates, ...]:
    """Reduce per-stem per-section features to per-section aggregates.

    Args:
        per_stem_features: Mapping from stem name to per-section
            feature rows. Expected stems include "vocals", "drums",
            "bass", "other" — any subset is accepted; missing stems
            yield 0.0 for their derived signals.
        energy_means: Per-section ``energy_mean`` from
            ``ArrangementSection.energy_mean``, used to compute
            the ramp signal.

    Returns:
        Tuple of ``SongFormAggregates``, aligned 1-to-1 with the
        section list (whose length is the longest input length).
        Empty input → empty tuple.

    The function never raises on shape mismatch between
    ``per_stem_features`` and ``energy_means``; it pads with
    zero-aggregates for any section index that lacks data.
    """
    # Section count: use energy_means length when available, else
    # fall back to the longest per-stem feature sequence.
    n = len(energy_means)
    if n == 0:
        for rows in per_stem_features.values():
            n = max(n, len(rows))
    if n == 0:
        return ()

    vocals_rows = _stem_features_for(per_stem_features, _VOCAL_STEM_NAMES)
    drums_rows = _stem_features_for(per_stem_features, _DRUM_STEM_NAMES)

    # Per-section vocal activity (0.0 when stem absent).
    if vocals_rows is None:
        vocal_scores: tuple[float, ...] = tuple(0.0 for _ in range(n))
    else:
        vocal_scores = tuple(
            _vocal_activity(vocals_rows[i]) if i < len(vocals_rows) else 0.0
            for i in range(n)
        )

    # Per-section vocal pitch stats (0.0 = abstain when stem
    # absent, when this section has no vocal-note evidence, or
    # when the upstream row carried ``None`` for the pitch field
    # — see ``_optional_float``). Consumed by Stage B's Pass 4b
    # CHORUS→VERSE demotion on shared-progression songs where H2
    # alone can't tell verse from chorus.
    if vocals_rows is None:
        pitch_medians: tuple[float, ...] = tuple(0.0 for _ in range(n))
        pitch_ranges: tuple[float, ...] = tuple(0.0 for _ in range(n))
    else:
        pitch_medians = tuple(
            _pitch_median(vocals_rows[i]) if i < len(vocals_rows) else 0.0
            for i in range(n)
        )
        pitch_ranges = tuple(
            _pitch_range(vocals_rows[i]) if i < len(vocals_rows) else 0.0
            for i in range(n)
        )

    # Per-section drum density (0.0 when stem absent).
    if drums_rows is None:
        drum_densities: tuple[float, ...] = tuple(0.0 for _ in range(n))
    else:
        drum_densities = tuple(
            _drum_density(drums_rows[i]) if i < len(drums_rows) else 0.0
            for i in range(n)
        )
    drum_zs = _robust_z_scores(drum_densities)
    if len(drum_zs) < n:
        drum_zs = drum_zs + tuple(0.0 for _ in range(n - len(drum_zs)))

    # Energy ramp: forward-difference, normalised by current energy.
    energies = list(energy_means) + [0.0] * max(0, n - len(energy_means))
    ramps: list[float] = []
    for i in range(n):
        if i == n - 1:
            ramps.append(0.0)
            continue
        cur = energies[i]
        nxt = energies[i + 1]
        denom = max(cur, _ENERGY_RAMP_DENOM_FLOOR)
        ramps.append((nxt - cur) / denom)

    # Energy z-score: song-relative standardised energy. No
    # density floor (unlike drums, energy is always meaningful;
    # a silent intro is the signal we want to detect).
    energy_zs = _robust_z_scores_core(energies[:n])
    if len(energy_zs) < n:
        energy_zs = energy_zs + tuple(0.0 for _ in range(n - len(energy_zs)))

    return tuple(
        SongFormAggregates(
            vocal_activity_score=vocal_scores[i],
            drum_density_per_s=drum_densities[i],
            drum_density_z=drum_zs[i],
            energy_ramp_into_next=ramps[i],
            energy_z=energy_zs[i],
            vocal_pitch_median_semitones=pitch_medians[i],
            vocal_pitch_range_semitones=pitch_ranges[i],
        )
        for i in range(n)
    )
