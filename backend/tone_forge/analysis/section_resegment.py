"""Boundary re-detection inside long ANCHOR sections (Fix C).

Complements the duration-guard post-pass (``section_naming.
flag_suspicious_durations``, Fix B) which flags sections whose
duration is structurally implausible for their label. Fix B tells
the user "this boundary is probably wrong"; Fix C actually inserts
the missing boundaries.

## Why this exists

The Stage-0 RMS-novelty boundary detector
(``analysis/sections.py:SectionDetector._detect_boundaries``) under-
segments songs whose chorus riff runs over multiple structurally
distinct sections. Reference case: session ``c3687f79`` (Linkin Park
— "One Step Closer") where a 70s "CHORUS" block spans
verse2+prechorus2+chorus2+bridge because the mixed-RMS shape barely
changes across those boundaries.

The signal that DOES change at those boundaries lives in the
per-stem MIDI onset patterns — drums add crashes at chorus entry,
bass shifts pitch center, guitar riffs cycle. This module computes
a combined MIDI-onset density novelty function inside each flagged
span and splits at internal peaks.

## Design decisions

* **Signal:** combined MIDI onset density across all available
  stems (vocals-only would be stronger but c3687f79 lacks a vocals
  stem; combined onset density falls back gracefully to whatever
  stems the pipeline extracted).
* **Bin size:** 0.5s (fine enough to localise a boundary to a
  half-second, coarse enough to smooth per-note jitter).
* **Smoothing:** 4s moving average (dampens per-beat wobble while
  preserving section-scale shape changes).
* **Threshold:** ``mean + 0.5*std`` of the novelty function (looser
  than the baseline detector's ``mean + std`` because we've already
  established via Fix B that this span is structurally suspect —
  we're actively looking for weak signals the primary pass missed).
* **Min sub-duration:** 6s (same as Fix B's ``min_section_s``
  fragment threshold; sub-sections shorter than this get merged
  with a neighbour).

Determinism: pure over inputs, stdlib + numpy only, no I/O, no RNG.
"""

from __future__ import annotations

from collections.abc import Mapping as _Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ResegmentThresholds:
    """Tuning surface for boundary re-detection.

    Defaults tuned against the c3687f79 reference case. Prog / jam-
    band forms with legitimately long chorus blocks may want a
    higher ``novelty_std_multiplier`` to avoid false-positive
    splits.
    """

    bin_size_s: float = 0.5
    """Onset-density bin width in seconds."""

    smoothing_window_s: float = 8.0
    """Moving-average window applied to per-bin density before
    computing the novelty function. 8s = 2 bars at 120 BPM; large
    enough to dampen per-beat riff jitter, small enough to preserve
    section-scale shape changes (verse→prechorus transitions
    typically span 1-2 bars). Was 4s in the initial draft; 8s tuned
    against the c3687f79 reference case to avoid over-splitting."""

    novelty_std_multiplier: float = 1.0
    """Threshold multiplier on the novelty function's standard
    deviation. The baseline detector uses 1.0 on the *mixed* RMS
    signal (where inter-section variance is compressed by the
    dominant chorus riff); here we apply 1.0 to the per-stem MIDI
    onset density (which has much higher inter-section variance in
    the flagged spans by construction — Fix B only flags sections
    the mixed-RMS pass got wrong). Tuned to 1.0 against c3687f79:
    lower values (0.5) over-split the 70s block into 7 chunks when
    the true internal structure is ~3-4 sub-sections."""

    min_sub_duration_s: float = 10.0
    """Minimum duration for a sub-section produced by a split. Set
    above Fix B's ``min_section_s`` fragment threshold (6.0s) with
    margin — we don't want a split to produce children that
    immediately trip the fragment flag, and typical verse/prechorus/
    chorus sub-sections in pop/rock are ≥10s."""

    max_boundaries: int = 4
    """Cap on how many boundaries a single split can produce. Keeps
    a run-away novelty function from carving a real (long) chorus
    into 8+ tiny fragments. Boundaries are ranked by novelty peak
    height and the top-N kept."""

    min_notes_for_signal: int = 12
    """A flagged span with fewer than this many total MIDI onsets
    across all stems has too little signal to trust; skip splitting
    and leave the ``duration_flag`` in place so the UI still shows
    the warning."""


def _iter_stem_notes(midi_stems: Any) -> list[dict]:
    """Flatten all stems' note dicts into a single time-sorted list.

    Accepts either the pipeline shape
    (``midi_stems[k]["notes"] = [{start, end, pitch, ...}, ...]``)
    or the API-decoded shape (identical after
    ``tone_forge_api._decode_midi_stems_for_payload``). Silently
    skips stems that lack a ``notes`` list.
    """
    if not isinstance(midi_stems, _Mapping):
        return []
    out: list[dict] = []
    for stem_name, stem_data in midi_stems.items():
        if not isinstance(stem_data, _Mapping):
            continue
        notes = stem_data.get("notes")
        if not isinstance(notes, list):
            continue
        for n in notes:
            if not isinstance(n, _Mapping):
                continue
            start = n.get("start")
            if start is None:
                continue
            try:
                out.append({"start": float(start), "stem": stem_name})
            except (TypeError, ValueError):
                continue
    out.sort(key=lambda x: x["start"])
    return out


def _find_novelty_boundaries(
    section_start_s: float,
    section_end_s: float,
    all_onsets: list[dict],
    thresholds: ResegmentThresholds,
) -> list[float]:
    """Return absolute-time boundary candidates inside a section span.

    Empty list means "no boundaries found" — the caller should leave
    the section unsplit.
    """
    duration = section_end_s - section_start_s
    if duration <= 2 * thresholds.min_sub_duration_s:
        # Too short to meaningfully split.
        return []

    # Collect onsets inside the window.
    inside = [
        o for o in all_onsets
        if section_start_s <= o["start"] < section_end_s
    ]
    if len(inside) < thresholds.min_notes_for_signal:
        return []

    # Bin onsets by time.
    n_bins = max(1, int(np.ceil(duration / thresholds.bin_size_s)))
    density = np.zeros(n_bins, dtype=float)
    for o in inside:
        idx = int((o["start"] - section_start_s) / thresholds.bin_size_s)
        if 0 <= idx < n_bins:
            density[idx] += 1.0

    # Smooth with moving average.
    smooth_bins = max(1, int(thresholds.smoothing_window_s / thresholds.bin_size_s))
    if len(density) > smooth_bins:
        kernel = np.ones(smooth_bins) / smooth_bins
        density_smooth = np.convolve(density, kernel, mode="same")
    else:
        density_smooth = density

    # Novelty = absolute first difference.
    novelty = np.abs(np.diff(density_smooth))
    if len(novelty) < 3:
        return []

    # Threshold: mean + k*std.
    threshold = float(np.mean(novelty) + thresholds.novelty_std_multiplier * np.std(novelty))
    if not np.isfinite(threshold):
        return []

    # Find local peaks above threshold, enforcing min-spacing.
    min_spacing_bins = int(thresholds.min_sub_duration_s / thresholds.bin_size_s)
    peaks: list[int] = []
    for i in range(1, len(novelty) - 1):
        if novelty[i] < threshold:
            continue
        if novelty[i] <= novelty[i - 1] or novelty[i] <= novelty[i + 1]:
            continue
        if peaks and (i - peaks[-1]) < min_spacing_bins:
            # Keep the stronger peak of the pair.
            if novelty[i] > novelty[peaks[-1]]:
                peaks[-1] = i
            continue
        peaks.append(i)

    # Cap: rank by peak height and keep only the top ``max_boundaries``.
    # Preserves min-spacing property because we're subsetting from a
    # list that was already spacing-filtered above.
    if len(peaks) > thresholds.max_boundaries:
        peaks_by_height = sorted(peaks, key=lambda i: -float(novelty[i]))
        top = set(peaks_by_height[: thresholds.max_boundaries])
        peaks = [i for i in peaks if i in top]

    # Convert bins → absolute time. Novelty index i corresponds to
    # the boundary between density_smooth[i] and density_smooth[i+1],
    # which is roughly at (i+1)*bin_size after section_start_s.
    boundaries = [
        section_start_s + (i + 1) * thresholds.bin_size_s
        for i in peaks
    ]

    # Guardrail: drop candidates too close to the section edges so
    # a split can't produce a sub-section shorter than
    # min_sub_duration_s at either end.
    boundaries = [
        b for b in boundaries
        if (b - section_start_s) >= thresholds.min_sub_duration_s
        and (section_end_s - b) >= thresholds.min_sub_duration_s
    ]

    return boundaries


def _split_section(
    section: dict,
    boundaries: list[float],
) -> list[dict]:
    """Split one section into N+1 children at the given boundary times.

    Each child inherits the parent's ``type`` (Stage A re-labeling
    happens at the caller). The parent's ``duration_flag`` is cleared
    on the children — the split has (probably) fixed the underlying
    boundary miss; if a child is still too long, the guard will re-
    flag it on the next post-pass.

    Non-duration fields (guidance_mode, dominant_stem, landmark_notes,
    debug_features, structural_role, energy metrics) are copied
    verbatim to each child. They're now approximate for the child
    span rather than exact — accepted trade-off because re-computing
    them here would require re-invoking guidance_mode + section
    features from inside this module, which would break the
    determinism contract (numpy + dict manipulation only).
    """
    if not boundaries:
        return [section]

    start = float(section.get("start_time", 0.0))
    end = float(section.get("end_time", 0.0))
    all_bounds = [start] + sorted(boundaries) + [end]

    children: list[dict] = []
    for i in range(len(all_bounds) - 1):
        child = dict(section)
        child["start_time"] = float(all_bounds[i])
        child["end_time"] = float(all_bounds[i + 1])
        child["duration_flag"] = ""
        # Landmark notes are section-scoped; recomputing them here
        # would need the stem MIDI. As a light patch, filter the
        # parent's landmark_notes to those falling inside the child
        # window so the JAM lead-lane doesn't render notes from a
        # neighbouring sub-section.
        parent_lm = section.get("landmark_notes")
        if isinstance(parent_lm, list):
            child["landmark_notes"] = [
                n for n in parent_lm
                if isinstance(n, _Mapping)
                and child["start_time"] <= float(n.get("start", -1)) < child["end_time"]
            ]
        children.append(child)
    return children


def resegment_flagged_sections(
    sections: list[dict],
    midi_stems: Any,
    thresholds: ResegmentThresholds = ResegmentThresholds(),
) -> list[dict]:
    """Return a new sections list where flagged spans have been split.

    Non-flagged sections pass through unchanged (identity, not a
    copy — callers who mutate the returned list will affect the
    input). Flagged spans get their internal boundaries detected via
    combined MIDI-onset novelty; if the detector finds one or more
    boundaries, the parent is replaced with N+1 children.

    The following ``duration_flag`` values trigger split attempts:
        - ``"chorus_too_long"``
        - ``"prechorus_too_long"``
        - ``"verse_too_long"``
        - ``"bridge_too_long"``

    ``"fragment"`` and ``""`` are left untouched — a fragment is
    already too short to split further, and unflagged sections
    aren't candidates.

    When the novelty function produces zero boundary candidates
    (weak signal / homogeneous span), the parent stays in place with
    its ``duration_flag`` intact so the UI still shows the warning.

    Determinism: pure over inputs.
    """
    if not sections:
        return sections

    SPLIT_FLAGS = {
        "chorus_too_long",
        "prechorus_too_long",
        "verse_too_long",
        "bridge_too_long",
    }

    all_onsets = _iter_stem_notes(midi_stems)

    out: list[dict] = []
    for section in sections:
        if not isinstance(section, dict):
            out.append(section)
            continue
        flag = str(section.get("duration_flag", ""))
        if flag not in SPLIT_FLAGS:
            out.append(section)
            continue
        try:
            start = float(section.get("start_time", 0.0))
            end = float(section.get("end_time", 0.0))
        except (TypeError, ValueError):
            out.append(section)
            continue
        boundaries = _find_novelty_boundaries(
            start, end, all_onsets, thresholds
        )
        if not boundaries:
            out.append(section)
            continue
        out.extend(_split_section(section, boundaries))

    return out


def relabel_sections_from_h2(
    sections: list[dict],
    chords: Any,
) -> list[dict]:
    """Re-run H2 role classification + Stage A labeling on ``sections``.

    Intended for use after :func:`resegment_flagged_sections` splits a
    long ANCHOR block: the children inherit the parent's ``type``
    (e.g. all four sub-sections of a split 70s chorus land as
    ``"chorus"``) even though the true structure is often
    verse2/prechorus2/chorus2/bridge. Re-running the H2 pipeline on
    the new segmentation lets each sub-section pick up its own
    structural role from its chord-trigram vector.

    Args:
        sections: List of section dicts. Each entry needs
            ``start_time``/``end_time`` (pipeline shape) or
            ``start_s``/``end_s`` (H2 spec §2 shape). Mutated in
            place — ``type``, ``structural_role`` and
            ``structural_confidence`` are overwritten when H2
            produces a usable result.
        chords: Iterable of chord dicts with ``start_s``/``end_s``.
            Accepts either the pipeline-native list-of-dicts shape or
            the bundle-projection list-of-Chord-contract-objects (the
            H2 extractor duck-types on ``["start_s"]`` / ``["end_s"]``
            access which both shapes support).

    Returns:
        The same ``sections`` list (mutation is in place; return value
        is a convenience for chaining).

    Failure modes are non-fatal: any exception in the H2 pipeline
    leaves sections with their pre-call labels intact. Empty inputs
    or a degenerate H2 result (short songs, missing chord data) also
    no-op.

    Stage B (per-stem aggregate refinement) is deliberately not run
    here — its inputs are keyed by the original section indices and
    become misaligned after splitting. Stage B re-fitting is
    out-of-scope for this helper; callers who need it should re-
    compute ``per_stem_features_by_stem`` against the new segmentation
    upstream.

    Determinism: pure over inputs (given determinism of ``extract_h2``
    and ``classify_roles``).
    """
    if not sections or not chords:
        return sections
    try:
        from tone_forge.song_form import classify_roles, extract_h2
        from tone_forge.analysis.section_naming import derive_section_types
    except Exception:
        return sections

    try:
        # Normalise chords to a list of dicts. The H2 extractor reads
        # via ``chord["start_s"]`` / ``chord["end_s"]`` which works
        # for both plain dicts and dataclass instances (via
        # __getitem__ on the dataclass, or by pre-converting here).
        chord_list: list = []
        for c in chords:
            if isinstance(c, _Mapping):
                chord_list.append(c)
                continue
            # Dataclass fallback: use its asdict-like access.
            start = getattr(c, "start_s", None)
            end = getattr(c, "end_s", None)
            symbol = getattr(c, "symbol", None)
            if start is None or end is None:
                continue
            chord_list.append({
                "start_s": float(start),
                "end_s": float(end),
                "symbol": symbol,
            })
        if not chord_list:
            return sections

        h2_bundle = {"chords": chord_list, "sections": sections}
        h2_result = extract_h2(h2_bundle)
        if h2_result.degenerate:
            return sections
        if len(h2_result.per_section) != len(sections):
            return sections

        decisions = classify_roles(h2_result.per_section, h2_result.h2_sep)
        derived_types = derive_section_types(decisions)
        for section, decision, st in zip(sections, decisions, derived_types):
            section["structural_role"] = decision.role
            section["structural_confidence"] = float(decision.confidence)
            section["type"] = st.value
    except Exception:
        # Any failure inside the H2 pipeline leaves the sections with
        # their prior labels (either upstream Stage A/B output for
        # fresh analyses, or the parent-inherited label from the Fix
        # C split for legacy bundles). Non-fatal by design.
        pass

    return sections
