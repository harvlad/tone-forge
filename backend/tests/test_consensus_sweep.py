"""Phase 6 Consensus-Corpus Regression Gate tests.

Covers:

    * ``score_entry`` per-field match logic (incl. undecided
      consensus -> None and Jaccard near-misses)
    * ``score_consensus_corpus`` aggregate counts + rates
    * Round-trip ``dump_consensus_score`` / ``load_consensus_score``
    * ``evaluate_consensus_acceptance`` four-rule gate
    * CLI: score / compare / show
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Optional

import pytest

from bench.consensus_sweep import (
    ConsensusAcceptanceConfig,
    ConsensusCorpusScore,
    ConsensusEntryScore,
    ConsensusScoreConfig,
    dump_consensus_score,
    evaluate_consensus_acceptance,
    load_consensus_score,
    score_consensus_corpus,
    score_entry,
)
from bench.consensus_sweep.__main__ import main as cs_main
from bench.corpus_consensus.loader import ConsensusCorpusEntry
from bench.evidence import ConsensusOutput, EvidenceRecord, EvidenceStore


SONG = "abc1234567890def"
SONG2 = "fed0987654321cba"


def _sec(song: str, idx: int) -> str:
    return f"{song}:{idx:04d}"


def _entry(
    *,
    song: str = SONG,
    idx: int = 0,
    ref_guidance: Optional[str] = "chord",
    ref_chords: Optional[tuple[str, ...]] = ("Am", "G", "F"),
    jam_guidance: Optional[str] = "chord",
    jam_chords: Optional[list[str]] = None,
    confidence: float = 1.0,
) -> ConsensusCorpusEntry:
    """Construct a corpus entry for unit tests (bypasses the loader)."""
    if jam_chords is None:
        jam_chords = ["Am", "G", "F"]
    jam_output = None
    if jam_guidance is not None or jam_chords is not None:
        jam_output = {
            "guidance_mode": jam_guidance,
            "chords_in_section": [{"symbol": s} for s in (jam_chords or [])],
        }
    return ConsensusCorpusEntry(
        song_id=song,
        section_id=_sec(song, idx),
        ref_guidance_mode=ref_guidance,
        ref_chord_sequence=ref_chords,
        ref_confidence=confidence,
        latest_jam_output=jam_output,
    )


# ---------------------------------------------------------------------------
# score_entry
# ---------------------------------------------------------------------------


def test_score_entry_perfect_match() -> None:
    s = score_entry(_entry())
    assert s.guidance_mode_match == 1.0
    assert s.chord_sequence_match == 1.0
    assert s.chord_sequence_jaccard == 1.0
    assert s.jam_present is True


def test_score_entry_guidance_mode_mismatch() -> None:
    s = score_entry(_entry(jam_guidance="riff"))
    assert s.guidance_mode_match == 0.0
    assert s.chord_sequence_match == 1.0


def test_score_entry_chord_sequence_mismatch() -> None:
    s = score_entry(_entry(jam_chords=["Am", "G", "C"]))
    assert s.chord_sequence_match == 0.0
    # 2 of 3 chord symbols overlap (Am, G); union has 4: {Am,G,F,C}; Jaccard = 2/4
    assert s.chord_sequence_jaccard == pytest.approx(0.5)


def test_score_entry_undecided_consensus_excluded() -> None:
    s = score_entry(_entry(ref_guidance=None, ref_chords=None))
    assert s.guidance_mode_match is None
    assert s.chord_sequence_match is None
    assert s.chord_sequence_jaccard is None


def test_score_entry_jam_missing_field_counts_as_zero() -> None:
    s = score_entry(_entry(jam_guidance=None))
    assert s.guidance_mode_match == 0.0


def test_score_entry_no_jam_output_at_all() -> None:
    e = ConsensusCorpusEntry(
        song_id=SONG, section_id=_sec(SONG, 0),
        ref_guidance_mode="chord",
        ref_chord_sequence=("Am",),
        ref_confidence=1.0,
        latest_jam_output=None,
    )
    s = score_entry(e)
    assert s.guidance_mode_match == 0.0
    assert s.chord_sequence_match == 0.0
    assert s.chord_sequence_jaccard == 0.0
    assert s.jam_present is False


# ---------------------------------------------------------------------------
# score_consensus_corpus
# ---------------------------------------------------------------------------


def _seed_store(
    root: Path,
    *,
    entries: list[tuple[int, Optional[str], Optional[tuple[str, ...]], Optional[str], Optional[list[str]]]],
) -> EvidenceStore:
    """Quick store seeder.

    Each entry tuple is (idx, ref_guidance, ref_chords, jam_guidance, jam_chords).
    """
    store = EvidenceStore(root=root)
    for (idx, ref_g, ref_c, jam_g, jam_c) in entries:
        if jam_g is not None or jam_c is not None:
            store.append(EvidenceRecord(
                song_id=SONG, section_id=_sec(SONG, idx),
                timestamp_utc=f"2026-06-18T0{idx}:00:00.000000Z",
                jam_output={
                    "guidance_mode": jam_g,
                    "chords_in_section": [{"symbol": s} for s in (jam_c or [])],
                },
            ))
        store.append(EvidenceRecord(
            song_id=SONG, section_id=_sec(SONG, idx),
            timestamp_utc=f"2026-06-18T1{idx}:00:00.000000Z",
            consensus_output=ConsensusOutput(
                guidance_mode=ref_g,
                chord_sequence=ref_c,
                confidence=1.0,
                agreement={"guidance_mode": 1.0, "chord_sequence": 1.0},
                votes={},
            ),
        ))
    return store


def test_score_corpus_all_match(tmp_path: Path) -> None:
    _seed_store(tmp_path, entries=[
        (0, "chord", ("Am", "G", "F"), "chord", ["Am", "G", "F"]),
        (1, "chord", ("C", "G"),       "chord", ["C", "G"]),
    ])
    score = score_consensus_corpus(EvidenceStore(root=tmp_path))
    assert score.n_entries == 2
    assert score.guidance_mode_match_rate == 1.0
    assert score.chord_sequence_match_rate == 1.0
    assert score.combined_match_rate == 1.0


def test_score_corpus_mixed(tmp_path: Path) -> None:
    _seed_store(tmp_path, entries=[
        (0, "chord", ("Am", "G"),  "chord", ["Am", "G"]),       # match both
        (1, "chord", ("Am", "G"),  "riff",  ["Am", "G"]),       # gm miss
        (2, "chord", ("Am", "G"),  "chord", ["Am", "C"]),       # cs miss
    ])
    score = score_consensus_corpus(EvidenceStore(root=tmp_path))
    assert score.n_entries == 3
    assert score.guidance_mode_match_rate == pytest.approx(2 / 3)
    assert score.chord_sequence_match_rate == pytest.approx(2 / 3)
    assert score.combined_match_rate == pytest.approx(2 / 3)


def test_score_corpus_low_confidence_excluded(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path)
    store.append(EvidenceRecord(
        song_id=SONG, section_id=_sec(SONG, 0),
        timestamp_utc="2026-06-18T00:00:00.000000Z",
        jam_output={"guidance_mode": "chord", "chords_in_section": [{"symbol": "Am"}]},
    ))
    store.append(EvidenceRecord(
        song_id=SONG, section_id=_sec(SONG, 0),
        timestamp_utc="2026-06-18T01:00:00.000000Z",
        consensus_output=ConsensusOutput(
            guidance_mode="chord", chord_sequence=("Am",),
            confidence=0.5,  # below default 0.8 threshold
            agreement={}, votes={},
        ),
    ))
    score = score_consensus_corpus(EvidenceStore(root=tmp_path))
    assert score.n_entries == 0


# ---------------------------------------------------------------------------
# Round-trip serialisation
# ---------------------------------------------------------------------------


def test_score_round_trip(tmp_path: Path) -> None:
    _seed_store(tmp_path / "ev", entries=[
        (0, "chord", ("Am", "G"), "chord", ["Am", "G"]),
        (1, "chord", ("Am", "G"), "riff",  ["Am", "G"]),
    ])
    original = score_consensus_corpus(EvidenceStore(root=tmp_path / "ev"))
    path = tmp_path / "score.json"
    dump_consensus_score(original, path)
    loaded = load_consensus_score(path)
    assert loaded.n_entries == original.n_entries
    assert loaded.combined_match_rate == original.combined_match_rate
    assert len(loaded.entries) == len(original.entries)


# ---------------------------------------------------------------------------
# Acceptance gate
# ---------------------------------------------------------------------------


def _score(
    *,
    combined: float = 0.8,
    guidance: float = 0.8,
    chord: float = 0.8,
    jaccard: float = 0.8,
    wall: float = 0.01,
    entries: tuple[ConsensusEntryScore, ...] = (),
) -> ConsensusCorpusScore:
    return ConsensusCorpusScore(
        n_entries=len(entries),
        n_entries_with_jam=len(entries),
        n_guidance_evaluated=len(entries),
        n_chord_sequence_evaluated=len(entries),
        guidance_mode_match_rate=guidance,
        chord_sequence_match_rate=chord,
        chord_sequence_mean_jaccard=jaccard,
        combined_match_rate=combined,
        score_wall_seconds=wall,
        entries=entries,
    )


def test_gate_accepts_strict_improvement() -> None:
    base = _score(combined=0.80)
    cand = _score(combined=0.85)
    v = evaluate_consensus_acceptance(cand, base)
    assert v.accepted is True
    assert v.combined_delta == pytest.approx(0.05)


def test_gate_rejects_no_op_when_strict_improvement_required() -> None:
    base = _score(combined=0.80)
    cand = _score(combined=0.80)
    v = evaluate_consensus_acceptance(cand, base)
    assert v.accepted is False
    assert "did not improve" in (v.rejection_reason or "")


def test_gate_allows_neutral_when_configured() -> None:
    base = _score(combined=0.80)
    cand = _score(combined=0.80)
    v = evaluate_consensus_acceptance(cand, base, ConsensusAcceptanceConfig(
        corpus_must_strictly_improve=False,
    ))
    assert v.accepted is True


def test_gate_rejects_regression_over_threshold() -> None:
    base = _score(combined=0.80)
    cand = _score(combined=0.78)
    v = evaluate_consensus_acceptance(cand, base, ConsensusAcceptanceConfig(
        corpus_must_strictly_improve=False,
        max_combined_regression_pp=1.0,
    ))
    assert v.accepted is False
    assert "regressed" in (v.rejection_reason or "")


def test_gate_allows_tiny_regression_under_threshold() -> None:
    base = _score(combined=0.800)
    cand = _score(combined=0.795)
    v = evaluate_consensus_acceptance(cand, base, ConsensusAcceptanceConfig(
        corpus_must_strictly_improve=False,
        max_combined_regression_pp=1.0,
    ))
    assert v.accepted is True


def test_gate_rejects_section_regression() -> None:
    base = _score(combined=0.80, entries=(
        ConsensusEntryScore(
            song_id=SONG, section_id=_sec(SONG, 0),
            ref_confidence=1.0,
            guidance_mode_match=1.0,
            chord_sequence_match=1.0,
            chord_sequence_jaccard=1.0,
            jam_present=True,
        ),
    ))
    cand = _score(combined=0.90, entries=(
        ConsensusEntryScore(
            song_id=SONG, section_id=_sec(SONG, 0),
            ref_confidence=1.0,
            guidance_mode_match=0.0,   # regressed
            chord_sequence_match=1.0,
            chord_sequence_jaccard=1.0,
            jam_present=True,
        ),
    ))
    v = evaluate_consensus_acceptance(cand, base, ConsensusAcceptanceConfig(
        corpus_must_strictly_improve=False,
        max_section_regressions=0,
    ))
    assert v.accepted is False
    assert "section(s) regressed" in (v.rejection_reason or "")
    assert "guidance_mode" in v.regressing_sections[0]


def test_gate_allows_section_regression_if_under_limit() -> None:
    base = _score(combined=0.80, entries=(
        ConsensusEntryScore(
            song_id=SONG, section_id=_sec(SONG, 0),
            ref_confidence=1.0,
            guidance_mode_match=1.0,
            chord_sequence_match=1.0,
            chord_sequence_jaccard=1.0,
            jam_present=True,
        ),
    ))
    cand = _score(combined=0.90, entries=(
        ConsensusEntryScore(
            song_id=SONG, section_id=_sec(SONG, 0),
            ref_confidence=1.0,
            guidance_mode_match=0.0,
            chord_sequence_match=1.0,
            chord_sequence_jaccard=1.0,
            jam_present=True,
        ),
    ))
    v = evaluate_consensus_acceptance(cand, base, ConsensusAcceptanceConfig(
        corpus_must_strictly_improve=False,
        max_section_regressions=1,
    ))
    assert v.accepted is True


def test_gate_rejects_runtime_blowout() -> None:
    base = _score(combined=0.80, wall=1.0)
    cand = _score(combined=0.90, wall=3.0)
    v = evaluate_consensus_acceptance(cand, base, ConsensusAcceptanceConfig(
        max_runtime_factor=2.0,
    ))
    assert v.accepted is False
    assert "score_wall_seconds" in (v.rejection_reason or "")


def test_gate_per_field_deltas_populated() -> None:
    base = _score(combined=0.80, guidance=0.8, chord=0.8, jaccard=0.8)
    cand = _score(combined=0.85, guidance=0.9, chord=0.8, jaccard=0.85)
    v = evaluate_consensus_acceptance(cand, base)
    deltas = dict(v.per_field_deltas)
    assert deltas["guidance_mode_match_rate"] == pytest.approx(0.1)
    assert deltas["chord_sequence_match_rate"] == pytest.approx(0.0)
    assert deltas["chord_sequence_mean_jaccard"] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_score_writes_file(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_store(tmp_path / "ev", entries=[
        (0, "chord", ("Am", "G"), "chord", ["Am", "G"]),
    ])
    out = tmp_path / "score.json"
    rc = cs_main([
        "--store-root", str(tmp_path / "ev"),
        "score", "--output", str(out),
    ])
    assert rc == 0
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["n_entries"] == 1
    assert payload["combined_match_rate"] == 1.0


def test_cli_score_default_stdout_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _seed_store(tmp_path / "ev", entries=[
        (0, "chord", ("Am", "G"), "chord", ["Am", "G"]),
    ])
    rc = cs_main(["--store-root", str(tmp_path / "ev"), "score"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["n_entries"] == 1


def test_cli_compare_accept(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    # Seed two stores; candidate has strict improvement
    base_store = _seed_store(tmp_path / "base", entries=[
        (0, "chord", ("Am",), "riff", ["Am"]),  # gm miss
    ])
    cand_store = _seed_store(tmp_path / "cand", entries=[
        (0, "chord", ("Am",), "chord", ["Am"]),  # match
    ])
    base_path = tmp_path / "base.json"
    cand_path = tmp_path / "cand.json"
    # Pin wall seconds: real scoring of a 1-entry corpus takes microseconds,
    # so the gate's 2x runtime rule would compare pure timing noise (flaky
    # under load). Equal values make the runtime rule deterministic.
    dump_consensus_score(
        replace(score_consensus_corpus(base_store), score_wall_seconds=1.0),
        base_path,
    )
    dump_consensus_score(
        replace(score_consensus_corpus(cand_store), score_wall_seconds=1.0),
        cand_path,
    )

    rc = cs_main([
        "consensus_sweep_dummy", "compare",
        "--candidate", str(cand_path),
        "--baseline", str(base_path),
    ] if False else [
        "compare",
        "--candidate", str(cand_path),
        "--baseline", str(base_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ACCEPT" in out


def test_cli_compare_reject(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    base_store = _seed_store(tmp_path / "base", entries=[
        (0, "chord", ("Am",), "chord", ["Am"]),  # match
    ])
    cand_store = _seed_store(tmp_path / "cand", entries=[
        (0, "chord", ("Am",), "riff", ["Am"]),  # regressed
    ])
    base_path = tmp_path / "base.json"
    cand_path = tmp_path / "cand.json"
    dump_consensus_score(score_consensus_corpus(base_store), base_path)
    dump_consensus_score(score_consensus_corpus(cand_store), cand_path)

    rc = cs_main([
        "compare",
        "--candidate", str(cand_path),
        "--baseline", str(base_path),
    ])
    assert rc == 1
    out = capsys.readouterr().out
    assert "REJECT" in out


def test_cli_compare_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    base_store = _seed_store(tmp_path / "base", entries=[
        (0, "chord", ("Am",), "chord", ["Am"]),
    ])
    cand_store = _seed_store(tmp_path / "cand", entries=[
        (0, "chord", ("Am",), "chord", ["Am"]),
    ])
    base_path = tmp_path / "base.json"
    cand_path = tmp_path / "cand.json"
    # Pin wall seconds so the runtime rule is deterministic (see
    # test_cli_compare_accept).
    dump_consensus_score(
        replace(score_consensus_corpus(base_store), score_wall_seconds=1.0),
        base_path,
    )
    dump_consensus_score(
        replace(score_consensus_corpus(cand_store), score_wall_seconds=1.0),
        cand_path,
    )

    rc = cs_main([
        "compare",
        "--candidate", str(cand_path),
        "--baseline", str(base_path),
        "--allow-neutral", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is True
    assert payload["combined_delta"] == pytest.approx(0.0)


def test_cli_show(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    store = _seed_store(tmp_path / "ev", entries=[
        (0, "chord", ("Am",), "chord", ["Am"]),
    ])
    score = score_consensus_corpus(store)
    path = tmp_path / "score.json"
    dump_consensus_score(score, path)
    rc = cs_main(["show", str(path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "entries:" in out
    assert "combined_match_rate" in out
