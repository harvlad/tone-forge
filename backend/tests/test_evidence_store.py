"""Unit tests for the Phase 1 evidence store.

Covers:

    * EvidenceRecord serde round-trip (schema invariants).
    * EvidenceStore append + read iteration on a tmp_path.
    * Writer mapping from AnalysisResult dict shape.
    * song_id derivation precedence (content_hash > url > name).
    * latest_for_section / latest_per_section semantics across
      multiple records for the same key.

No real audio or pipeline. Tests are hermetic; they build dict
shapes mirroring what unified_pipeline.AnalysisResult.to_dict()
produces today and exercise the evidence subsystem in isolation.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import pytest

from bench.evidence import (
    ConsensusOutput,
    Correction,
    EvidenceRecord,
    EvidenceStore,
    ReferenceSource,
    derive_section_id,
    derive_song_id,
    dump_evidence_record,
    from_analysis_result,
    load_evidence_record,
)
from bench.evidence.writer import write_analysis_to_store


# ---------------------------------------------------------------------------
# Schema serde
# ---------------------------------------------------------------------------


def test_evidence_record_serde_round_trip_minimal(tmp_path: Path) -> None:
    """Minimal record (no refs, no consensus, no corrections) survives JSONL."""
    record = EvidenceRecord(
        song_id="abc123",
        section_id="abc123:0001",
        timestamp_utc="2026-06-18T12:00:00.000000Z",
        jam_output={"guidance_mode": "chord", "guidance_confidence": 0.42},
    )
    path = tmp_path / "x.jsonl"
    dump_evidence_record(record, path)

    line = path.read_text(encoding="utf-8").strip()
    loaded = load_evidence_record(line)
    assert loaded.song_id == record.song_id
    assert loaded.section_id == record.section_id
    assert loaded.timestamp_utc == record.timestamp_utc
    assert loaded.jam_output["guidance_mode"] == "chord"
    assert loaded.reference_sources == ()
    assert loaded.consensus_output is None
    assert loaded.corrections == ()
    assert loaded.schema_version == 1


def test_evidence_record_serde_round_trip_fully_populated(tmp_path: Path) -> None:
    """Round-trip with reference sources, consensus, corrections set."""
    record = EvidenceRecord(
        song_id="abc123",
        section_id="abc123:0001",
        timestamp_utc="2026-06-18T12:00:00.000000Z",
        jam_output={"guidance_mode": "chord"},
        reference_sources=(
            ReferenceSource(
                source="songsterr",
                version="rev-42",
                fetched_at_utc="2026-06-18T11:00:00.000000Z",
                labels={"chord_sequence": ["C", "G", "Am", "F"]},
                source_url="https://www.songsterr.com/some-song",
            ),
            ReferenceSource(
                source="ultimate_guitar",
                version="rev-99",
                fetched_at_utc="2026-06-18T11:05:00.000000Z",
                labels={"chord_sequence": ["C", "G", "Am", "F"]},
            ),
        ),
        consensus_output=ConsensusOutput(
            guidance_mode="chord",
            chord_sequence=("C", "G", "Am", "F"),
            confidence=0.95,
            agreement={"guidance_mode": 1.0, "chord_sequence": 1.0},
            votes={"guidance_mode": {"chord": 2}},
        ),
        corrections=(
            Correction(
                correction_type="guidance_mode",
                previous_value="chord",
                corrected_value="riff",
                user_id="user-7",
                note="this is the seven nation army riff",
            ),
        ),
    )
    path = tmp_path / "x.jsonl"
    dump_evidence_record(record, path)
    loaded = load_evidence_record(path.read_text(encoding="utf-8").strip())

    assert len(loaded.reference_sources) == 2
    assert loaded.reference_sources[0].source == "songsterr"
    assert loaded.reference_sources[1].source == "ultimate_guitar"
    assert loaded.consensus_output is not None
    assert loaded.consensus_output.guidance_mode == "chord"
    assert loaded.consensus_output.chord_sequence == ("C", "G", "Am", "F")
    assert math.isclose(loaded.consensus_output.confidence, 0.95)
    assert loaded.consensus_output.votes == {"guidance_mode": {"chord": 2}}
    assert len(loaded.corrections) == 1
    assert loaded.corrections[0].correction_type == "guidance_mode"
    assert loaded.corrections[0].corrected_value == "riff"


def test_unsupported_schema_version_raises() -> None:
    payload = {
        "schema_version": 99,
        "song_id": "x",
        "section_id": "x:0001",
        "timestamp_utc": "2026-06-18T12:00:00Z",
    }
    with pytest.raises(ValueError, match="unsupported evidence schema"):
        load_evidence_record(json.dumps(payload))


# ---------------------------------------------------------------------------
# Song / section id derivation
# ---------------------------------------------------------------------------


def test_song_id_prefers_content_hash() -> None:
    a = derive_song_id(content_hash="deadbeef" * 8, source_url="x", source_name="y")
    b = derive_song_id(content_hash="deadbeef" * 8, source_url="DIFFERENT")
    assert a == b
    assert len(a) == 16


def test_song_id_falls_back_to_url_then_name() -> None:
    a = derive_song_id(source_url="https://yt.example/abc", duration_sec=180.0)
    b = derive_song_id(source_url="https://yt.example/abc", duration_sec=180.0)
    c = derive_song_id(source_name="MySong")
    assert a == b
    assert a != c


def test_song_id_anonymous_when_nothing_provided() -> None:
    """No identifying info still yields a deterministic id rather than raising."""
    a = derive_song_id()
    b = derive_song_id()
    assert a == b
    assert len(a) == 16


def test_section_id_is_lex_sortable() -> None:
    ids = [derive_section_id("abc", i) for i in range(15)]
    assert ids == sorted(ids), "section ids must sort lexicographically with order"


# ---------------------------------------------------------------------------
# Writer: AnalysisResult dict -> EvidenceRecord list
# ---------------------------------------------------------------------------


def _make_analysis_result_dict() -> dict:
    """Build a minimal AnalysisResult-shape dict for the writer."""
    return {
        "source_name": "Test Song",
        "source_url": "https://yt.example/x",
        "duration_sec": 240.0,
        "tempo_bpm": 120.0,
        "detected_key": "C major",
        "detected_key_root": 0,
        "detected_key_strength": 0.8,
        "analysis_mode": "standard",
        "chords": [
            {"start_s": 0.0, "end_s": 4.0, "symbol": "C", "confidence": 0.9},
            {"start_s": 4.0, "end_s": 8.0, "symbol": "G", "confidence": 0.85},
            {"start_s": 8.0, "end_s": 12.0, "symbol": "Am", "confidence": 0.9},
            {"start_s": 12.0, "end_s": 16.0, "symbol": "F", "confidence": 0.88},
            # Chord landing AFTER the section window — must be excluded:
            {"start_s": 100.0, "end_s": 104.0, "symbol": "Em", "confidence": 0.7},
        ],
        "sections": [
            {
                "start_time": 0.0,
                "end_time": 16.0,
                "type": "verse",
                "guidance_mode": "chord",
                "guidance_confidence": 0.95,
                "guidance_reason": "chord: other=chord(0.71)",
                "dominant_stem": "other",
                "landmark_notes": [],
            },
            {
                "start_time": 16.0,
                "end_time": 32.0,
                "type": "chorus",
                "guidance_mode": "riff",
                "guidance_confidence": 0.71,
                "guidance_reason": "riff: bass=riff(0.65)",
                "dominant_stem": "bass",
                "landmark_notes": [
                    {"pitch": 40, "start_s": 16.2, "end_s": 16.6},
                ],
            },
        ],
    }


def test_writer_emits_one_record_per_section() -> None:
    result = _make_analysis_result_dict()
    records = from_analysis_result(result)
    assert len(records) == 2
    assert records[0].jam_output["section_index"] == 0
    assert records[0].jam_output["start_s"] == 0.0
    assert records[0].jam_output["end_s"] == 16.0
    assert records[0].jam_output["guidance_mode"] == "chord"
    assert records[1].jam_output["section_index"] == 1
    assert records[1].jam_output["guidance_mode"] == "riff"


def test_writer_chord_window_filter() -> None:
    """Only chords whose midpoint lies in [start, end) carry over."""
    result = _make_analysis_result_dict()
    records = from_analysis_result(result)
    # First section: 0-16s should see C, G, Am, F.
    chord_syms_section_0 = [c["symbol"] for c in records[0].jam_output["chords_in_section"]]
    assert chord_syms_section_0 == ["C", "G", "Am", "F"]
    # Second section: 16-32s should see no chords (the Em at 100-104s
    # is far outside the window).
    assert records[1].jam_output["chords_in_section"] == []


def test_writer_carries_song_context() -> None:
    result = _make_analysis_result_dict()
    records = from_analysis_result(result)
    ctx = records[0].jam_output["song_context"]
    assert ctx["source_url"] == "https://yt.example/x"
    assert ctx["tempo_bpm"] == 120.0
    assert ctx["detected_key"] == "C major"


def test_writer_handles_no_sections() -> None:
    result = {
        "source_name": "Empty",
        "sections": [],
    }
    assert from_analysis_result(result) == []


def test_writer_skips_section_with_invalid_timing() -> None:
    # Writer's ``except (TypeError, ValueError)`` triggers when timing
    # fields are present but non-castable. Missing keys default to 0.0
    # via ``.get(..., 0.0)`` and pass through (a section that genuinely
    # has no timing info still produces a degenerate 0..0 record — not
    # ideal, but the writer treats absent keys as "0" rather than "skip").
    result = {
        "source_name": "Broken",
        "sections": [
            {"start_time": "bad", "end_time": "worse", "type": "verse"},
            {"start_time": 0.0, "end_time": 4.0, "type": "verse"},
        ],
    }
    records = from_analysis_result(result)
    assert len(records) == 1
    assert records[0].jam_output["section_index"] == 1


def test_writer_uses_supplied_timestamp() -> None:
    result = _make_analysis_result_dict()
    ts = "2030-01-01T00:00:00.000000Z"
    records = from_analysis_result(result, timestamp_utc=ts)
    assert all(r.timestamp_utc == ts for r in records)


# ---------------------------------------------------------------------------
# Store integration
# ---------------------------------------------------------------------------


def _ts_at(date: str) -> str:
    """Build an ISO timestamp landing on the given UTC date."""
    return f"{date}T12:00:00.000000Z"


def test_store_append_and_iter(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    r1 = EvidenceRecord(
        song_id="abc",
        section_id="abc:0001",
        timestamp_utc=_ts_at("2026-06-18"),
    )
    r2 = EvidenceRecord(
        song_id="abc",
        section_id="abc:0002",
        timestamp_utc=_ts_at("2026-06-18"),
    )
    r3 = EvidenceRecord(
        song_id="def",
        section_id="def:0001",
        timestamp_utc=_ts_at("2026-06-19"),
    )
    store.append(r1)
    store.append(r2)
    store.append(r3)

    paths = store.file_paths()
    assert len(paths) == 2  # two distinct days
    records = list(store.iter_records())
    assert len(records) == 3
    assert {r.song_id for r in records} == {"abc", "def"}


def test_store_count_and_total_bytes(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    assert store.count() == 0
    assert store.total_bytes() == 0
    store.extend([
        EvidenceRecord(
            song_id="x", section_id=f"x:{i:04d}",
            timestamp_utc=_ts_at("2026-06-18"),
        ) for i in range(5)
    ])
    assert store.count() == 5
    assert store.total_bytes() > 0


def test_store_latest_for_section_picks_newest_timestamp(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    older = EvidenceRecord(
        song_id="abc", section_id="abc:0001",
        timestamp_utc="2026-06-18T10:00:00.000000Z",
        jam_output={"v": "old"},
    )
    newer = EvidenceRecord(
        song_id="abc", section_id="abc:0001",
        timestamp_utc="2026-06-18T11:00:00.000000Z",
        jam_output={"v": "new"},
    )
    store.append(older)
    store.append(newer)

    latest = store.latest_for_section("abc", "abc:0001")
    assert latest is not None
    assert latest.jam_output["v"] == "new"


def test_store_latest_per_section_groups_correctly(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.extend([
        EvidenceRecord(
            song_id="abc", section_id="abc:0001",
            timestamp_utc="2026-06-18T10:00:00.000000Z",
            jam_output={"r": 1},
        ),
        EvidenceRecord(
            song_id="abc", section_id="abc:0001",
            timestamp_utc="2026-06-18T11:00:00.000000Z",
            jam_output={"r": 2},
        ),
        EvidenceRecord(
            song_id="abc", section_id="abc:0002",
            timestamp_utc="2026-06-18T10:00:00.000000Z",
            jam_output={"r": 3},
        ),
        EvidenceRecord(
            song_id="def", section_id="def:0001",
            timestamp_utc="2026-06-18T10:00:00.000000Z",
            jam_output={"r": 4},
        ),
    ])
    latest = store.latest_per_section()
    assert latest[("abc", "abc:0001")].jam_output["r"] == 2
    assert latest[("abc", "abc:0002")].jam_output["r"] == 3
    assert latest[("def", "def:0001")].jam_output["r"] == 4

    only_abc = store.latest_per_section(song_id="abc")
    assert set(only_abc.keys()) == {("abc", "abc:0001"), ("abc", "abc:0002")}


def test_store_tolerates_corrupt_line(tmp_path: Path) -> None:
    """A trailing partial line must not block reads of earlier records."""
    store = EvidenceStore(root=tmp_path)
    store.append(EvidenceRecord(
        song_id="x", section_id="x:0001",
        timestamp_utc=_ts_at("2026-06-18"),
    ))
    # Append a corrupt line.
    daily_path = store.file_paths()[0]
    with daily_path.open("a", encoding="utf-8") as fh:
        fh.write('{"this is": not json\n')
    records = list(store.iter_records())
    assert len(records) == 1
    assert records[0].song_id == "x"


def test_store_date_prefix_filter(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(EvidenceRecord(
        song_id="a", section_id="a:0001",
        timestamp_utc="2026-06-18T10:00:00.000000Z",
    ))
    store.append(EvidenceRecord(
        song_id="b", section_id="b:0001",
        timestamp_utc="2026-07-01T10:00:00.000000Z",
    ))
    june = list(store.iter_records(date_prefix="2026-06"))
    july = list(store.iter_records(date_prefix="2026-07"))
    assert len(june) == 1 and june[0].song_id == "a"
    assert len(july) == 1 and july[0].song_id == "b"


def test_write_analysis_to_store_helper(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    result = _make_analysis_result_dict()
    n = write_analysis_to_store(result, store)
    assert n == 2
    assert store.count() == 2
    # Both records share the same song_id (derived from URL+duration).
    records = list(store.iter_records())
    assert records[0].song_id == records[1].song_id


def test_writer_song_id_stable_across_calls(tmp_path: Path) -> None:
    """Same input dict produces same song_id every time."""
    result = _make_analysis_result_dict()
    r1 = from_analysis_result(result)
    r2 = from_analysis_result(result)
    assert r1[0].song_id == r2[0].song_id


# ---------------------------------------------------------------------------
# Phase 9 forward-compat: schema must accept extra blob
# ---------------------------------------------------------------------------


def test_extra_field_round_trips(tmp_path: Path) -> None:
    record = EvidenceRecord(
        song_id="x", section_id="x:0001",
        timestamp_utc=_ts_at("2026-06-18"),
        extra={
            "audio_features": {"mfcc_mean": [0.1, 0.2, 0.3]},
            "model_version": "v1.2",
        },
    )
    path = tmp_path / "x.jsonl"
    dump_evidence_record(record, path)
    loaded = load_evidence_record(path.read_text(encoding="utf-8").strip())
    assert loaded.extra["audio_features"]["mfcc_mean"] == [0.1, 0.2, 0.3]
    assert loaded.extra["model_version"] == "v1.2"
