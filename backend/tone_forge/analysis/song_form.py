"""Refine Stage A section types using per-stem song-form signals.

Stage A (``section_naming.derive_section_types``) maps H2 role
decisions onto a minimal vocabulary —
INTRO/VERSE/CHORUS/BRIDGE/OUTRO — using chord-trigram recurrence
plus position. Stage B (this module) takes that output and refines
it using per-section aggregates from
``song_form_aggregates.aggregate_song_form``:

    CHORUS + vocal_activity_score < ceiling
        → INSTRUMENTAL

    VERSE + next is CHORUS + energy_ramp_into_next > floor
        → PRECHORUS

    drum_density_z < ceiling   (and not at the song's edge)
        → BREAKDOWN

    transition.to_section's refined type is CHORUS
        + aggregates[from_section].energy_ramp_into_next > floor
        → transition.type = "buildup"

All four rules are conservative one-sided thresholds: when a signal
is ambiguous, Stage A's label survives. Pure function, no I/O, no
RNG.

Boundary
--------
Imports only ``tone_forge.analysis.sections`` (own subsystem) plus
``tone_forge.analysis.song_form_aggregates``. Does not cross the
analysis subsystem boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from tone_forge.analysis.sections import SectionType
from tone_forge.analysis.song_form_aggregates import SongFormAggregates


@dataclass(frozen=True)
class SongFormThresholds:
    """Frozen tuning surface for Stage B refinement.

    Defaults are initial calibration values (see
    ``song_form_classifier_design.md`` §Thresholds). B5 sweeps
    these against the canonical-6 corpus and locks the final
    values back into the design doc.
    """

    vocal_silence_ceiling: float = 0.15
    """``vocal_activity_score`` below this → vocals deemed absent;
    a CHORUS flips to INSTRUMENTAL. One-sided: never flips the
    reverse direction."""

    prechorus_ramp_floor: float = 0.25
    """``energy_ramp_into_next`` above this — combined with
    "current is VERSE, next is CHORUS" — flips current to
    PRECHORUS. Conservative: weak ramps stay as VERSE."""

    breakdown_z_ceiling: float = -1.0
    """``drum_density_z`` below this → BREAKDOWN candidate. Applied
    to any non-edge section (INTRO/OUTRO are preserved)."""

    buildup_ramp_floor: float = 0.40
    """``energy_ramp_into_next`` above this — combined with
    "target section's refined type is CHORUS" — annotates the
    transition as ``type="buildup"``. Threshold is stricter than
    ``prechorus_ramp_floor`` because BUILDUP is the more
    visually-loaded annotation."""

    edge_energy_z_ceiling: float = -1.0
    """``energy_z`` below this — combined with "section is at the
    first or last position AND Stage A labelled it CHORUS" — demotes
    the edge to INTRO/OUTRO. Catches riff-uniform songs where H2
    sees ANCHOR everywhere and Stage A maps every section to CHORUS,
    but a clearly-lower-energy edge gives away the true intro/outro.
    One-sided: only demotes CHORUS at edges, never promotes."""


def refine_section_types(
    stage_a_types: Sequence[SectionType],
    aggregates: Sequence[SongFormAggregates],
    thresholds: SongFormThresholds = SongFormThresholds(),
) -> tuple[SectionType, ...]:
    """Refine Stage A labels using per-section song-form aggregates.

    Args:
        stage_a_types: Output of
            ``section_naming.derive_section_types``. One
            ``SectionType`` per section, in section order.
        aggregates: Output of
            ``song_form_aggregates.aggregate_song_form``. One
            ``SongFormAggregates`` per section, aligned 1-to-1
            with ``stage_a_types``.
        thresholds: Frozen tuning knobs.

    Returns:
        Tuple of ``SectionType`` values, aligned 1-to-1 with
        input. When ``aggregates`` is empty or its length disagrees
        with ``stage_a_types``, Stage A's labels are returned
        verbatim (defensive no-op — Stage B cannot fire without
        evidence).
    """
    n = len(stage_a_types)
    if n == 0:
        return ()
    if len(aggregates) != n:
        return tuple(stage_a_types)

    refined: list[SectionType] = list(stage_a_types)

    # Pass 0: edge-demotion — riff-uniform songs where H2 sees
    # ANCHOR everywhere and Stage A maps every section to CHORUS.
    # A first/last section whose energy_z drops clearly below the
    # song median is the true INTRO/OUTRO. Runs before the other
    # passes so that low-vocals edges (e.g. a quiet intro with
    # whisper-soft vocals) are demoted to INTRO before Pass 1
    # would otherwise re-classify them as INSTRUMENTAL.
    # One-sided: only demotes CHORUS at edges, never promotes.
    if n >= 2:
        if (
            refined[0] is SectionType.CHORUS
            and aggregates[0].energy_z < thresholds.edge_energy_z_ceiling
        ):
            refined[0] = SectionType.INTRO
        if (
            refined[n - 1] is SectionType.CHORUS
            and aggregates[n - 1].energy_z < thresholds.edge_energy_z_ceiling
        ):
            refined[n - 1] = SectionType.OUTRO

    # Pass 1: INSTRUMENTAL — CHORUS with low vocals.
    # Apply before PRECHORUS/BREAKDOWN so that later passes see the
    # refined types (PRECHORUS detection should not treat an
    # INSTRUMENTAL pass as a chorus to ramp into).
    #
    # Guard: only fires when at least one section in the song has
    # *some* vocal activity. When the vocals stem is absent entirely
    # (e.g. instrumental songs, or stems = [guitar, bass, drums] only),
    # ``aggregate_song_form`` returns 0.0 for every section. Without
    # this guard, every CHORUS would be flipped to INSTRUMENTAL on
    # no-vocals songs. Mirrors the no-drum-song guard inside
    # ``_robust_z_scores``.
    has_any_vocals = any(a.vocal_activity_score > 0.0 for a in aggregates)
    if has_any_vocals:
        for i in range(n):
            if refined[i] is SectionType.CHORUS:
                if aggregates[i].vocal_activity_score < thresholds.vocal_silence_ceiling:
                    refined[i] = SectionType.INSTRUMENTAL

    # Pass 2: PRECHORUS — VERSE immediately before a refined CHORUS,
    # with a strong energy ramp. INSTRUMENTAL chorus does not
    # qualify as the target (matches musical intuition).
    for i in range(n - 1):
        if refined[i] is not SectionType.VERSE:
            continue
        if refined[i + 1] is not SectionType.CHORUS:
            continue
        if aggregates[i].energy_ramp_into_next > thresholds.prechorus_ramp_floor:
            refined[i] = SectionType.PRECHORUS

    # Pass 3: BREAKDOWN — low drum density inside the song.
    # Edges (INTRO/OUTRO/INSTRUMENTAL at edges) are preserved.
    for i in range(n):
        if i == 0 or i == n - 1:
            continue
        if refined[i] in (SectionType.INTRO, SectionType.OUTRO):
            continue
        if aggregates[i].drum_density_z < thresholds.breakdown_z_ceiling:
            refined[i] = SectionType.BREAKDOWN

    return tuple(refined)


def annotate_transitions(
    transition_count: int,
    transitions_from_to: Sequence[tuple[int, int]],
    refined_types: Sequence[SectionType],
    aggregates: Sequence[SongFormAggregates],
    thresholds: SongFormThresholds = SongFormThresholds(),
) -> tuple[str | None, ...]:
    """Compute new ``type`` overrides for section transitions.

    Returns a tuple of new transition-type strings (or ``None`` for
    transitions whose type is unchanged), aligned 1-to-1 with the
    input transitions. The composition layer applies the overrides
    to its concrete transition objects (dict or
    ``SectionTransition`` — either works).

    Args:
        transition_count: Number of transitions in the song. Must
            equal ``len(transitions_from_to)``.
        transitions_from_to: Sequence of ``(from_section,
            to_section)`` index pairs, one per transition.
        refined_types: Output of ``refine_section_types``. Indexed
            by section number.
        aggregates: Output of ``aggregate_song_form``. Indexed by
            section number.
        thresholds: Frozen tuning knobs.

    Returns:
        Tuple of length ``transition_count``. Entry ``i`` is the
        new ``type`` string to assign to transition ``i``, or
        ``None`` if the transition is unchanged. Defensive no-op
        on shape mismatch.
    """
    if transition_count == 0:
        return ()
    if len(transitions_from_to) != transition_count:
        return tuple(None for _ in range(transition_count))
    if len(refined_types) != len(aggregates):
        return tuple(None for _ in range(transition_count))

    overrides: list[str | None] = []
    n_sections = len(refined_types)
    for from_idx, to_idx in transitions_from_to:
        if not (0 <= from_idx < n_sections):
            overrides.append(None)
            continue
        if not (0 <= to_idx < n_sections):
            overrides.append(None)
            continue
        if refined_types[to_idx] is not SectionType.CHORUS:
            overrides.append(None)
            continue
        if aggregates[from_idx].energy_ramp_into_next > thresholds.buildup_ramp_floor:
            overrides.append("buildup")
        else:
            overrides.append(None)
    return tuple(overrides)
