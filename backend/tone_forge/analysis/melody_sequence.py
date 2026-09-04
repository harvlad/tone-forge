"""Song-melody sequence assembly from per-stem MIDI extraction.

Why: the extraction pipeline already knows which notes are the tune —
vocals stems are the melody by definition, and ``midi.melody_split``
tags polyphonic stems' notes with ``role`` ∈ {melody, harmony} — but
nothing composed that knowledge into a single playable line. Clients
that want to *play* the melody (launchpad follow-along, mobile
sequencer, MIDI export) each had to re-derive it from raw
``midi_stems``. This module builds the one canonical
``contracts.MelodySequence``: full-song monophonic line plus
per-section phrase slices.

Boundary discipline: this is an ``analysis/`` module, so it may import
only ``tone_forge.contracts``. The melody/accompaniment splitter lives
in the frozen ``midi/`` package — callers at the composition layer
(``tone_forge_api``) inject ``midi.melody_split.annotate_roles`` via
the ``annotate`` parameter for stems whose notes lost their ``role``
tags (the deep-link path decodes notes from binary MIDI, which cannot
carry roles).

Pure functions over plain note dicts ``{"pitch", "start", "end",
"velocity"}`` — no audio, no I/O, unit-testable without models.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from tone_forge.contracts import MelodyPhrase, MelodySequence

__all__ = ["build_melody_sequence"]


# Minimum note count for a stem to be a credible melody source — below
# this the "melody" is a handful of stray detections and the sequence
# surfaces would render an insultingly sparse lane.
_MIN_MELODY_NOTES = 4

# Notes shorter than this after mono-trimming are inaudible clicks;
# they came from overlap trimming or extractor jitter, not the tune.
_MIN_NOTE_DURATION_S = 0.03

# Deterministic tie-break when two stems score equally: the human
# prior for "which stem carries the tune". Unlisted stems sort last,
# alphabetically.
_STEM_PREFERENCE = (
    "vocals",
    "other",
    "guitar_lead",
    "guitar_left",
    "guitar_right",
    "guitar",
    "piano",
)

# Stems the role splitter runs on — mirrors the gate in the pipeline's
# melody-role tagging pass (other/piano/guitar*). Bass and drums are
# never melody sources: drums are unpitched, bass lines read an octave
# under the tune and would win duration-scoring on sustain alone.
def _is_polyphonic_melody_source(stem: str) -> bool:
    return stem in ("other", "piano") or stem.startswith("guitar")


def _clean_notes(raw: Any) -> List[Dict[str, Any]]:
    """Coerce a wire/pipeline notes list into validated note dicts.

    Bad rows are dropped silently (consistent with the bundle
    assembler's lenient reads) — one corrupt note must not cost the
    whole melody lane. ``role`` is preserved when present so the
    caller can filter on it.
    """
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        try:
            pitch = int(item["pitch"])
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        try:
            velocity = int(item.get("velocity", 80))
        except (TypeError, ValueError):
            velocity = 80
        note: Dict[str, Any] = {
            "pitch": pitch,
            "start": start,
            "end": end,
            "velocity": max(1, min(127, velocity)),
        }
        role = item.get("role")
        if isinstance(role, str):
            note["role"] = role
        out.append(note)
    return out


def _melody_candidates(
    stem: str,
    notes: List[Dict[str, Any]],
    annotate: Optional[Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]],
) -> List[Dict[str, Any]]:
    """The subset of a stem's notes eligible to be the melody line."""
    if stem == "vocals":
        # A vocals stem *is* the melody — the splitter's register gate
        # would wrongly cull low verses against a high chorus median.
        return notes
    if not _is_polyphonic_melody_source(stem):
        return []
    if notes and not any("role" in n for n in notes):
        # Deep-link decode path: notes rebuilt from binary MIDI carry
        # no role tags. Re-annotate via the injected splitter; without
        # one, treat the stem as unusable rather than passing chord
        # strums off as the tune.
        if annotate is None:
            return []
        notes = _clean_notes(annotate(notes))
    return [n for n in notes if n.get("role") == "melody"]


def _mono_trim(notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enforce strict monophony: trim overlaps, drop residual slivers.

    Sequence players walk one note at a time; overlapping extractions
    (double-stop top voices, extractor tails) would need voice
    stealing on every client. Trimming the earlier note at the next
    onset (mono legato) is the standard synth behaviour and keeps
    onset times — the musically load-bearing part — untouched.
    """
    ordered = sorted(notes, key=lambda n: (n["start"], n["pitch"]))
    out: List[Dict[str, Any]] = []
    for note in ordered:
        if out and out[-1]["end"] > note["start"]:
            out[-1] = {**out[-1], "end": note["start"]}
            if out[-1]["end"] - out[-1]["start"] < _MIN_NOTE_DURATION_S:
                out.pop()
        out.append(
            {
                "pitch": note["pitch"],
                "start": note["start"],
                "end": note["end"],
                "velocity": note["velocity"],
            }
        )
    return [n for n in out if n["end"] - n["start"] >= _MIN_NOTE_DURATION_S]


def _section_bounds(raw_sections: Any) -> List[Tuple[float, float, str]]:
    """Tolerant read of persisted sections (contract or legacy keys)."""
    if not isinstance(raw_sections, Iterable) or isinstance(
        raw_sections, (str, bytes)
    ):
        return []
    out: List[Tuple[float, float, str]] = []
    for item in raw_sections:
        if not isinstance(item, Mapping):
            continue
        start = end = None
        for key in ("start_s", "start", "start_time"):
            if item.get(key) is not None:
                try:
                    start = float(item[key])
                except (TypeError, ValueError):
                    start = None
                break
        for key in ("end_s", "end", "end_time"):
            if item.get(key) is not None:
                try:
                    end = float(item[key])
                except (TypeError, ValueError):
                    end = None
                break
        if start is None or end is None or end <= start:
            continue
        label = ""
        for key in ("label", "name", "type", "section_type"):
            value = item.get(key)
            if isinstance(value, str) and value:
                label = value
                break
        out.append((start, end, label))
    out.sort(key=lambda s: s[0])
    return out


def build_melody_sequence(
    midi_stems: Mapping[str, Any],
    sections: Any = None,
    *,
    annotate: Optional[
        Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]
    ] = None,
) -> Optional[MelodySequence]:
    """Compose the song's melody line from per-stem extracted MIDI.

    ``midi_stems`` is the pipeline / decoded-payload shape:
    ``{stem: {"notes": [...], "overall_confidence": ...}}``. Stems are
    scored by total voiced duration of their melody-eligible notes
    (all notes for vocals; ``role == "melody"`` notes for the
    polyphonic sources) — duration beats note-count so a busy strummed
    stem full of short double-stop tops doesn't outrank a sung line.
    Returns ``None`` when no stem clears ``_MIN_MELODY_NOTES``:
    consumers treat that as "this song has no usable melody lane",
    the same contract as an absent Phase-3 field.
    """
    if not isinstance(midi_stems, Mapping):
        return None

    best_key: Optional[Tuple[float, int, str]] = None
    best_stem = ""
    best_candidates: List[Dict[str, Any]] = []
    for stem, blob in midi_stems.items():
        if not isinstance(stem, str) or not isinstance(blob, Mapping):
            continue
        notes = _clean_notes(blob.get("notes"))
        candidates = _melody_candidates(stem, notes, annotate)
        if len(candidates) < _MIN_MELODY_NOTES:
            continue
        score = sum(n["end"] - n["start"] for n in candidates)
        rank = (
            _STEM_PREFERENCE.index(stem)
            if stem in _STEM_PREFERENCE
            else len(_STEM_PREFERENCE)
        )
        # Higher score wins; on a tie the preference table decides so
        # the pick is deterministic across runs and platforms.
        key = (score, -rank, stem)
        if best_key is None or key > best_key:
            best_key, best_stem, best_candidates = key, stem, candidates
    if best_key is None:
        return None

    source_stem, candidates = best_stem, best_candidates
    raw_conf = midi_stems[source_stem].get("overall_confidence")
    try:
        confidence = max(0.0, min(1.0, float(raw_conf)))
    except (TypeError, ValueError):
        # Absent / unparsable — a neutral prior, not a claim.
        confidence = 0.5

    line = _mono_trim(candidates)
    if len(line) < _MIN_MELODY_NOTES:
        return None

    phrases: List[MelodyPhrase] = []
    for start_s, end_s, label in _section_bounds(sections):
        phrases.append(
            MelodyPhrase(
                start_s=start_s,
                end_s=end_s,
                section_label=label,
                notes=tuple(
                    n for n in line if start_s <= n["start"] < end_s
                ),
            )
        )

    return MelodySequence(
        source_stem=source_stem,
        notes=tuple(line),
        phrases=tuple(phrases),
        confidence=confidence,
    )
