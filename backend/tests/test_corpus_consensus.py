"""Phase 5 Consensus Corpus tests.

Covers:

    * ``iter_consensus_corpus`` confidence gate
    * Latest-wins selection across multiple consensus records
    * ``require_jam_output`` filter
    * ``song_id`` filter
    * ``summarise_consensus_corpus`` shape + values
    * CLI: stats / list / export (text + JSON)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest

from bench.corpus_consensus import (
    ConsensusCorpusConfig,
    iter_consensus_corpus,
    summarise_consensus_corpus,
)
from bench.corpus_consensus.__main__ import main as cc_main
from bench.evidence import (
    ConsensusOutput,
    EvidenceRecord,
    EvidenceStore,
)


SONG = "abc1234567890def"
SONG2 = "fed0987654321cba"


def _sec(song: str, idx: int) -> str:
    return f"{song}:{idx:04d}"


def _jam_record(
    *,
    song_id: str = SONG,
    section_id: Optional[str] = None,
    guidance_mode: str = "chord",
    chords: Optional[list[dict]] = None,
    timestamp_utc: str = "2026-06-18T00:00:00.000000Z",
) -> EvidenceRecord:
    return EvidenceRecord(
        song_id=song_id,
        section_id=section_id or _sec(song_id, 0),
        timestamp_utc=timestamp_utc,
        jam_output={
            "guidance_mode": guidance_mode,
            "chords_in_section": chords or [],
        },
    )


def _consensus_record(
    *,
    song_id: str = SONG,
    section_id: Optional[str] = None,
    guidance_mode: Optional[str] = "chord",
    chord_sequence: Optional[tuple[str, ...]] = ("Am", "G", "F"),
    confidence: float = 1.0,
    timestamp_utc: str = "2026-06-18T01:00:00.000000Z",
    agreement: Optional[dict] = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        song_id=song_id,
        section_id=section_id or _sec(song_id, 0),
        timestamp_utc=timestamp_utc,
        consensus_output=ConsensusOutput(
            guidance_mode=guidance_mode,
            chord_sequence=chord_sequence,
            confidence=confidence,
            agreement=agreement or {"guidance_mode": 1.0, "chord_sequence": 1.0},
            votes={"guidance_mode": {"chord": 2}},
        ),
    )


# ---------------------------------------------------------------------------
# Selection logic
# ---------------------------------------------------------------------------


def test_basic_entry_with_jam_and_consensus(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_jam_record(
        chords=[{"symbol": "Am"}, {"symbol": "G"}, {"symbol": "F"}],
    ))
    store.append(_consensus_record())
    entries = list(iter_consensus_corpus(store))
    assert len(entries) == 1
    e = entries[0]
    assert e.song_id == SONG
    assert e.section_id == _sec(SONG, 0)
    assert e.ref_guidance_mode == "chord"
    assert e.ref_chord_sequence == ("Am", "G", "F")
    assert e.ref_confidence == 1.0
    assert e.latest_jam_output is not None
    assert e.latest_jam_output["guidance_mode"] == "chord"


def test_low_confidence_consensus_excluded(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_jam_record())
    store.append(_consensus_record(confidence=0.5))
    assert list(iter_consensus_corpus(store)) == []
    # Custom config can lower the bar.
    entries = list(iter_consensus_corpus(
        store, config=ConsensusCorpusConfig(min_confidence=0.4),
    ))
    assert len(entries) == 1


def test_section_without_consensus_skipped(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_jam_record())
    assert list(iter_consensus_corpus(store)) == []


def test_section_without_jam_emits_with_none(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_consensus_record())
    entries = list(iter_consensus_corpus(store))
    assert len(entries) == 1
    assert entries[0].latest_jam_output is None
    assert entries[0].jam_timestamp_utc is None


def test_require_jam_output_filters(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_consensus_record())
    assert list(iter_consensus_corpus(
        store, config=ConsensusCorpusConfig(require_jam_output=True),
    )) == []


def test_latest_consensus_wins(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_jam_record())
    # Older consensus with stale labels:
    store.append(_consensus_record(
        guidance_mode="riff",
        chord_sequence=("X",),
        timestamp_utc="2026-06-18T00:00:00.000000Z",
    ))
    # Newer consensus with correct labels:
    store.append(_consensus_record(
        guidance_mode="chord",
        chord_sequence=("Am", "G", "F"),
        timestamp_utc="2026-06-18T05:00:00.000000Z",
    ))
    entries = list(iter_consensus_corpus(store))
    assert len(entries) == 1
    assert entries[0].ref_guidance_mode == "chord"
    assert entries[0].ref_chord_sequence == ("Am", "G", "F")


def test_latest_jam_wins(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_jam_record(
        guidance_mode="riff",
        timestamp_utc="2026-06-18T00:00:00.000000Z",
    ))
    store.append(_jam_record(
        guidance_mode="chord",
        timestamp_utc="2026-06-18T05:00:00.000000Z",
    ))
    store.append(_consensus_record())
    entries = list(iter_consensus_corpus(store))
    assert entries[0].latest_jam_output["guidance_mode"] == "chord"
    assert entries[0].jam_timestamp_utc == "2026-06-18T05:00:00.000000Z"


def test_song_id_filter(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_consensus_record(song_id=SONG))
    store.append(_consensus_record(song_id=SONG2))
    all_entries = list(iter_consensus_corpus(store))
    assert {e.song_id for e in all_entries} == {SONG, SONG2}
    subset = list(iter_consensus_corpus(
        store, config=ConsensusCorpusConfig(song_id=SONG2),
    ))
    assert [e.song_id for e in subset] == [SONG2]


def test_multiple_sections_distinct_entries(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    for i in range(4):
        sid = _sec(SONG, i)
        store.append(_jam_record(section_id=sid))
        store.append(_consensus_record(section_id=sid))
    entries = list(iter_consensus_corpus(store))
    assert len(entries) == 4
    assert {e.section_id for e in entries} == {_sec(SONG, i) for i in range(4)}


def test_ref_agreement_carries_through(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_consensus_record(
        agreement={"guidance_mode": 1.0, "chord_sequence": 0.83},
    ))
    entries = list(iter_consensus_corpus(store))
    assert entries[0].ref_agreement == {
        "guidance_mode": 1.0, "chord_sequence": 0.83,
    }


# ---------------------------------------------------------------------------
# Summarise
# ---------------------------------------------------------------------------


def test_summary_empty(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    s = summarise_consensus_corpus(store)
    assert s["n_entries"] == 0
    assert s["n_unique_songs"] == 0
    assert s["mean_confidence"] == 0.0


def test_summary_shape(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    # song1: chord with jam
    store.append(_jam_record())
    store.append(_consensus_record())
    # song2: riff without jam
    store.append(_consensus_record(
        song_id=SONG2,
        section_id=_sec(SONG2, 0),
        guidance_mode="riff",
        confidence=0.9,
    ))
    s = summarise_consensus_corpus(store)
    assert s["n_entries"] == 2
    assert s["n_unique_songs"] == 2
    assert s["n_with_jam_output"] == 1
    assert s["n_without_jam_output"] == 1
    assert s["by_guidance_mode"] == {"chord": 1, "riff": 1}
    assert s["min_confidence"] == pytest.approx(0.9)
    assert s["mean_confidence"] == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _seed_two_song_store(root: Path) -> EvidenceStore:
    store = EvidenceStore(root=root)
    store.append(_jam_record())
    store.append(_consensus_record())
    store.append(_consensus_record(
        song_id=SONG2,
        section_id=_sec(SONG2, 0),
        guidance_mode="riff",
        confidence=0.9,
    ))
    return store


def test_cli_stats_text(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_two_song_store(tmp_path / "ev")
    rc = cc_main(["--store-root", str(tmp_path / "ev"), "stats"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "corpus entries:        2" in out
    assert "unique songs:          2" in out
    assert "chord" in out
    assert "riff" in out


def test_cli_stats_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_two_song_store(tmp_path / "ev")
    rc = cc_main(["--store-root", str(tmp_path / "ev"), "stats", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["n_entries"] == 2
    assert payload["by_guidance_mode"] == {"chord": 1, "riff": 1}


def test_cli_list_text(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_two_song_store(tmp_path / "ev")
    rc = cc_main(["--store-root", str(tmp_path / "ev"), "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert SONG in out
    assert SONG2 in out
    assert "[jam]" in out
    assert "[no-jam]" in out


def test_cli_list_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_two_song_store(tmp_path / "ev")
    rc = cc_main(["--store-root", str(tmp_path / "ev"), "list", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["n_entries"] == 2
    modes = {entry["ref_guidance_mode"] for entry in payload["entries"]}
    assert modes == {"chord", "riff"}


def test_cli_list_empty(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    EvidenceStore(root=tmp_path / "ev")
    rc = cc_main(["--store-root", str(tmp_path / "ev"), "list"])
    assert rc == 0
    assert "consensus corpus is empty" in capsys.readouterr().out


def test_cli_export_writes_file(tmp_path: Path) -> None:
    _seed_two_song_store(tmp_path / "ev")
    out_path = tmp_path / "out" / "corpus.json"
    rc = cc_main([
        "--store-root", str(tmp_path / "ev"),
        "export", "--output", str(out_path),
    ])
    assert rc == 0
    payload = json.loads(out_path.read_text())
    assert payload["n_entries"] == 2
    assert payload["min_confidence"] == 0.8
    assert len(payload["entries"]) == 2


def test_cli_song_id_filter(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_two_song_store(tmp_path / "ev")
    rc = cc_main([
        "--store-root", str(tmp_path / "ev"),
        "stats", "--json", "--song-id", SONG2,
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["n_entries"] == 1
    assert payload["by_guidance_mode"] == {"riff": 1}


def test_cli_require_jam_filter(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_two_song_store(tmp_path / "ev")
    rc = cc_main([
        "--store-root", str(tmp_path / "ev"),
        "stats", "--json", "--require-jam-output",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    # Only song1 had a jam_output.
    assert payload["n_entries"] == 1
    assert payload["n_with_jam_output"] == 1
    assert payload["n_without_jam_output"] == 0
