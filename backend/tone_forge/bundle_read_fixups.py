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
from typing import Any, Optional, Sequence

import numpy as np


def _ensure_midi_stems_decoded(midi_stems: Any) -> Any:
    """Decode persisted base64 MIDI ``content`` into per-stem ``notes`` lists.

    Local-engine and unified-pipeline sessions persist per-stem MIDI as
    base64-encoded MIDI files under ``midi_stems[k]["content"]``. The
    ``notes`` array is decoded on demand by
    ``tone_forge_api._decode_midi_stems_for_payload`` at API response
    time — but ``apply_bundle_read_fixups`` runs BEFORE that decode
    (see the call order in ``tone_forge_api.py:2694-2728``), so at
    fixup time ``notes`` is absent on freshly-persisted bundles.

    Without decoding here:
    * ``resegment_flagged_sections`` (Fix C) finds no MIDI onsets and
      cannot detect novelty boundaries — the read-path Fix C becomes
      a silent no-op on local-engine bundles.
    * When Fix C or Fix 4 DO split a section (via other paths), the
      per-child ``recompute_section_features_for_child`` helper has
      no stem notes to compute against and cannot refresh
      ``debug_features``.

    Idempotent: stems that already carry a ``notes`` list pass through
    unchanged. Decode failures leave the stem intact (empty ``notes``)
    so downstream code that gates on ``if not isinstance(notes, list)``
    still degrades gracefully.

    Returns a new dict (does not mutate the input). Callers can safely
    swap ``result["midi_stems"] = _ensure_midi_stems_decoded(...)`` or
    hold the decoded dict local to the fixup layer.
    """
    if not isinstance(midi_stems, _Mapping):
        return midi_stems

    try:
        import base64
        import io
        try:
            import pretty_midi
        except Exception:
            pretty_midi = None
    except Exception:
        return midi_stems

    out: dict[str, Any] = {}
    for stem_key, stem in midi_stems.items():
        if not isinstance(stem, _Mapping):
            out[stem_key] = stem
            continue
        # Already decoded? Keep as-is.
        if isinstance(stem.get("notes"), list):
            out[stem_key] = dict(stem)
            continue

        stem_out = {k: v for k, v in stem.items()}
        content_b64 = stem.get("content")
        if (
            pretty_midi is None
            or not isinstance(content_b64, str)
            or not content_b64
        ):
            stem_out["notes"] = []
            out[stem_key] = stem_out
            continue
        try:
            raw = base64.b64decode(content_b64)
            pm = pretty_midi.PrettyMIDI(io.BytesIO(raw))
        except Exception:
            stem_out["notes"] = []
            out[stem_key] = stem_out
            continue

        notes_out: list[dict] = []
        for instrument in pm.instruments:
            if getattr(instrument, "is_drum", False):
                continue
            for n in instrument.notes:
                duration = float(n.end) - float(n.start)
                if duration <= 0:
                    continue
                notes_out.append({
                    "start": float(n.start),
                    "end": float(n.end),
                    "pitch": int(n.pitch),
                    "velocity": int(max(1, min(127, n.velocity))),
                })
        notes_out.sort(key=lambda x: (x["start"], x["pitch"]))
        stem_out["notes"] = notes_out
        out[stem_key] = stem_out
    return out


def _try_stage_b_from_debug_features(
    sections: list[dict],
    stage_a_types: Sequence[Any],
) -> Optional[tuple[Any, ...]]:
    """Attempt Stage B refinement using per-section ``debug_features``.

    ``ArrangementSection.debug_features`` (assigned by the analysis
    workers when guidance-mode features are computed — see
    ``local_engine/analysis_worker.py`` and ``unified_pipeline.py``)
    is a tuple of ``asdict``-serialised ``SectionFeatures`` rows, one
    per stem. That per-section snapshot survives ``dict(section)``
    copies made by ``resegment_flagged_sections`` and
    ``detect_chord_vocab_boundaries``, so we can reconstruct the
    per-stem feature mapping that ``aggregate_song_form`` requires
    even after Fix C / Fix 4 split boundaries.

    Returns ``None`` (Stage B abstains, caller falls back to Stage A
    only) when:

    * Any section lacks a non-empty ``debug_features`` field
      (legacy bundles written before the Plan B assignment landed).
    * Any row is missing ``stem_name``.
    * Per-stem row counts are misaligned across sections (a stem
      present on one section but absent on another would break the
      1-to-1 shape ``aggregate_song_form`` expects).

    On success returns the ``refine_section_types`` output aligned
    1-to-1 with ``sections``.
    """
    try:
        from tone_forge.analysis.song_form import (
            SongFormThresholds,
            refine_section_types,
        )
        from tone_forge.analysis.song_form_aggregates import (
            aggregate_song_form,
        )
    except Exception:
        return None

    per_stem: dict[str, list[dict]] = {}
    energy_means: list[float] = []
    for section in sections:
        raw_debug = section.get("debug_features")
        if not isinstance(raw_debug, (list, tuple)) or not raw_debug:
            return None
        try:
            energy_means.append(float(section.get("energy_mean", 0.0)))
        except (TypeError, ValueError):
            return None
        for row in raw_debug:
            if not isinstance(row, dict):
                return None
            name = str(row.get("stem_name", "")).lower()
            if not name:
                return None
            per_stem.setdefault(name, []).append(row)

    n = len(sections)
    for rows in per_stem.values():
        if len(rows) != n:
            # Ragged stem coverage — abstain rather than misalign
            # rows to section indices they don't belong to.
            return None

    aggregates = aggregate_song_form(per_stem, energy_means)
    if len(aggregates) != n:
        return None

    return refine_section_types(
        stage_a_types, aggregates, SongFormThresholds(),
    )


def relabel_sections_from_h2(
    sections: list[dict],
    chords: Any,
) -> list[dict]:
    """Re-run H2 role classification + Stage A/B labeling on ``sections``.

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

    Stage B refinement runs when every section carries a
    non-empty ``debug_features`` snapshot (per-stem
    ``SectionFeatures`` rows, populated by the analysis workers on
    bundle write). The snapshot survives the ``dict(section)``
    copies made by upstream section-splitters, so Stage B evidence
    stays attached to each sub-section after Fix C / Fix 4 boundary
    refinements. Legacy bundles written before the snapshot landed
    fall through to Stage A only — the same behaviour this helper
    had before.

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

        decisions = classify_roles(
            h2_result.per_section,
            h2_result.h2_sep,
            per_section_insufficient=h2_result.per_section_insufficient,
        )
        derived_types = derive_section_types(decisions)
        # Stage B rerun on the persisted per-section evidence. When
        # every section carries a ``debug_features`` snapshot the
        # helper returns refined types; otherwise it abstains and we
        # keep the Stage A output. This is what restores Pass 1
        # INSTRUMENTAL / Pass 4 VERSE / Pass 4b VERSE labels that
        # would otherwise be wiped by the ANCHOR-→-CHORUS Stage A
        # mapping every time Fix C / Fix 4 split boundaries above.
        stage_b_types = _try_stage_b_from_debug_features(
            sections, derived_types
        )
        final_types = (
            stage_b_types if stage_b_types is not None else derived_types
        )
        # Preserve-vs-overwrite gate for non-split sections. The
        # asymmetry follows the label ontology:
        #
        #   * ``_from_split`` sub-sections always take the fresh
        #     Stage A/B labels — their boundaries changed, so the
        #     write-time label is stale by construction.
        #
        #   * Untagged sections whose write-time type is
        #     ``"chorus"`` may still be refined by read-time Stage
        #     B (a fresh Pass 1 / 4 / 4b can flip CHORUS to
        #     INSTRUMENTAL / VERSE that write-time missed because
        #     it hadn't seen the enlarged section set).
        #
        #   * Untagged sections whose write-time type is anything
        #     else (BRIDGE, INSTRUMENTAL, VERSE, PRECHORUS,
        #     INTRO, OUTRO, BREAKDOWN, BUILDUP) are preserved.
        #     Those labels came from write-time evidence Stage A/B
        #     cannot recover from the enlarged section set — in
        #     particular BRIDGE requires H2 UNIQUE, which flips to
        #     ANCHOR on shared-progression songs once the section
        #     set is enlarged by Fix C / Fix 4 splits.
        #
        # Backward-compat: when NO section carries ``_from_split``
        # (legacy caller, unit tests), overwrite all — preserves
        # the pre-Plan-E contract.
        any_tagged = any(bool(s.get("_from_split")) for s in sections)
        for section, decision, st in zip(sections, decisions, final_types):
            if any_tagged and not section.get("_from_split"):
                prior_type = str(section.get("type", "")).lower()
                if prior_type != "chorus":
                    continue
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

    # Decode persisted MIDI stems into per-stem ``notes`` lists once
    # per call. ``apply_bundle_read_fixups`` runs BEFORE
    # ``_decode_midi_stems_for_payload`` in the API layer, so
    # freshly-persisted bundles only carry base64 ``content`` here.
    # Without decoding, ``resegment_flagged_sections`` (Fix C) and
    # the per-child feature recomputation both silently no-op on
    # local-engine bundles. Held local — we do NOT mutate
    # ``result["midi_stems"]`` because the API decode layer expects
    # to run against the persisted shape.
    _decoded_midi_stems = _ensure_midi_stems_decoded(
        result.get("midi_stems") or {}
    )

    # Convert persisted list-of-floats beats to an np array for
    # feature recomputation. Reused by Fix C and Fix 4 branches.
    _raw_beats = result.get("beats_s")
    _bundle_beats_arr: Optional[np.ndarray] = None
    if isinstance(_raw_beats, (list, tuple)):
        try:
            _bundle_beats_arr = np.asarray(
                [float(b) for b in _raw_beats], dtype=np.float64,
            )
        except Exception:
            _bundle_beats_arr = None

    # Same for the persisted energy curve. ``energy_curve_sr`` is
    # not persisted at the bundle top level; the analysis workers
    # emit at the 10.0 Hz default and unified pipeline mirrors it.
    _raw_energy = result.get("energy_curve")
    _bundle_energy_arr: Optional[np.ndarray] = None
    if isinstance(_raw_energy, (list, tuple)) and _raw_energy:
        try:
            _bundle_energy_arr = np.asarray(
                [float(e) for e in _raw_energy], dtype=np.float64,
            )
        except Exception:
            _bundle_energy_arr = None

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
            _fix_c_chords = result.get("chords")
            resegmented = resegment_flagged_sections(
                raw_sections,
                _decoded_midi_stems,
                chord_regions=(
                    _fix_c_chords
                    if isinstance(_fix_c_chords, list)
                    else None
                ),
                beats_s=_bundle_beats_arr,
                energy_curve=_bundle_energy_arr,
                energy_curve_sr=10.0,
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
            from tone_forge.analysis.section_features import (
                recompute_section_features_for_child,
            )
            from tone_forge.analysis.section_resegment import (
                _stem_notes_by_name,
            )
            # Reuse the beats array built once at the top of the
            # fixup layer. Fix 4 requires beats non-None to proceed
            # at all (the chord-vocab novelty detector is beat-
            # indexed); when the persisted bundle carries no beats
            # array we skip the whole pass silently.
            _fix4_beats = _bundle_beats_arr
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
                    # Per-stem notes for post-split feature
                    # recomputation. Built once for the whole Fix 4
                    # pass — matches the shape ``_split_section``
                    # uses inside ``resegment_flagged_sections`` so
                    # both split sites converge on the same source
                    # of truth for child aggregates.
                    _fix4_stem_notes = _stem_notes_by_name(
                        _decoded_midi_stems
                    )
                    _fix4_chords_ref = result.get("chords")
                    refined: list = []
                    for idx, section in enumerate(raw_sections):
                        splits = sorted(by_section.get(idx, []))
                        if not splits:
                            refined.append(section)
                            continue
                        s_start = float(section.get("start_time", 0.0))
                        s_end = float(section.get("end_time", 0.0))
                        edges = [s_start] + splits + [s_end]
                        # Extract parent stem_names order from the
                        # section's own debug_features so children
                        # keep the same per-stem row ordering. Fall
                        # back to no-recompute when the parent lacks
                        # the snapshot (legacy bundles).
                        parent_debug = section.get("debug_features")
                        parent_stem_names: list[str] = []
                        if isinstance(parent_debug, (list, tuple)):
                            for _row in parent_debug:
                                if isinstance(_row, dict):
                                    _nm = _row.get("stem_name")
                                    if isinstance(_nm, str) and _nm:
                                        parent_stem_names.append(_nm)
                        can_recompute = bool(
                            parent_stem_names
                            and isinstance(_fix4_chords_ref, list)
                        )
                        for k in range(len(edges) - 1):
                            sub = dict(section)
                            child_start = edges[k]
                            child_end = edges[k + 1]
                            sub["start_time"] = child_start
                            sub["end_time"] = child_end
                            sub["duration"] = child_end - child_start
                            # Tag as "needs read-path relabel" — see the
                            # matching flag set by
                            # ``section_resegment._split_section``. Fix
                            # 4's inline splitter has the same
                            # write-time-label-is-stale-on-child-
                            # boundaries invariant; the flag lets
                            # ``relabel_sections_from_h2`` preserve
                            # untouched originals while overwriting
                            # sub-sections here.
                            sub["_from_split"] = True
                            if can_recompute:
                                # Recompute per-child debug_features
                                # and energy_mean from primary
                                # sources. Without this the child
                                # inherits stale parent-window
                                # aggregates via ``dict(section)``
                                # above and Stage B's Pass 4 gate
                                # sees identical energy/vocal-
                                # activity numbers on every sub of
                                # the same parent (the stale-child-
                                # aggregate defect diagnosed on the
                                # Paramore case).
                                try:
                                    child_rows, child_energy = (
                                        recompute_section_features_for_child(
                                            child_start_s=child_start,
                                            child_end_s=child_end,
                                            stem_names=parent_stem_names,
                                            stem_notes_by_name=_fix4_stem_notes,
                                            chord_regions=_fix4_chords_ref,
                                            beats_s=_fix4_beats,
                                            energy_curve=_bundle_energy_arr,
                                            energy_curve_sr=10.0,
                                        )
                                    )
                                    sub["debug_features"] = child_rows
                                    if child_energy is not None:
                                        sub["energy_mean"] = child_energy
                                except Exception:
                                    # Recompute is best-effort; a
                                    # failure leaves the stale
                                    # parent aggregates in place
                                    # (current behavior, no
                                    # regression).
                                    pass
                            refined.append(sub)
                    raw_sections = refined
                    result["sections"] = raw_sections
                    # Re-run Stage A on the newly-subdivided boundaries.
                    # The subdivided sections inherit their parent's
                    # ``type`` / ``structural_role`` from ``dict(section)``
                    # above — those labels are stale relative to the new
                    # boundaries. Symmetric with the Fix C branch above,
                    # which also relabels after resegmenting.
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

    # Strip the internal ``_from_split`` tag before the result
    # dict leaves the fixup layer. The tag is an implementation
    # detail of ``relabel_sections_from_h2``'s preserve-vs-
    # overwrite gate; downstream serializers (session bundle,
    # JAM UI) never see it.
    if isinstance(raw_sections, list):
        for _s in raw_sections:
            if isinstance(_s, dict):
                _s.pop("_from_split", None)
