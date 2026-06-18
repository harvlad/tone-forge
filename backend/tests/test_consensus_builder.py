"""Phase 3 Consensus Builder tests.

Covers:

    * ``build_consensus_for_section`` rule logic:
        - 2+ agreement -> confidence 1.0
        - majority + minority -> confidence == majority ratio
        - clean tie -> no consensus (None, confidence 0.0)
        - single source -> singleton consensus, agreement 1.0
        - missing field on some sources -> ignored, not counted as 0
        - tempo bucketing
        - chord_sequence normalization
    * ``build_consensus_for_store`` integration:
        - one consensus record appended per (song_id, section_id)
        - existing reference rows preserved
        - re-running adds *new* consensus rows (idempotent append)
    * CLI: ``build`` / ``show`` / ``inspect``
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pytest

from bench.consensus import (
    ConsensusBuilderConfig,
    build_consensus_for_section,
    build_consensus_for_store,
)
from bench.consensus.__main__ import main as consensus_main
from bench.evidence import EvidenceRecord, EvidenceStore, ReferenceSource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ref_record(
    song_id: str,
    section_id: str,
    source: str,
    labels: dict,
    *,
    timestamp_utc: str = "2026-06-18T00:00:00.000000Z",
    version: str = "v1",
) -> EvidenceRecord:
    return EvidenceRecord(
        song_id=song_id,
        section_id=section_id,
        timestamp_utc=timestamp_utc,
        jam_output={},
        reference_sources=(ReferenceSource(
            source=source,
            version=version,
            fetched_at_utc=timestamp_utc,
            labels=labels,
        ),),
    )


SONG = "abc1234567890def"
SEC = f"{SONG}:0000"


# ---------------------------------------------------------------------------
# Vote-rule unit tests
# ---------------------------------------------------------------------------


def test_two_sources_agree_confidence_one() -> None:
    recs = [
        _ref_record(SONG, SEC, "songsterr", {"guidance_mode": "chord"}),
        _ref_record(SONG, SEC, "ultimate_guitar", {"guidance_mode": "chord"}),
    ]
    c = build_consensus_for_section(recs)
    assert c is not None
    assert c.guidance_mode == "chord"
    assert c.confidence == 1.0
    assert c.agreement["guidance_mode"] == 1.0
    assert c.votes["guidance_mode"] == {"chord": 2}


def test_three_sources_split_majority_wins() -> None:
    recs = [
        _ref_record(SONG, SEC, "a", {"guidance_mode": "chord"}),
        _ref_record(SONG, SEC, "b", {"guidance_mode": "chord"}),
        _ref_record(SONG, SEC, "c", {"guidance_mode": "riff"}),
    ]
    c = build_consensus_for_section(recs)
    assert c is not None
    assert c.guidance_mode == "chord"
    # 2/3 majority -> agreement 0.667, confidence == that
    assert c.confidence == pytest.approx(2 / 3, abs=1e-6)
    assert c.votes["guidance_mode"] == {"chord": 2, "riff": 1}


def test_clean_tie_yields_no_consensus() -> None:
    recs = [
        _ref_record(SONG, SEC, "a", {"guidance_mode": "chord"}),
        _ref_record(SONG, SEC, "b", {"guidance_mode": "riff"}),
    ]
    c = build_consensus_for_section(recs)
    assert c is not None
    # 1/2 each: agreement == 0.5, but floor=0.5 so no winner.
    assert c.guidance_mode is None
    assert c.confidence == 0.0
    assert c.votes["guidance_mode"] == {"chord": 1, "riff": 1}


def test_single_source_is_unanimous() -> None:
    recs = [_ref_record(SONG, SEC, "manual", {"guidance_mode": "lead"})]
    c = build_consensus_for_section(recs)
    assert c is not None
    assert c.guidance_mode == "lead"
    assert c.confidence == 1.0


def test_no_sources_returns_none() -> None:
    assert build_consensus_for_section([]) is None
    # Records without reference_sources also yield None.
    rec_no_ref = EvidenceRecord(song_id=SONG, section_id=SEC,
                                 timestamp_utc="2026-06-18T00:00:00Z",
                                 jam_output={"guidance_mode": "chord"})
    assert build_consensus_for_section([rec_no_ref]) is None


def test_missing_field_does_not_drag_confidence() -> None:
    """One source supplies only guidance_mode, another supplies only tempo.

    Each field is scored independently across the sources that
    supply it. Confidence is min(agreement) across keys that *had
    data*, not across all keys.
    """
    recs = [
        _ref_record(SONG, SEC, "a", {"guidance_mode": "chord"}),
        _ref_record(SONG, SEC, "b", {"tempo_bpm": 120.0}),
    ]
    c = build_consensus_for_section(recs)
    assert c is not None
    assert c.guidance_mode == "chord"
    # tempo_bpm: 1/1 vote -> agreement 1.0, but wait — only one source
    # supplied tempo. With floor=0.5 strict, 1.0 > 0.5 so it wins.
    # guidance_mode: 1/1 from source 'a'. Both confidences = 1.0.
    assert c.confidence == 1.0


def test_chord_sequence_exact_match_required() -> None:
    recs = [
        _ref_record(SONG, SEC, "a", {"chord_sequence": ["Am", "G", "F"]}),
        _ref_record(SONG, SEC, "b", {"chord_sequence": ["Am", "G", "F"]}),
        _ref_record(SONG, SEC, "c", {"chord_sequence": ["Am", "G", "F", "C"]}),
    ]
    c = build_consensus_for_section(recs)
    assert c is not None
    assert c.chord_sequence == ("Am", "G", "F")
    assert c.confidence == pytest.approx(2 / 3, abs=1e-6)


def test_tempo_bucketing_lumps_close_values() -> None:
    recs = [
        _ref_record(SONG, SEC, "a", {"tempo_bpm": 119.7}),
        _ref_record(SONG, SEC, "b", {"tempo_bpm": 120.3}),  # round to 120
        _ref_record(SONG, SEC, "c", {"tempo_bpm": 80.0}),
    ]
    c = build_consensus_for_section(recs)
    assert c is not None
    # 119.7 and 120.3 both bucket to "120.000" at bucket=1 BPM
    assert c.agreement["tempo_bpm"] == pytest.approx(2 / 3, abs=1e-6)


def test_tempo_zero_or_invalid_dropped() -> None:
    recs = [
        _ref_record(SONG, SEC, "a", {"tempo_bpm": 0.0}),
        _ref_record(SONG, SEC, "b", {"tempo_bpm": "not a tempo"}),
        _ref_record(SONG, SEC, "c", {"tempo_bpm": 120.0}),
    ]
    c = build_consensus_for_section(recs)
    assert c is not None
    # Only the 120.0 voter survives normalization.
    assert c.votes["tempo_bpm"] == {"120.000": 1}


def test_per_field_agreement_breakdown() -> None:
    """Confidence is min(agreement) so weakest *decided* field drags it down."""
    recs = [
        _ref_record(SONG, SEC, "a", {
            "guidance_mode": "chord",
            "chord_sequence": ["Am", "G"],
        }),
        _ref_record(SONG, SEC, "b", {
            "guidance_mode": "chord",
            "chord_sequence": ["Am", "G"],
        }),
        _ref_record(SONG, SEC, "c", {
            "guidance_mode": "chord",
            "chord_sequence": ["Am", "G"],
        }),
        _ref_record(SONG, SEC, "d", {
            "guidance_mode": "chord",
            "chord_sequence": ["F", "G"],   # disagrees
        }),
    ]
    c = build_consensus_for_section(recs)
    assert c is not None
    # guidance_mode: 4/4 -> 1.0
    # chord_sequence: 3/4 -> 0.75, crosses floor=0.5 so decided
    assert c.guidance_mode == "chord"
    assert c.chord_sequence == ("Am", "G")
    assert c.agreement["guidance_mode"] == 1.0
    assert c.agreement["chord_sequence"] == 0.75
    assert c.confidence == 0.75  # min of (1.0, 0.75)


def test_split_plurality_does_not_decide_chord_sequence() -> None:
    """Plurality at-or-below floor leaves chord_sequence undecided.

    2/4 = 0.5 plurality with floor=0.5 means no value crossed the
    floor strictly. ``chord_sequence`` stays None and is excluded
    from the confidence calc.
    """
    recs = [
        _ref_record(SONG, SEC, "a", {
            "guidance_mode": "chord",
            "chord_sequence": ["Am", "G"],
        }),
        _ref_record(SONG, SEC, "b", {
            "guidance_mode": "chord",
            "chord_sequence": ["Am", "G"],
        }),
        _ref_record(SONG, SEC, "c", {
            "guidance_mode": "chord",
            "chord_sequence": ["Am", "C"],
        }),
        _ref_record(SONG, SEC, "d", {
            "guidance_mode": "chord",
            "chord_sequence": ["F", "G"],
        }),
    ]
    c = build_consensus_for_section(recs)
    assert c is not None
    assert c.guidance_mode == "chord"
    assert c.chord_sequence is None
    # Only decided field is guidance_mode (1.0); chord_sequence
    # plurality 0.5 didn't cross the strict floor.
    assert c.confidence == 1.0
    assert c.agreement["chord_sequence"] == 0.5


# ---------------------------------------------------------------------------
# Store integration
# ---------------------------------------------------------------------------


def test_build_consensus_for_store_appends_one_per_section(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    # Two songs, two sections each, two reference sources each.
    for song in ("aaa1111111111aaa", "bbb2222222222bbb"):
        for sec_idx in range(2):
            sid = f"{song}:{sec_idx:04d}"
            store.append(_ref_record(song, sid, "songsterr", {"guidance_mode": "chord"}))
            store.append(_ref_record(song, sid, "ultimate_guitar", {"guidance_mode": "chord"}))

    n = build_consensus_for_store(store, timestamp_utc="2026-06-19T00:00:00.000000Z")
    assert n == 4  # 2 songs * 2 sections each

    consensus_records = [r for r in store.iter_records() if r.consensus_output is not None]
    assert len(consensus_records) == 4
    assert all(r.consensus_output.confidence == 1.0 for r in consensus_records)


def test_build_consensus_skips_jam_only_sections(tmp_path: Path) -> None:
    """Sections with jam_output but no references shouldn't get a consensus row."""
    store = EvidenceStore(root=tmp_path)
    store.append(EvidenceRecord(
        song_id=SONG, section_id=SEC,
        timestamp_utc="2026-06-18T00:00:00.000000Z",
        jam_output={"guidance_mode": "chord"},
    ))
    n = build_consensus_for_store(store)
    assert n == 0


def test_re_running_appends_fresh_consensus(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(_ref_record(SONG, SEC, "a", {"guidance_mode": "chord"}))
    store.append(_ref_record(SONG, SEC, "b", {"guidance_mode": "chord"}))

    n1 = build_consensus_for_store(store, timestamp_utc="2026-06-18T00:00:00.000000Z")
    n2 = build_consensus_for_store(store, timestamp_utc="2026-06-19T00:00:00.000000Z")
    assert n1 == 1
    assert n2 == 1
    consensus_records = [r for r in store.iter_records() if r.consensus_output is not None]
    assert len(consensus_records) == 2  # both kept


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _seed_two_source_store(root: Path) -> EvidenceStore:
    store = EvidenceStore(root=root)
    store.append(_ref_record(SONG, SEC, "songsterr",
                             {"guidance_mode": "chord",
                              "chord_sequence": ["Am", "G", "F"]}))
    store.append(_ref_record(SONG, SEC, "ultimate_guitar",
                             {"guidance_mode": "chord",
                              "chord_sequence": ["Am", "G", "F"]}))
    return store


def test_cli_build(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_two_source_store(tmp_path / "ev")
    rc = consensus_main(["--store-root", str(tmp_path / "ev"), "build"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wrote 1 consensus records" in out


def test_cli_show_returns_one(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_two_source_store(tmp_path / "ev")
    consensus_main(["--store-root", str(tmp_path / "ev"), "build"])
    capsys.readouterr()  # clear build output
    rc = consensus_main(["--store-root", str(tmp_path / "ev"), "show"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    record = json.loads(out)
    assert record["song_id"] == SONG
    assert record["consensus_output"]["guidance_mode"] == "chord"
    assert record["consensus_output"]["confidence"] == 1.0


def test_cli_show_no_consensus_returns_1(tmp_path: Path,
                                          capsys: pytest.CaptureFixture) -> None:
    EvidenceStore(root=tmp_path / "ev")
    rc = consensus_main(["--store-root", str(tmp_path / "ev"), "show"])
    assert rc == 1
    assert "no consensus" in capsys.readouterr().err


def test_cli_show_confidence_filter(tmp_path: Path,
                                     capsys: pytest.CaptureFixture) -> None:
    store = EvidenceStore(root=tmp_path / "ev")
    # Two-way disagreement -> confidence 0.0
    store.append(_ref_record(SONG, SEC, "a", {"guidance_mode": "chord"}))
    store.append(_ref_record(SONG, SEC, "b", {"guidance_mode": "riff"}))
    consensus_main(["--store-root", str(tmp_path / "ev"), "build"])
    capsys.readouterr()
    # No filter: should print
    consensus_main(["--store-root", str(tmp_path / "ev"), "show"])
    assert capsys.readouterr().out.strip()
    # confidence-min 0.5 should hide it (confidence is 0.0)
    consensus_main([
        "--store-root", str(tmp_path / "ev"),
        "show", "--confidence-min", "0.5",
    ])
    assert capsys.readouterr().out.strip() == ""


def test_cli_inspect_dumps_breakdown(tmp_path: Path,
                                      capsys: pytest.CaptureFixture) -> None:
    _seed_two_source_store(tmp_path / "ev")
    rc = consensus_main([
        "--store-root", str(tmp_path / "ev"),
        "inspect", "--song-id", SONG, "--section-id", SEC,
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "consensus_mode:    chord" in out
    assert "confidence:        1.000" in out
    assert "votes:" in out
    assert "'chord'=2" in out


def test_cli_inspect_no_records_for_section(tmp_path: Path,
                                             capsys: pytest.CaptureFixture) -> None:
    EvidenceStore(root=tmp_path / "ev")
    rc = consensus_main([
        "--store-root", str(tmp_path / "ev"),
        "inspect", "--song-id", SONG, "--section-id", SEC,
    ])
    assert rc == 1
