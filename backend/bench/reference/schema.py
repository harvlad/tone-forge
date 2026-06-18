"""Normalized reference-file schema.

A reference file is what a curator drops into
``backend/data/references/`` to teach the evidence store what an
external source (Songsterr / UG / Chordify / manual) says about one
song. The file is provider-agnostic: every adapter normalizes into
this shape so the ingest path stays simple.

File layout::

    {
      "song_id": "610382edfb7fcdad",
      "source": "songsterr",
      "version": "rev-2026-06-15",
      "fetched_at_utc": "2026-06-18T14:00:00.000000Z",
      "source_url": "https://www.songsterr.com/a/wsa/...",
      "sections": [
        {
          "section_id": "610382edfb7fcdad:0000",
          "labels": {
            "guidance_mode": "chord",
            "chord_sequence": ["Am", "G", "F", "C"],
            "tempo_bpm": 72.0,
            "tuning": "EADGBE"
          }
        },
        ...
      ]
    }

The ``labels`` dict is intentionally free-form per the Phase 1 schema
note: different providers carry different shapes (Songsterr knows
rhythm; Chordify knows beat grid; UG is freeform). The Consensus
Builder (Phase 3) reads the well-known keys it understands and ignores
the rest.

Why a separate file format instead of writing ``EvidenceRecord``
JSONL directly?

    1. One curator-facing file per (song, source) pair is easier to
       hand-edit than per-section JSONL.
    2. The ingest step computes ``fetched_at_utc`` -> per-record
       ``timestamp_utc`` consistently across all sections, so a
       single reference batch groups cleanly in time-window queries.
    3. The intermediate format lets us evolve provider adapters
       without touching the evidence store.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple


__all__ = [
    "RawReferenceSection",
    "RawReferenceFile",
    "load_reference_file",
    "dump_reference_file",
]


@dataclass(frozen=True)
class RawReferenceSection:
    """One section's labels from one external source."""

    section_id: str
    labels: Mapping[str, Any]


@dataclass(frozen=True)
class RawReferenceFile:
    """A normalized reference file: one source covering one song.

    All sections in a file come from the same source/version/fetch
    event, so they share ``source``, ``version``, ``fetched_at_utc``,
    ``source_url``. The ingest step builds one ``EvidenceRecord`` per
    section, each carrying a single-element ``reference_sources``
    tuple with the shared header copied verbatim.

    Field invariants:

        * ``song_id`` must match the evidence store's existing
          ``derive_song_id`` output for the same song; the ingest
          step does not re-derive it. Callers building a reference
          file by hand must look up the song id from the evidence
          store first.
        * ``sections`` may be empty (rare but legal — a curator may
          want to record "this source has nothing for this song" as
          a zero-section reference file).
    """

    song_id: str
    source: str
    version: str
    fetched_at_utc: str
    sections: Tuple[RawReferenceSection, ...] = ()
    source_url: Optional[str] = None


# ---------------------------------------------------------------------------
# JSON I/O. No pickle, no datetime objects on the wire.
# ---------------------------------------------------------------------------


def _section_to_jsonable(s: RawReferenceSection) -> dict:
    return {"section_id": s.section_id, "labels": dict(s.labels)}


def _jsonable_to_section(data: Mapping[str, Any]) -> RawReferenceSection:
    return RawReferenceSection(
        section_id=str(data["section_id"]),
        labels=dict(data.get("labels", {})),
    )


def load_reference_file(path: Path | str) -> RawReferenceFile:
    """Parse a reference JSON file into ``RawReferenceFile``.

    Raises ``ValueError`` if required top-level fields are missing.
    Per-section ``labels`` are accepted verbatim — no schema check on
    inner keys, because providers carry different shapes.
    """
    target = Path(path)
    with target.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, Mapping):
        raise ValueError(f"reference file root must be an object: {target}")
    for required in ("song_id", "source", "version", "fetched_at_utc"):
        if required not in data:
            raise ValueError(
                f"reference file {target} missing required field {required!r}"
            )
    sections_raw = data.get("sections", [])
    if not isinstance(sections_raw, list):
        raise ValueError(
            f"reference file {target} 'sections' must be a list, "
            f"got {type(sections_raw).__name__}"
        )
    sections = tuple(_jsonable_to_section(s) for s in sections_raw)
    return RawReferenceFile(
        song_id=str(data["song_id"]),
        source=str(data["source"]),
        version=str(data["version"]),
        fetched_at_utc=str(data["fetched_at_utc"]),
        sections=sections,
        source_url=data.get("source_url"),
    )


def dump_reference_file(ref: RawReferenceFile, path: Path | str) -> Path:
    """Write a ``RawReferenceFile`` as pretty-printed JSON.

    Pretty-printed (indent=2) instead of compact because reference
    files are curator-facing: humans read and diff them by hand.
    The evidence JSONL files use compact serialization; reference
    files do not.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "song_id": ref.song_id,
        "source": ref.source,
        "version": ref.version,
        "fetched_at_utc": ref.fetched_at_utc,
        "source_url": ref.source_url,
        "sections": [_section_to_jsonable(s) for s in ref.sections],
    }
    with target.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    return target
