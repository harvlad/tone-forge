"""Map a pipeline ``AnalysisResult`` dict to ``EvidenceRecord`` rows.

This is the bridge between the live JAM pipeline output and the
durable evidence store. One ``AnalysisResult`` produces one
``EvidenceRecord`` per section. Sections without timing fields are
skipped (defensive — older bundles may carry odd shapes).

Phase 1 only writes ``jam_output``; ``reference_sources`` /
``consensus_output`` / ``corrections`` remain empty. Later phases
append *additional* records with the same ``(song_id, section_id)``
and populated reference / consensus fields.

The mapping is intentionally pipeline-version-tolerant:

    * Missing top-level fields default to ``None``.
    * Missing per-section fields default to ``None`` / ``""`` / ``0``.
    * Unknown fields land in ``extra`` so a future schema bump
      doesn't drop information.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from .schema import EvidenceRecord
from .store import EvidenceStore


__all__ = [
    "derive_song_id",
    "derive_section_id",
    "from_analysis_result",
]


# ---------------------------------------------------------------------------
# Identity helpers — content-derived where possible, URL-derived fallback.
# ---------------------------------------------------------------------------


def derive_song_id(
    *,
    source_url: Optional[str] = None,
    source_name: Optional[str] = None,
    content_hash: Optional[str] = None,
    duration_sec: Optional[float] = None,
) -> str:
    """Compute a stable 16-char song id.

    Priority order:

        1. ``content_hash`` (sha256 of the audio file, set by the
           SessionBundle's AcquiredAudio) — best because two
           identical audio files always map to the same id
           regardless of URL.
        2. ``source_url`` + ``duration_sec`` — stable across
           re-analyses of the same YouTube link.
        3. ``source_name`` only — last resort for ad-hoc uploads
           with no URL and no content hash.

    The output is the first 16 hex chars of a SHA-1 over the chosen
    seed string. 16 hex chars = 64 bits, enough entropy to avoid
    collisions in any realistic corpus while staying short enough
    to embed in section ids and CLI output.
    """
    if content_hash:
        seed = f"content:{content_hash}"
    elif source_url:
        seed = f"url:{source_url}|dur:{duration_sec or 0:.1f}"
    elif source_name:
        seed = f"name:{source_name}"
    else:
        # No identifying info at all — fall back to a degenerate id.
        # Callers landing here have a bigger problem (no provenance);
        # we don't raise so evidence can still be written.
        seed = "anonymous"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def derive_section_id(song_id: str, section_idx: int) -> str:
    """``{song_id}:{section_idx:04d}`` — lex-sortable per song."""
    return f"{song_id}:{int(section_idx):04d}"


# ---------------------------------------------------------------------------
# Mapping pipeline output -> evidence record.
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _chords_in_window(
    chords: Optional[Iterable[Mapping[str, Any]]],
    start_s: float,
    end_s: float,
) -> list[dict]:
    """Filter the song-level chord lane to one section's window.

    A chord is included if its midpoint falls in
    ``[start_s, end_s)``. Matches the convention used by the
    section-features extractor at ``analysis/section_features.py``.
    """
    if not chords:
        return []
    out: list[dict] = []
    for c in chords:
        try:
            cs = float(c.get("start_s", 0.0))
            ce = float(c.get("end_s", 0.0))
        except (TypeError, ValueError):
            continue
        mid = 0.5 * (cs + ce)
        if start_s <= mid < end_s:
            out.append({
                "start_s": cs,
                "end_s": ce,
                "symbol": c.get("symbol"),
                "confidence": c.get("confidence"),
            })
    return out


def from_analysis_result(
    result_dict: Mapping[str, Any],
    *,
    timestamp_utc: Optional[str] = None,
) -> list[EvidenceRecord]:
    """Convert one ``AnalysisResult.to_dict()`` payload into per-section evidence.

    The input dict shape is what the pipeline persists to
    ``backend/data/history.json`` (and what the SessionBundle builder
    consumes). See ``unified_pipeline.AnalysisResult.to_dict``.

    Returns one ``EvidenceRecord`` per section. If the result has no
    sections (e.g. fast-mode or a failed analysis) returns an empty
    list.
    """
    sections = result_dict.get("sections") or []
    if not sections:
        return []

    song_id = derive_song_id(
        source_url=result_dict.get("source_url"),
        source_name=result_dict.get("source_name"),
        content_hash=(
            result_dict.get("content_hash")
            or (result_dict.get("audio") or {}).get("content_hash")
        ),
        duration_sec=result_dict.get("duration_sec"),
    )
    ts = timestamp_utc or _utc_now_iso()

    song_context = {
        "source_name": result_dict.get("source_name"),
        "source_url": result_dict.get("source_url"),
        "duration_sec": result_dict.get("duration_sec"),
        "tempo_bpm": result_dict.get("tempo_bpm"),
        "detected_key": result_dict.get("detected_key"),
        "detected_key_root": result_dict.get("detected_key_root"),
        "detected_key_strength": result_dict.get("detected_key_strength"),
        "analysis_mode": result_dict.get("analysis_mode"),
    }
    chords = result_dict.get("chords") or []

    out: list[EvidenceRecord] = []
    for idx, section in enumerate(sections):
        try:
            start_s = float(section.get("start_time", 0.0))
            end_s = float(section.get("end_time", 0.0))
        except (TypeError, ValueError):
            # Section without timing info — skip rather than emit a
            # half-formed record.
            continue
        section_id = derive_section_id(song_id, idx)
        jam_output = {
            "section_index": idx,
            "start_s": start_s,
            "end_s": end_s,
            "type": section.get("type"),
            "guidance_mode": section.get("guidance_mode", "chord"),
            "guidance_confidence": float(section.get("guidance_confidence", 0.0)),
            "guidance_reason": section.get("guidance_reason", ""),
            "dominant_stem": section.get("dominant_stem", ""),
            "landmark_notes": list(section.get("landmark_notes") or []),
            "chords_in_section": _chords_in_window(chords, start_s, end_s),
            "song_context": song_context,
        }
        out.append(EvidenceRecord(
            song_id=song_id,
            section_id=section_id,
            timestamp_utc=ts,
            jam_output=jam_output,
            reference_sources=(),
            consensus_output=None,
            corrections=(),
            schema_version=1,
        ))
    return out


def write_analysis_to_store(
    result_dict: Mapping[str, Any],
    store: EvidenceStore,
    *,
    timestamp_utc: Optional[str] = None,
) -> int:
    """Helper: convert + append in one call. Returns count written."""
    records = from_analysis_result(result_dict, timestamp_utc=timestamp_utc)
    store.extend(records)
    return len(records)
