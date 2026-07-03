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
from statistics import median
from typing import Any, Iterable, Optional, Sequence

from tone_forge.analysis.sections import SectionType
from tone_forge.analysis.song_form_aggregates import SongFormAggregates


# Pitch-class letters — the leading tokens of a chord symbol.
# Used by the CHORUS→CHORUS PRECHORUS refinement (Pass 2b) to compare
# chord-root vocabularies without needing full chord parsing.
_ROOT_PC = {
    "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11,
}


def _chord_root_pc(symbol: str) -> Optional[int]:
    """Return the pitch class of a chord symbol's root (0-11) or None.

    Accepts standard symbols: ``C``, ``C#``, ``Db``, ``Am``, ``F#7``,
    ``G5``, ``Csus4``, etc. Enharmonic-equivalent roots collapse to
    the same pitch class so ``C#`` == ``Db``.
    """
    if not symbol:
        return None
    letter = symbol[0].upper()
    if letter not in _ROOT_PC:
        return None
    pc = _ROOT_PC[letter]
    if len(symbol) > 1:
        accidental = symbol[1]
        if accidental == "#":
            pc = (pc + 1) % 12
        elif accidental == "b":
            pc = (pc - 1) % 12
    return pc


def _extract_root_set(chord_symbols: Iterable[Any]) -> frozenset[int]:
    """Collect the pitch-class set of a section's chord roots."""
    roots: set[int] = set()
    for c in chord_symbols:
        if isinstance(c, str):
            symbol = c
        elif isinstance(c, dict):
            symbol = str(c.get("symbol") or "")
        else:
            symbol = str(getattr(c, "symbol", "") or "")
        pc = _chord_root_pc(symbol)
        if pc is not None:
            roots.add(pc)
    return frozenset(roots)


def _is_chord_vocab_subset(
    prev_symbols: Iterable[Any],
    next_symbols: Iterable[Any],
) -> bool:
    """Return True iff ``prev`` has a strictly-narrower chord-root
    vocabulary that is a subset of ``next``.

    Fires the CHORUS→CHORUS PRECHORUS rule (Pass 2b): a section whose
    chord-root set is a proper subset of the anchor's roots AND whose
    vocabulary is at most 60% the size of the anchor's is the classic
    pre-chorus "vamp narrowing into the chorus" pattern.
    """
    prev_roots = _extract_root_set(prev_symbols)
    next_roots = _extract_root_set(next_symbols)
    if not prev_roots or not next_roots:
        return False
    if not prev_roots.issubset(next_roots):
        return False
    if prev_roots == next_roots:
        return False
    return len(prev_roots) <= 0.6 * len(next_roots)


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

    verse_demotion_min_choruses: int = 4
    """Minimum count of Stage-A CHORUSes in the (post Pass 0-3)
    refined tuple before the CHORUS→VERSE demotion (Pass 4)
    considers firing. Below this the intra-CHORUS medians are too
    noisy to trust; abstain. Motivating case: pop-punk / folk /
    any genre where verse and chorus share the same chord
    progression, so H2 chord-trigram recurrence collapses to
    ANCHOR on every section and Stage A ships an all-CHORUS
    tuple."""

    verse_demotion_z_offset: float = 0.35
    """``energy_z`` must be at least this far below the
    intra-CHORUS median for Pass 4 demotion. 0.35 ≈ half a
    MAD-scaled standard deviation; picked so a chorus with a
    marginal energy dip stays CHORUS but a genuine verse
    (typically ~1 z below chorus median) crosses the line."""

    verse_demotion_vocal_ratio: float = 0.75
    """``vocal_activity_score`` must be below this fraction of the
    intra-CHORUS median for Pass 4 demotion. 0.75 gives real
    headroom for pop-punk verses (which are sung with similar
    intensity to the chorus) while still catching hushed /
    whispered / low-density verse vocals. Multiplicative rather
    than additive because ``vocal_activity_score`` is bounded on
    [0, 1] and the useful dynamic range collapses near the
    endpoints."""


def refine_section_types(
    stage_a_types: Sequence[SectionType],
    aggregates: Sequence[SongFormAggregates],
    thresholds: SongFormThresholds = SongFormThresholds(),
    chords_per_section: Optional[Sequence[Sequence[Any]]] = None,
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
        chords_per_section: Optional sequence of chord lists (one per
            section, aligned 1-to-1 with ``stage_a_types``). Each entry
            is a sequence of chord symbols (str), chord dicts with a
            ``symbol`` key, or ``Chord`` records. When provided, Pass
            2b (CHORUS→CHORUS vocab-narrow PRECHORUS) can fire; when
            None or shape-mismatched, Pass 2b is silently disabled.

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

    # Pass 2b: PRECHORUS — CHORUS→CHORUS with vocab narrowing + ramp.
    # Generalises Pass 2 to the Fix-C-split case: when boundary
    # re-detection splits one long CHORUS block into sub-sections and
    # H2 relabel gives all children the ANCHOR (→ CHORUS) label, the
    # classic pre-chorus vamp is still wearing the CHORUS jersey.
    # The rule fires when the first section's chord-root set is a
    # proper subset of the next section's AND the vocabulary is at
    # most 60% the size AND there is a rising energy ramp
    # (relaxed to 0.6× the Pass 2 floor since the anchor-child
    # signal is already strong evidence).
    #
    # Requires ``chords_per_section`` — silently disabled when None
    # or shape-mismatched.
    if (
        chords_per_section is not None
        and len(chords_per_section) == n
    ):
        ramp_floor_2b = thresholds.prechorus_ramp_floor * 0.6
        for i in range(n - 1):
            if refined[i] is not SectionType.CHORUS:
                continue
            if refined[i + 1] is not SectionType.CHORUS:
                continue
            if aggregates[i].energy_ramp_into_next <= ramp_floor_2b:
                continue
            if _is_chord_vocab_subset(
                chords_per_section[i], chords_per_section[i + 1]
            ):
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

    # Pass 4: CHORUS → VERSE — intra-CHORUS energy + vocal dip.
    #
    # For songs where H2 chord-trigram recurrence cannot separate
    # verse from chorus (they share the same progression — see
    # pop-punk, folk, many pop songs), Stage A ships an all-CHORUS
    # tuple. Passes 0-3 catch a handful of these cases at the
    # edges (INTRO/OUTRO), when vocals disappear (INSTRUMENTAL),
    # when drums drop out (BREAKDOWN), or when a chord-vocab
    # narrowing pattern is visible (PRECHORUS). The remaining
    # verses are indistinguishable from choruses by H2 or chord
    # alone; the only signal left is energy + vocal density.
    #
    # A CHORUS is demoted to VERSE when *both* ``energy_z`` and
    # ``vocal_activity_score`` fall meaningfully below the median
    # of the surviving CHORUSes. Both signals are required so
    # that:
    #   * a chorus with a quiet variant doesn't get demoted on
    #     energy alone (still sung at full intensity);
    #   * an instrumental break Pass 1 missed doesn't get demoted
    #     on vocals alone (still full energy).
    #
    # Runs *after* the other passes so it operates on the final
    # refined types (not touching sections that Pass 0/1/3
    # already reclassified). One-sided: never promotes VERSE to
    # CHORUS. Preserves at least one CHORUS: pathological songs
    # (say, an instrumental where every CHORUS is low-energy)
    # keep the highest-signal survivor.
    _pass_4_chorus_to_verse(refined, aggregates, thresholds)

    return tuple(refined)


def _pass_4_chorus_to_verse(
    refined: list[SectionType],
    aggregates: Sequence[SongFormAggregates],
    thresholds: SongFormThresholds,
) -> None:
    """Mutate ``refined`` in place: demote low-energy + low-vocal
    CHORUSes to VERSE.

    See the Pass 4 comment in ``refine_section_types`` for the
    signal rationale. Extracted as a standalone helper so it's
    unit-testable and the pass ordering in the caller reads as a
    single grep-able list.
    """
    chorus_indices = [
        i for i, t in enumerate(refined) if t is SectionType.CHORUS
    ]
    if len(chorus_indices) < thresholds.verse_demotion_min_choruses:
        return

    chorus_energy_zs = [aggregates[i].energy_z for i in chorus_indices]
    chorus_vocals = [
        aggregates[i].vocal_activity_score for i in chorus_indices
    ]
    median_z = median(chorus_energy_zs)
    median_vocals = median(chorus_vocals)

    # Rank candidates from most demotion-worthy (lowest combined
    # signal) to least. Deterministic; ties resolve by index
    # (Python's sort is stable).
    candidates = sorted(
        chorus_indices,
        key=lambda i: (
            aggregates[i].energy_z + aggregates[i].vocal_activity_score
        ),
    )

    for i in candidates:
        agg = aggregates[i]
        if agg.energy_z >= median_z - thresholds.verse_demotion_z_offset:
            continue
        if (
            agg.vocal_activity_score
            >= median_vocals * thresholds.verse_demotion_vocal_ratio
        ):
            continue
        # Sanity: keep at least one CHORUS in the song. Prevents
        # pathological all-low-signal songs from ending up
        # VERSE-only.
        remaining_choruses = sum(
            1 for t in refined if t is SectionType.CHORUS
        )
        if remaining_choruses <= 1:
            break
        refined[i] = SectionType.VERSE


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
