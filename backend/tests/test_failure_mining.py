"""Phase 4 Failure Mining tests.

Covers:

    * ``mine_failures`` agreement / disagreement rules
    * Low-confidence consensus filter
    * Missing jam_output or missing consensus -> skipped
    * Chord-sequence equivalence (list vs tuple)
    * CLI: ``report`` and ``summary`` (text + JSON)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest

from bench.evidence import (
    ConsensusOutput,
    EvidenceRecord,
    EvidenceStore,
    ReferenceSource,
)
from bench.failures import FailureMiningConfig, mine_failures
from bench.failures.__main__ import main as failures_main


SONG = "abc1234567890def"
SEC = f"{SONG}:0000"


def _jam_record(
    *,
    guidance_mode: str = "chord",
    chords: Optional[list[dict]] = None,
    timestamp_utc: str = "2026-06-18T00:00:00.000000Z",
    section_id: str = SEC,
) -> EvidenceRecord:
    return EvidenceRecord(
        song_id=SONG,
        section_id=section_id,
        timestamp_utc=timestamp_utc,
        jam_output={
            "guidance_mode": guidance_mode,
            "chords_in_section": chords or [],
        },
    )


def _consensus_record(
    *,
    guidance_mode: Optional[str] = "chord",
    chord_sequence: Optional[tuple[str, ...]] = ("Am", "G", "F"),
    confidence: float = 1.0,
    timestamp_utc: str = "2026-06-18T01:00:00.000000Z",
    section_id: str = SEC,
) -> EvidenceRecord:
    return EvidenceRecord(
        song_id=SONG,
        section_id=section_id,
        timestamp_utc=timestamp_utc,
        consensus_output=ConsensusOutput(
            guidance_mode=guidance_mode,
            chord_sequence=chord_sequence,
            confidence=confidence,
            agreement={"guidance_mode": 1.0, "chord_sequence": 1.0},
            votes={"guidance_mode": {"chord": 2}, "chord_sequence": {"[Am, G, F]": 2}},
        ),
    )


# ---------------------------------------------------------------------------
# Rule logic
# ---------------------------------------------------------------------------


def test_agreement_yields_no_failure(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_jam_record(
        guidance_mode="chord",
        chords=[
            {"symbol": "Am"},
            {"symbol": "G"},
            {"symbol": "F"},
        ],
    ))
    store.append(_consensus_record())
    assert mine_failures(store) == []


def test_guidance_mode_disagreement_yields_failure(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_jam_record(
        guidance_mode="riff",
        chords=[{"symbol": "Am"}, {"symbol": "G"}, {"symbol": "F"}],
    ))
    store.append(_consensus_record(guidance_mode="chord"))
    rows = mine_failures(store)
    assert len(rows) == 1
    assert rows[0].failure_type == "guidance_mode_mismatch"
    assert rows[0].jam_value == "riff"
    assert rows[0].consensus_value == "chord"
    assert rows[0].consensus_confidence == 1.0


def test_chord_sequence_disagreement(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_jam_record(chords=[
        {"symbol": "Am"}, {"symbol": "G"}, {"symbol": "C"},
    ]))
    store.append(_consensus_record(chord_sequence=("Am", "G", "F")))
    rows = mine_failures(store)
    assert any(r.failure_type == "chord_sequence_mismatch" for r in rows)
    row = next(r for r in rows if r.failure_type == "chord_sequence_mismatch")
    assert row.jam_value == ("Am", "G", "C")
    assert row.consensus_value == ("Am", "G", "F")


def test_multiple_field_disagreements_produce_multiple_rows(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_jam_record(
        guidance_mode="lead",
        chords=[{"symbol": "X"}],
    ))
    store.append(_consensus_record(
        guidance_mode="chord",
        chord_sequence=("Am",),
    ))
    rows = mine_failures(store)
    failure_types = {r.failure_type for r in rows}
    assert failure_types == {"guidance_mode_mismatch", "chord_sequence_mismatch"}


def test_low_confidence_consensus_filtered_out(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_jam_record(guidance_mode="riff"))
    store.append(_consensus_record(guidance_mode="chord", confidence=0.5))
    assert mine_failures(store) == []
    # With a lower bar the disagreement does show.
    rows = mine_failures(store, config=FailureMiningConfig(
        min_consensus_confidence=0.4,
    ))
    assert any(r.failure_type == "guidance_mode_mismatch" for r in rows)


def test_missing_jam_skipped(tmp_path: Path) -> None:
    """A section with only a consensus record yields no failure (engine wasn't asked)."""
    store = EvidenceStore(root=tmp_path)
    store.append(_consensus_record())
    assert mine_failures(store) == []


def test_missing_consensus_skipped(tmp_path: Path) -> None:
    """A section with only a JAM output yields no failure (nothing to disagree with)."""
    store = EvidenceStore(root=tmp_path)
    store.append(_jam_record(guidance_mode="riff"))
    assert mine_failures(store) == []


def test_latest_jam_wins_over_older(tmp_path: Path) -> None:
    """Multiple jam rows: most recent is what's compared against consensus."""
    store = EvidenceStore(root=tmp_path)
    # Old wrong, new right.
    store.append(_jam_record(
        guidance_mode="riff",
        timestamp_utc="2026-06-18T00:00:00.000000Z",
    ))
    store.append(_jam_record(
        guidance_mode="chord",
        chords=[{"symbol": "Am"}, {"symbol": "G"}, {"symbol": "F"}],
        timestamp_utc="2026-06-18T02:00:00.000000Z",
    ))
    store.append(_consensus_record())
    assert mine_failures(store) == []


def test_jam_missing_field_counts_as_failure(tmp_path: Path) -> None:
    """If the engine omits a field the consensus carries, that's a failure."""
    store = EvidenceStore(root=tmp_path)
    store.append(EvidenceRecord(
        song_id=SONG, section_id=SEC,
        timestamp_utc="2026-06-18T00:00:00.000000Z",
        jam_output={"guidance_mode": "chord"},  # no chords_in_section
    ))
    store.append(_consensus_record(chord_sequence=("Am",)))
    rows = mine_failures(store)
    assert len(rows) == 1
    assert rows[0].failure_type == "chord_sequence_mismatch"
    assert rows[0].jam_value is None
    assert rows[0].consensus_value == ("Am",)


def test_consensus_none_field_is_no_failure(tmp_path: Path) -> None:
    """A consensus that didn't decide a field doesn't fault the engine on it."""
    store = EvidenceStore(root=tmp_path)
    store.append(_jam_record(guidance_mode="chord",
                             chords=[{"symbol": "Am"}]))
    store.append(_consensus_record(chord_sequence=None))  # no consensus on chords
    assert mine_failures(store) == []


def test_multiple_sections_distinct_rows(tmp_path: Path) -> None:
    """Each section that disagrees on guidance_mode emits one row.

    The jam record carries an empty ``chords_in_section`` (engine
    didn't supply one), and the consensus carries a chord_sequence,
    so each section also produces a chord_sequence_mismatch row.
    That's 2 failures per section x 3 sections = 6 rows.
    """
    store = EvidenceStore(root=tmp_path)
    for i in range(3):
        sid = f"{SONG}:{i:04d}"
        store.append(_jam_record(guidance_mode="riff", section_id=sid))
        store.append(_consensus_record(guidance_mode="chord", section_id=sid))
    rows = mine_failures(store)
    assert len(rows) == 6
    # Three sections cover the guidance_mode_mismatch class.
    guidance_rows = [r for r in rows if r.failure_type == "guidance_mode_mismatch"]
    assert len(guidance_rows) == 3
    assert {r.section_id for r in guidance_rows} == {
        f"{SONG}:0000", f"{SONG}:0001", f"{SONG}:0002",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _seed_disagreeing_store(root: Path) -> EvidenceStore:
    store = EvidenceStore(root=root)
    store.append(_jam_record(guidance_mode="riff"))
    store.append(_consensus_record(guidance_mode="chord"))
    return store


def test_cli_report_text(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_disagreeing_store(tmp_path / "ev")
    rc = failures_main(["--store-root", str(tmp_path / "ev"), "report"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "failures:" in out
    assert "guidance_mode_mismatch" in out


def test_cli_report_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_disagreeing_store(tmp_path / "ev")
    rc = failures_main(["--store-root", str(tmp_path / "ev"), "report", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["n_failures"] >= 1
    assert "guidance_mode_mismatch" in payload["by_failure_type"]
    guidance_rows = [
        r for r in payload["rows"]
        if r["failure_type"] == "guidance_mode_mismatch"
    ]
    assert guidance_rows
    assert guidance_rows[0]["consensus_value"] == "chord"
    assert guidance_rows[0]["jam_value"] == "riff"


def test_cli_summary(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_disagreeing_store(tmp_path / "ev")
    rc = failures_main(["--store-root", str(tmp_path / "ev"), "summary", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["n_unique_songs_failing"] == 1
    assert payload["n_unique_sections_failing"] >= 1


def test_cli_report_empty_store(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    EvidenceStore(root=tmp_path / "ev")
    rc = failures_main(["--store-root", str(tmp_path / "ev"), "report"])
    assert rc == 0
    assert "no engine-vs-consensus disagreements" in capsys.readouterr().out
