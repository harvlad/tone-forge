"""Boundary-friendly chord detection for the analysis subsystem.

This is the public entry point that other subsystems (specifically
``session`` and ``guidance``) consume via composition. It wraps the
internal librosa-based ``chord_detector`` and emits the platform
``contracts.Chord`` shape so callers never see the internal dataclass.

Spike results (``backend/scripts/chord_spike.py``, re-run from
this docstring): the current detector averages ~96% **root-only**
accuracy across five synthetic guitar-style progressions but only
~58% strict/triad-relaxed. The strict gap is over-segmentation:
the Viterbi state sequence emits short quality-mislabel slivers
between long correct regions, so root identity is solid while
the precise quality flickers on the boundaries. Dom7 is no longer
the dominant failure mode — ``I-IV-V7-I`` now surfaces 5 regions
(truth 4) with G7 detected as a distinct region, same over-
segmentation shape as the all-triad progressions. The cached
``backend/scripts/chord_spike_report.json`` predates the Viterbi
rebuild (commit ``8f8df6b``) and reports the older, less-over-
segmented averages; re-run the spike to refresh.

Good enough for the Jam chord lane; not a research project.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np

from tone_forge.contracts import Chord
from tone_forge.analysis import chord_detector as _internal

__all__ = [
    "detect_chords",
    "detect_chords_with_key",
    "snap_chord_boundaries_to_beats",
    "enforce_min_hold",
    "filter_chords_in_monophonic_sections",
    "collapse_same_root_regions",
]


# ---------------------------------------------------------------------------
# Root parsing helpers (Round-2 Fix 2)
# ---------------------------------------------------------------------------

_PITCH_CLASS_BY_LETTER = {
    "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11,
}


def _chord_root_pc(symbol: str) -> Optional[int]:
    """Return the pitch class (0-11) of the chord's root, or None.

    Parses only the leading root token — ``C``, ``C#``, ``Db``, etc.
    Everything after the accidental (quality, extension, bass slash) is
    ignored. Returns ``None`` for empty strings, no-chord tokens (``N``,
    ``NC``, ``N.C.``, ``-``), or malformed inputs.

    Enharmonics collapse: ``C#`` and ``Db`` both map to pitch class 1;
    ``F#`` and ``Gb`` both map to 6, etc. This is the invariant Round-2
    Fix 2 relies on: same-root collapse fires on chroma-ambiguous
    quality flicker within a stable pitch class, so we must not
    accidentally split ``C#5 → Db5`` if the up-stream label writer ever
    switches spelling mid-song.
    """
    if not symbol:
        return None
    s = symbol.strip()
    if not s or s.upper() in ("N", "NC", "N.C.", "-"):
        return None
    letter = s[0].upper()
    if letter not in _PITCH_CLASS_BY_LETTER:
        return None
    pc = _PITCH_CLASS_BY_LETTER[letter]
    if len(s) >= 2:
        acc = s[1]
        if acc == "#":
            pc = (pc + 1) % 12
        elif acc == "b":
            pc = (pc - 1) % 12
    return pc


def detect_chords(
    audio: np.ndarray,
    sr: int,
    *,
    min_chord_duration_s: float = 0.5,
    bass_audio: Optional[np.ndarray] = None,
    beats_s: Optional[np.ndarray] = None,
) -> Tuple[Chord, ...]:
    """Detect chords in ``audio`` and return ``contracts.Chord`` records.

    Args:
        audio: Mono audio samples (any range; librosa-compatible).
        sr: Sample rate in Hz.
        min_chord_duration_s: Drop chord regions shorter than this. The
            spike used 0.3s; default here is 0.5s to favor stable
            regions in the Jam chord lane.
        bass_audio: Optional mono bass-stem samples at the same sample
            rate. When supplied, the detector biases its emission
            scores toward chord templates whose root matches the
            per-window bass pitch class extracted via pyin. This is the
            Phase 5 disambiguation pathway for relative-major/minor
            pairs the chroma matcher alone cannot separate.
        beats_s: Optional beat timestamps in seconds (from
            ``librosa.beat.beat_track``). When supplied, the detector
            replaces its fixed-0.5s analysis grid with beat-aligned
            windows so chord-region boundaries land on musical beats
            rather than on an arbitrary clock subdivision (Phase 6).

    Returns:
        A tuple of ``contracts.Chord`` ordered by ``start_s``.
    """
    raw = _internal.detect_chords_from_audio(
        audio, sr,
        min_chord_duration=min_chord_duration_s,
        bass_y=bass_audio,
        beats_s=beats_s,
    )
    chords = tuple(
        Chord(
            start_s=float(c.start_time),
            end_s=float(c.end_time),
            symbol=c.name,
            confidence=float(c.confidence),
        )
        for c in raw
    )
    # Round-2 Fix 2 — collapse same-root quality flicker on per-stem
    # chord lanes too. Uniform application: chroma-ambiguity artefacts
    # look the same on every stem, and mixed-stem chord lanes benefit
    # from the same stability pass ``detect_chords_with_key`` applies
    # to the primary "other" lane. No-op when beats_s is missing so
    # the max-span guard has no scale.
    chords = collapse_same_root_regions(chords, beats_s)
    return chords


def detect_chords_with_key(
    audio: np.ndarray,
    sr: int,
    *,
    min_chord_duration_s: float = 0.5,
    bass_audio: Optional[np.ndarray] = None,
    beats_s: Optional[np.ndarray] = None,
) -> Tuple[Tuple[Chord, ...], Dict[str, Any]]:
    """Detect chords AND surface the underlying key decision.

    Behaviourally identical to ``detect_chords`` for chord output. The
    second return value is a small dict the chord_detector populates
    in-place describing the post-tie-break key:

        {"root": int (0-11), "mode": "major"|"minor",
         "strength": float (0-1), "label": "F minor"}

    Empty dict on degenerate input (no chroma, all-zero audio): the
    chord_detector then falls back to its silent-defaults path and
    never writes into ``key_out``.

    Wired by ``unified_pipeline._detect_chord_lane`` so the key
    surfaces in the persisted AnalysisResult dict (same defensibility
    pattern as the Phase-7 tempo/beats hoist). Direct chord_detector
    callers that don't need the key keep using ``detect_chords``.

    Runs the default ``DetectorConfig`` — the same configuration the
    bench corpus pins its regression floors against. The former
    chord-lane stage tuning (self-loop bonus, power-chord priors) was
    removed after ``scripts.analysis_eval`` ablation showed it costs
    up to 35 WCSR points on ground-truth fixtures; see the inline
    comment below for the measured table.
    """
    from tone_forge.analysis.detector_config import DetectorConfig

    # Hardening 2026-07 — chord-lane config reverted to the bench
    # default. The previous stage tuning (self_loop_bonus=0.03 plus
    # the power-chord emission/post-Viterbi levers) was measured
    # against the ground-truth corpus via ``scripts.analysis_eval``
    # ablation and LOSES badly on triad-relaxed WCSR:
    #
    #   fixture             default   selfloop03   full stage cfg
    #   lets_make_it_pain    0.9998     0.6470        0.6470
    #   demolition_warning   0.9337     0.8527        0.8088
    #
    # The self-loop bonus made the decoder hold *wrong* chords
    # through genuine changes (WCSR does not penalise same-label
    # over-segmentation, so the "stability" rationale never showed
    # up in scores — only the mislabels did). Quality flicker is
    # already handled post-hoc by ``collapse_same_root_regions``.
    # One config everywhere: production lane now scores exactly what
    # the bench regression floors ratchet against.
    _stage_config = DetectorConfig()

    key_out: Dict[str, Any] = {}
    raw = _internal.detect_chords_from_audio(
        audio, sr,
        min_chord_duration=min_chord_duration_s,
        bass_y=bass_audio,
        beats_s=beats_s,
        config=_stage_config,
        key_out=key_out,
    )
    chords = tuple(
        Chord(
            start_s=float(c.start_time),
            end_s=float(c.end_time),
            symbol=c.name,
            confidence=float(c.confidence),
        )
        for c in raw
    )
    # Stage 1.5 — harmonic-rhythm min-hold. Absorb sub-beat flickers
    # into the higher-confidence neighbour so the chord stream reads
    # at ~1 chord per bar on typical rock/pop rather than flipping
    # every 0.5-1s window. No-op when beats_s is missing.
    chords = enforce_min_hold(chords, beats_s, min_beats=1.0)
    # Round-2 Fix 2 — same-root region collapse. Absorb quality
    # flicker within a stable root pitch class (C#m ↔ C#5 flip-flop
    # is the classic distorted-rock artefact where chroma ambiguity
    # on the 3rd bin causes the Viterbi to alternate every window or
    # two). Max span guard prevents flattening legitimate long
    # progressions (Cmaj → Cmaj7 → C7 vamps). No-op when beats_s is
    # missing so the max-span guard has no beat scale.
    chords = collapse_same_root_regions(chords, beats_s)
    return chords, key_out


def snap_chord_boundaries_to_beats(
    chords: Tuple[Chord, ...],
    beats_s: Optional[np.ndarray],
    song_dur_s: float,
) -> Tuple[Chord, ...]:
    """Return ``chords`` with each region's start/end snapped to nearest beat.

    Phase 6 (hybrid grid). The detector emits regions on a fixed 0.5s
    grid because beat-driven chroma aggregation regressed WCSR
    (longer-averaged chroma loses discriminability — see the
    chord_detector phase-progression doc block). This post-processing
    pass moves boundary timestamps to the nearest musical beat so the
    Jam ribbon visually aligns to the rhythm, without disturbing the
    chord labels themselves.

    The toggle exists so the UI can switch between the
    higher-WCSR-precision view (no snap) and the visually-aligned
    view (snap on). Both arrays are computed once at analysis time;
    the toggle is a render-time choice.

    Args:
        chords: Detector output, ordered by ``start_s``, contiguous
            (no gaps), no overlaps.
        beats_s: Beat timestamps in seconds. None or fewer-than-2
            entries returns ``chords`` unchanged.
        song_dur_s: Song duration in seconds, used as the snap target
            for the very last region's end_time.

    Returns:
        Tuple of ``Chord`` with snapped timestamps. Length may be
        equal to or less than input length (regions that collapsed to
        zero duration after snap are dropped). Contiguity is
        preserved: each region's start equals the previous region's
        end.
    """
    if beats_s is None or len(chords) < 2:
        return chords
    beats_arr = np.asarray(beats_s, dtype=np.float64)
    if beats_arr.ndim != 1 or beats_arr.size < 2:
        return chords

    # Snap targets include song start and end so first/last region
    # boundaries have endpoints to land on outside the beat range.
    snap_targets = np.unique(np.concatenate((
        [0.0], beats_arr, [float(song_dur_s)],
    )))

    def _snap(t: float) -> float:
        return float(snap_targets[int(np.argmin(np.abs(snap_targets - t)))])

    snapped_starts = [_snap(c.start_s) for c in chords]
    snapped_ends = [_snap(c.end_s) for c in chords]

    # Force contiguity: a region's start equals the previous region's
    # snapped end. Pin the first start and last end to the original
    # values so the song's overall span is preserved.
    snapped_starts[0] = float(chords[0].start_s)
    snapped_ends[-1] = float(chords[-1].end_s)
    for i in range(1, len(chords)):
        snapped_starts[i] = snapped_ends[i - 1]

    return tuple(
        Chord(
            start_s=snapped_starts[i],
            end_s=snapped_ends[i],
            symbol=chords[i].symbol,
            confidence=chords[i].confidence,
        )
        for i in range(len(chords))
        if snapped_ends[i] > snapped_starts[i]
    )


def enforce_min_hold(
    chords: Tuple[Chord, ...],
    beats_s: Optional[np.ndarray],
    min_beats: float = 1.0,
) -> Tuple[Chord, ...]:
    """Merge chord regions shorter than ``min_beats`` beats into a neighbour.

    Stage 1.5 — harmonic-rhythm min-hold. Even with the raised
    ``self_loop_bonus`` (Fix 2A), noisy distorted-guitar chroma can
    still produce sub-beat flickers between two long stable regions.
    This post-process walks the emitted chord sequence and absorbs
    any region whose length is below ``min_beats * median_beat_dur``
    into the higher-confidence adjacent region.

    The invariant preserved by this pass is *span contiguity*:
    surviving regions cover the full input span with no gaps because
    absorbing extends the winning neighbour's start_s/end_s to swallow
    the short region.

    Args:
        chords: Detector output (ideally after beat snap), ordered by
            ``start_s``, contiguous.
        beats_s: Beat timestamps. None or fewer than 2 entries means we
            have no beat grid, so the input is returned unchanged.
        min_beats: Minimum region length in beats. Default 1.0 —
            one beat is the natural harmonic-rhythm floor on
            distorted rock guitar; jazz callers can lower it.

    Returns:
        A new tuple with short regions absorbed. Length is equal to
        or less than input length. Labels of surviving regions are
        unchanged.
    """
    if beats_s is None or len(chords) == 0:
        return chords
    beats_arr = np.asarray(beats_s, dtype=np.float64)
    if beats_arr.ndim != 1 or beats_arr.size < 2:
        return chords
    if len(chords) < 2:
        return chords

    beat_dur_s = float(np.median(np.diff(beats_arr)))
    if not np.isfinite(beat_dur_s) or beat_dur_s <= 0.0:
        return chords
    hold_floor_s = float(min_beats) * beat_dur_s

    working = list(chords)
    changed = True
    while changed and len(working) >= 2:
        changed = False
        for i in range(len(working)):
            c = working[i]
            duration = float(c.end_s) - float(c.start_s)
            if duration >= hold_floor_s:
                continue
            prev_c = working[i - 1] if i > 0 else None
            next_c = working[i + 1] if i < len(working) - 1 else None
            if prev_c is None and next_c is None:
                continue
            if prev_c is not None and next_c is not None:
                take_prev = float(prev_c.confidence) >= float(next_c.confidence)
            else:
                take_prev = next_c is None
            if take_prev and prev_c is not None:
                working[i - 1] = Chord(
                    start_s=float(prev_c.start_s),
                    end_s=float(c.end_s),
                    symbol=prev_c.symbol,
                    confidence=float(prev_c.confidence),
                )
                del working[i]
            elif next_c is not None:
                working[i + 1] = Chord(
                    start_s=float(c.start_s),
                    end_s=float(next_c.end_s),
                    symbol=next_c.symbol,
                    confidence=float(next_c.confidence),
                )
                del working[i]
            changed = True
            break

    return tuple(working)


def filter_chords_in_monophonic_sections(
    chords: Any,
    sections: Sequence[Mapping[str, Any]],
    mono_ratio_floor: float = 0.70,
    pc_diversity_ceiling: float = 0.35,
) -> Any:
    """Drop chord regions inside sections with a monophonic-riff signature.

    Stage 1.6 — monophonic-section chord gate. Chord recognition on
    a single-note riff is a category error: there is no harmony to
    recognise. The section-feature stage already computes
    ``monophonic_ratio`` and ``pitch_class_diversity`` per stem, per
    section (persisted in ``section['debug_features']``); this filter
    aggregates them (max mono / min diversity across the harmonic
    stems ``other`` + ``bass``) and drops any chord whose midpoint
    lies inside a section that is both strongly monophonic AND has a
    narrow pitch-class vocabulary.

    Sections missing all signals are treated as NOT-gated
    (conservative default): all their chords survive.

    Accepts either ``Tuple[Chord, ...]`` (analysis-side contract shape)
    or ``list[dict]`` (persistence/pipeline shape). Return type
    mirrors the input.

    Args:
        chords: Detector output, ordered by start time. Either a
            tuple of ``Chord`` or a list of chord dicts with
            ``start_s``/``end_s`` keys.
        sections: Section dicts. Each may carry ``start_s``/``end_s``
            (or the legacy ``start_time``/``end_time`` /
            ``startSec``/``endSec``) plus top-level
            ``monophonic_ratio``/``pitch_class_diversity`` OR a
            ``debug_features`` list of per-stem SectionFeatures dicts.
        mono_ratio_floor: Minimum ``monophonic_ratio`` to gate. Default
            0.70 (only strongly-mono sections fire).
        pc_diversity_ceiling: Maximum ``pitch_class_diversity`` to gate.
            Default 0.35 (only narrow-vocab sections fire).

    Returns:
        A new tuple/list containing chord regions whose midpoint does
        NOT fall inside a gated section. Matches the input container
        type.
    """
    if not chords or not sections:
        return chords

    def _section_window(sec: Mapping[str, Any]) -> Optional[Tuple[float, float]]:
        for start_key, end_key in (
            ("start_s", "end_s"),
            ("start_time", "end_time"),
            ("startSec", "endSec"),
        ):
            if start_key in sec and end_key in sec:
                try:
                    return float(sec[start_key]), float(sec[end_key])
                except (TypeError, ValueError):
                    return None
        return None

    def _section_mono_signals(
        sec: Mapping[str, Any],
    ) -> Optional[Tuple[float, float]]:
        """Return the section's (monophonic_ratio, pitch_class_diversity)
        pair, aggregated across per-stem debug_features when the
        top-level fields are absent.

        Aggregation rule: take the max ``monophonic_ratio`` across the
        harmonic stems (``other``, ``bass``) — if any harmonic stem is
        strongly monophonic then chord recognition has no polyphonic
        signal to latch onto — and the min ``pitch_class_diversity``
        (narrowest pc vocabulary wins). Vocals/drums are excluded
        because they don't feed the chord recognizer.
        """
        mono = sec.get("monophonic_ratio")
        div = sec.get("pitch_class_diversity")
        if mono is not None and div is not None:
            try:
                return float(mono), float(div)
            except (TypeError, ValueError):
                pass
        # Fallback to per-stem debug_features.
        debug = sec.get("debug_features")
        if not isinstance(debug, (list, tuple)):
            return None
        harmonic_stems = ("other", "bass")
        monos: list[float] = []
        divs: list[float] = []
        for row in debug:
            if not isinstance(row, Mapping):
                continue
            if row.get("stem_name") not in harmonic_stems:
                continue
            row_mono = row.get("monophonic_ratio")
            row_div = row.get("pitch_class_diversity")
            if row_mono is None or row_div is None:
                continue
            try:
                monos.append(float(row_mono))
                divs.append(float(row_div))
            except (TypeError, ValueError):
                continue
        if not monos:
            return None
        return max(monos), min(divs)

    gated_windows = []
    for sec in sections:
        signals = _section_mono_signals(sec)
        if signals is None:
            continue
        mono_f, div_f = signals
        if mono_f < mono_ratio_floor or div_f > pc_diversity_ceiling:
            continue
        window = _section_window(sec)
        if window is None:
            continue
        gated_windows.append(window)

    if not gated_windows:
        return chords

    def _in_gate(t: float) -> bool:
        for start_s, end_s in gated_windows:
            if start_s <= t < end_s:
                return True
        return False

    def _midpoint(c: Any) -> float:
        if isinstance(c, Mapping):
            return 0.5 * (float(c.get("start_s", 0.0))
                          + float(c.get("end_s", 0.0)))
        return 0.5 * (float(c.start_s) + float(c.end_s))

    if isinstance(chords, tuple):
        return tuple(c for c in chords if not _in_gate(_midpoint(c)))
    return [c for c in chords if not _in_gate(_midpoint(c))]


def collapse_same_root_regions(
    chords: Any,
    beats_s: Optional[np.ndarray],
    max_span_beats: float = 4.0,
) -> Any:
    """Merge contiguous chord regions sharing a root pitch class.

    Round-2 Fix 2 — harmonic-stability pass. Distorted-guitar chroma
    routinely flickers between (root, minor) and (root, power) within
    a single stable region because the 3rd bin sits at exactly the
    magnitude the Viterbi cannot decide on. This produces sequences
    like ``[C#m 0.5s, C#5 0.5s, C#m 1.0s, C#5 0.5s]`` that look like
    harmonic noise but musically are a single sustained
    ``C#-tonality``. This pass collapses such runs to a single region
    whose quality is the one carrying the most confidence × duration
    weight.

    Algorithm:
      1. Walk the chord list. Group consecutive regions by root pitch
         class (enharmonics collapse — ``C#`` and ``Db`` count as the
         same root).
      2. For each group of ≥2 regions:
         - If ``max_span_beats > 0`` AND beats are provided AND the
           group's total duration exceeds ``max_span_beats × median
           beat_dur`` → keep the group unchanged (protects a
           legitimate long ``Cmaj → Cmaj7 → C7`` progression from
           being flattened).
         - Otherwise emit a single region ``[first.start_s,
           last.end_s]`` whose symbol is the highest-weight symbol
           and confidence is the weighted mean.
      3. No-chord regions (symbol parses to ``None``) always terminate
         a run — never merged across.

    Accepts either ``Tuple[Chord, ...]`` (analysis-side contract) or
    ``list[dict]`` (persistence/pipeline shape). Return type mirrors
    the input container.

    Args:
        chords: Detector output, ordered by start time. Either a
            tuple of ``Chord`` or a list of chord dicts with
            ``start_s``/``end_s``/``symbol``/``confidence`` keys.
        beats_s: Beat timestamps in seconds. When None or shorter
            than 2 entries, the ``max_span_beats`` guard has no beat
            scale and is disabled — all same-root runs collapse
            regardless of duration.
        max_span_beats: Maximum total duration (in beats) of a
            collapsable run. Runs longer than this are left
            untouched. Default 4.0 (one bar in 4/4). Set to 0.0 or
            negative to disable the guard entirely.

    Returns:
        A new tuple/list of the same shape as the input, with
        collapsable runs merged. Length is equal to or less than
        input length.
    """
    if not chords:
        return chords
    is_tuple = isinstance(chords, tuple)
    working = list(chords)
    if len(working) < 2:
        return chords

    beat_dur_s: Optional[float] = None
    if beats_s is not None:
        beats_arr = np.asarray(beats_s, dtype=np.float64)
        if beats_arr.ndim == 1 and beats_arr.size >= 2:
            b = float(np.median(np.diff(beats_arr)))
            if np.isfinite(b) and b > 0.0:
                beat_dur_s = b

    def _sym(c: Any) -> str:
        if isinstance(c, Mapping):
            return str(c.get("symbol", "") or "")
        return str(getattr(c, "symbol", "") or "")

    def _start(c: Any) -> float:
        if isinstance(c, Mapping):
            return float(c.get("start_s", 0.0))
        return float(getattr(c, "start_s", 0.0))

    def _end(c: Any) -> float:
        if isinstance(c, Mapping):
            return float(c.get("end_s", 0.0))
        return float(getattr(c, "end_s", 0.0))

    def _conf(c: Any) -> float:
        if isinstance(c, Mapping):
            v = c.get("confidence", 1.0)
        else:
            v = getattr(c, "confidence", 1.0)
        try:
            return float(v)
        except (TypeError, ValueError):
            return 1.0

    def _emit_group(group: list[Any]) -> list[Any]:
        """Collapse a same-root group into a single region (or keep as-is
        when the max-span guard trips or the group has only one member)."""
        if len(group) < 2:
            return group
        first = group[0]
        last = group[-1]
        span_s = _end(last) - _start(first)
        if (
            max_span_beats > 0.0
            and beat_dur_s is not None
            and span_s > max_span_beats * beat_dur_s
        ):
            return group
        # Weighted vote: (confidence × duration) per unique symbol.
        weights: Dict[str, float] = {}
        confidences: Dict[str, list[float]] = {}
        for c in group:
            sym = _sym(c)
            dur = max(_end(c) - _start(c), 0.0)
            w = _conf(c) * dur
            weights[sym] = weights.get(sym, 0.0) + w
            confidences.setdefault(sym, []).append(_conf(c))
        # Argmax by weight, with symbol tiebreaker for determinism.
        winner = max(weights.items(), key=lambda kv: (kv[1], kv[0]))[0]
        winner_confs = confidences[winner]
        merged_conf = float(sum(winner_confs) / max(len(winner_confs), 1))
        # Rebuild one record of the same shape as the input.
        proto = first
        if isinstance(proto, Mapping):
            merged = dict(proto)
            merged["start_s"] = float(_start(first))
            merged["end_s"] = float(_end(last))
            merged["symbol"] = winner
            merged["confidence"] = merged_conf
            return [merged]
        merged_chord = Chord(
            start_s=float(_start(first)),
            end_s=float(_end(last)),
            symbol=winner,
            confidence=merged_conf,
        )
        return [merged_chord]

    # Walk the chord list, grouping by root pitch class. No-chord
    # regions (root is None) flush any pending group and pass through
    # as-is; they never join a group.
    out: list[Any] = []
    group: list[Any] = []
    group_root: Optional[int] = None
    for c in working:
        root = _chord_root_pc(_sym(c))
        if root is None:
            if group:
                out.extend(_emit_group(group))
                group = []
                group_root = None
            out.append(c)
            continue
        if group_root is None:
            group = [c]
            group_root = root
            continue
        if root == group_root:
            group.append(c)
            continue
        # Different root: flush the pending group, start a new one.
        out.extend(_emit_group(group))
        group = [c]
        group_root = root
    if group:
        out.extend(_emit_group(group))

    if is_tuple:
        return tuple(out)
    return out
