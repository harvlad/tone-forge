"""Post-analysis fixups applied to a persisted result dict on bundle read.

Legacy history entries were written before the Fix B / Fix C / Round-2
chord-detector work landed. Rather than gate JAM behind a re-analysis
of every session, this module re-applies those fixes to the persisted
``AnalysisResult`` dict shape at bundle-read time. Runs on every read
so any subsequent tightening of thresholds propagates to existing
bundles.

Boundary discipline:

* This module sits at the ``tone_forge`` top level rather than inside
  any subsystem so it can freely compose analysis + song_form. The
  session subsystem (``tone_forge.session.bundle``) may not import
  either directly — the boundary test in
  ``tests/test_subsystem_boundaries.py`` enforces that. Composition
  belongs in the API edge; this module is the composition unit.
* :func:`apply_bundle_read_fixups` mutates ``result`` in place and
  returns ``None``. Every stage is wrapped in a broad ``try/except``:
  a failure in any single fixup must not block bundle assembly.

Fixups applied, in order:
    1. ``flag_suspicious_durations`` (Fix B) on ``result["sections"]``
    2. ``resegment_flagged_sections`` + ``relabel_sections_from_h2``
       + re-flag durations (Fix C)
    3. ``detect_chord_vocab_boundaries``-driven section splits +
       re-flag durations (Round-2 Fix 4)
    4. ``collapse_same_root_regions`` on ``chords`` and
       ``chords_beat_snapped`` (Round-2 Fix 2)
    5. ``filter_chords_in_monophonic_sections`` on the same
       (Stage 1.6)
"""

from __future__ import annotations

from collections.abc import Mapping as _Mapping
from typing import Any, Optional

import numpy as np


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
        # their prior labels. Non-fatal by design.
        pass

    return sections


def apply_bundle_read_fixups(result: dict) -> None:
    """Apply legacy-bundle fixups to ``result`` in place.

    Called by the API composition layer (``tone_forge_api``) before
    handing the result dict to :func:`tone_forge.session.bundle.build`.
    All stages are best-effort: failures leave the affected field
    untouched so the bundle still assembles.
    """
    if not isinstance(result, dict):
        return

    raw_sections = result.get("sections")

    if isinstance(raw_sections, list):
        # Fix B — duration-guard on every read.
        try:
            from tone_forge.analysis.section_naming import (
                flag_suspicious_durations,
            )
            flag_suspicious_durations(raw_sections)
        except Exception:
            pass

        # Fix C — boundary re-detection inside flagged spans.
        try:
            from tone_forge.analysis.section_resegment import (
                resegment_flagged_sections,
            )
            midi_stems_blob = result.get("midi_stems") or {}
            resegmented = resegment_flagged_sections(
                raw_sections, midi_stems_blob
            )
            if len(resegmented) != len(raw_sections):
                raw_sections = resegmented
                result["sections"] = raw_sections
                raw_chords = result.get("chords")
                if isinstance(raw_chords, list) and raw_chords:
                    try:
                        relabel_sections_from_h2(raw_sections, raw_chords)
                    except Exception:
                        pass
                try:
                    from tone_forge.analysis.section_naming import (
                        flag_suspicious_durations,
                    )
                    flag_suspicious_durations(raw_sections)
                except Exception:
                    pass
        except Exception:
            pass

    # Round-2 Fix 4 — chord-vocabulary Jaccard boundary refinement.
    if (
        isinstance(raw_sections, list)
        and result.get("chords")
    ):
        try:
            from tone_forge.analysis.chord_vocab_boundaries import (
                detect_chord_vocab_boundaries,
            )
            _fix4_beats_raw = result.get("beats_s")
            _fix4_beats: Optional[np.ndarray] = None
            if isinstance(_fix4_beats_raw, (list, tuple)):
                try:
                    _fix4_beats = np.asarray(
                        [float(b) for b in _fix4_beats_raw],
                        dtype=np.float64,
                    )
                except Exception:
                    _fix4_beats = None
            if _fix4_beats is not None:
                new_boundaries = detect_chord_vocab_boundaries(
                    raw_sections, result["chords"], _fix4_beats,
                )
                if new_boundaries:
                    by_section: dict = {}
                    for row in new_boundaries:
                        by_section.setdefault(
                            int(row["source_section_index"]), []
                        ).append(float(row["time_s"]))
                    refined: list = []
                    for idx, section in enumerate(raw_sections):
                        splits = sorted(by_section.get(idx, []))
                        if not splits:
                            refined.append(section)
                            continue
                        s_start = float(section.get("start_time", 0.0))
                        s_end = float(section.get("end_time", 0.0))
                        edges = [s_start] + splits + [s_end]
                        for k in range(len(edges) - 1):
                            sub = dict(section)
                            sub["start_time"] = edges[k]
                            sub["end_time"] = edges[k + 1]
                            sub["duration"] = edges[k + 1] - edges[k]
                            refined.append(sub)
                    raw_sections = refined
                    result["sections"] = raw_sections
                    try:
                        from tone_forge.analysis.section_naming import (
                            flag_suspicious_durations,
                        )
                        flag_suspicious_durations(raw_sections)
                    except Exception:
                        pass
        except Exception:
            pass

    # Round-2 Fix 2 — same-root region collapse.
    raw_chords_list = result.get("chords")
    raw_chords_snapped_list = result.get("chords_beat_snapped")
    raw_beats_list = result.get("beats_s")
    _bundle_beats: Optional[np.ndarray] = None
    if isinstance(raw_beats_list, (list, tuple)):
        try:
            _bundle_beats = np.asarray(
                [float(b) for b in raw_beats_list], dtype=np.float64,
            )
        except Exception:
            _bundle_beats = None

    if isinstance(raw_chords_list, list) and raw_chords_list:
        try:
            from tone_forge.analysis.chords import (
                collapse_same_root_regions,
            )
            raw_chords_list = collapse_same_root_regions(
                raw_chords_list, _bundle_beats,
            )
            result["chords"] = raw_chords_list
            if isinstance(raw_chords_snapped_list, list):
                raw_chords_snapped_list = collapse_same_root_regions(
                    raw_chords_snapped_list, _bundle_beats,
                )
                result["chords_beat_snapped"] = raw_chords_snapped_list
        except Exception:
            pass

    # Stage 1.6 — monophonic-section chord gate.
    if isinstance(raw_sections, list) and isinstance(raw_chords_list, list):
        try:
            from tone_forge.analysis.chords import (
                filter_chords_in_monophonic_sections,
            )
            raw_chords_list = filter_chords_in_monophonic_sections(
                raw_chords_list, raw_sections,
            )
            result["chords"] = raw_chords_list
            if isinstance(raw_chords_snapped_list, list):
                raw_chords_snapped_list = (
                    filter_chords_in_monophonic_sections(
                        raw_chords_snapped_list, raw_sections,
                    )
                )
                result["chords_beat_snapped"] = raw_chords_snapped_list
        except Exception:
            pass
