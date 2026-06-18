"""Phase 9 — Future-ML compatibility layer tests.

The ML module's job is to expose a stable, JSON-clean view over the
evidence store without modifying it. Tests cover:

  * Schema validation (good store, bad store).
  * Hard vs semi-supervised filtering at the confidence threshold.
  * Feature extraction from jam_output, including the forward-compat
    ``extra`` bucket.
  * Stats roll-up across a small store.
  * Filters: song_id, date_prefix.
  * CLI smoke for ``validate`` / ``stats`` / ``dump``.
  * Dispatcher routing of ``python -m bench ml``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.evidence.schema import (
    ConsensusOutput,
    EvidenceRecord,
)
from bench.evidence.store import EvidenceStore
from bench.ml import (
    MLDatasetConfig,
    SchemaValidationError,
    compute_dataset_stats,
    iter_ml_examples,
    validate_store_schema,
)
from bench.ml.__main__ import main as ml_main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _record(
    *,
    song_id: str,
    section_idx: int,
    timestamp: str,
    jam: dict | None = None,
    consensus: ConsensusOutput | None = None,
    extra: dict | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        song_id=song_id,
        section_id=f"{song_id}:{section_idx:04d}",
        timestamp_utc=timestamp,
        jam_output=jam or {},
        reference_sources=(),
        consensus_output=consensus,
        corrections=(),
        extra=extra or {},
    )


def _make_consensus(
    *,
    guidance_mode: str = "chord",
    chord_sequence: tuple[str, ...] | None = ("C", "G", "Am", "F"),
    confidence: float = 0.9,
) -> ConsensusOutput:
    return ConsensusOutput(
        guidance_mode=guidance_mode,
        chord_sequence=chord_sequence,
        confidence=confidence,
        agreement={"guidance_mode": 1.0, "chord_sequence": 0.8},
        votes={"guidance_mode": {guidance_mode: 3}},
    )


@pytest.fixture
def populated_store(tmp_path: Path) -> EvidenceStore:
    """Small evidence store with two songs, mixed confidence."""
    store = EvidenceStore(root=tmp_path)
    # Song A, section 0: hard label (conf 0.95), with jam features.
    store.append(_record(
        song_id="songA", section_idx=0,
        timestamp="2026-06-18T10:00:00Z",
        jam={
            "guidance_mode": "chord",
            "guidance_confidence": 0.88,
            "key": "C",
            "tempo_bpm": 120.0,
            "chords_in_section": [
                {"symbol": "C", "start_s": 0.0},
                {"symbol": "G", "start_s": 2.0},
            ],
        },
        consensus=_make_consensus(confidence=0.95),
        extra={"audio_fp": "ab12"},
    ))
    # Song A, section 1: semi-supervised (conf 0.6).
    store.append(_record(
        song_id="songA", section_idx=1,
        timestamp="2026-06-18T10:01:00Z",
        jam={"guidance_mode": "riff", "tempo_bpm": 120.0},
        consensus=_make_consensus(
            guidance_mode="riff",
            chord_sequence=None,
            confidence=0.6,
        ),
    ))
    # Song B, section 0: hard label, lead.
    store.append(_record(
        song_id="songB", section_idx=0,
        timestamp="2026-06-19T11:00:00Z",
        jam={"guidance_mode": "lead", "key": "Am"},
        consensus=_make_consensus(
            guidance_mode="lead",
            chord_sequence=("Am", "Dm", "E7"),
            confidence=0.92,
        ),
    ))
    # Song B, section 1: jam_output only, no consensus → skipped.
    store.append(_record(
        song_id="songB", section_idx=1,
        timestamp="2026-06-19T11:01:00Z",
        jam={"guidance_mode": "chord"},
        consensus=None,
    ))
    return store


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_validate_store_schema_clean(populated_store: EvidenceStore) -> None:
    # Should return without raising.
    validate_store_schema(populated_store)


def test_validate_store_schema_rejects_mismatched_version() -> None:
    """If a record with a future schema_version slips past iter_records
    (e.g. through a test double or future loader change), validate
    must reject it loudly rather than silently feed the ML pipeline.
    """
    bad = EvidenceRecord(
        song_id="x", section_id="x:0001",
        timestamp_utc="2026-06-18T10:00:00Z",
        schema_version=999,
    )

    class _MockStore:
        def iter_records(self):
            yield bad

    with pytest.raises(SchemaValidationError):
        validate_store_schema(_MockStore())  # type: ignore[arg-type]


def test_unsupported_schema_lines_are_quarantined_on_disk(tmp_path: Path) -> None:
    """A hand-written future-schema line on disk should be skipped
    by the store loader (forward-safety) rather than crashing the
    ML pipeline.
    """
    store = EvidenceStore(root=tmp_path)
    bad_line = json.dumps({
        "schema_version": 999,
        "song_id": "x",
        "section_id": "x:0001",
        "timestamp_utc": "2026-06-18T10:00:00Z",
        "jam_output": {},
        "reference_sources": [],
        "consensus_output": None,
        "corrections": [],
        "extra": {},
    })
    (tmp_path / "2026-06-18.jsonl").write_text(bad_line + "\n")
    # Loader silently quarantines unsupported schema lines.
    assert store.count() == 0
    # Validation over an empty (after quarantine) store is clean.
    validate_store_schema(store)


def test_schema_validation_error_is_value_error() -> None:
    assert issubclass(SchemaValidationError, ValueError)


# ---------------------------------------------------------------------------
# iter_ml_examples — hard/semi gating
# ---------------------------------------------------------------------------


def test_iter_ml_examples_hard_only_default(populated_store: EvidenceStore) -> None:
    examples = list(iter_ml_examples(populated_store))
    # songA:0000 (0.95) and songB:0000 (0.92) qualify; songA:0001 is
    # 0.6 (below 0.8 floor) and songB:0001 has no consensus.
    assert len(examples) == 2
    assert all(ex.has_hard_label for ex in examples)
    assert {ex.provenance["section_id"] for ex in examples} == {
        "songA:0000", "songB:0000",
    }


def test_iter_ml_examples_includes_semisupervised(populated_store: EvidenceStore) -> None:
    cfg = MLDatasetConfig(include_semisupervised=True)
    examples = list(iter_ml_examples(populated_store, config=cfg))
    # Now songA:0001 (conf 0.6) joins; songB:0001 still has no consensus.
    assert len(examples) == 3
    hard = [ex for ex in examples if ex.has_hard_label]
    semi = [ex for ex in examples if not ex.has_hard_label]
    assert len(hard) == 2
    assert len(semi) == 1
    assert semi[0].provenance["section_id"] == "songA:0001"
    assert semi[0].label_confidence == pytest.approx(0.6)


def test_iter_ml_examples_skips_sections_without_consensus(
    populated_store: EvidenceStore,
) -> None:
    cfg = MLDatasetConfig(include_semisupervised=True)
    examples = list(iter_ml_examples(populated_store, config=cfg))
    section_ids = {ex.provenance["section_id"] for ex in examples}
    # songB:0001 had jam_output only, no consensus → never emitted.
    assert "songB:0001" not in section_ids


def test_iter_ml_examples_song_id_filter(populated_store: EvidenceStore) -> None:
    cfg = MLDatasetConfig(song_id="songB")
    examples = list(iter_ml_examples(populated_store, config=cfg))
    assert len(examples) == 1
    assert examples[0].provenance["song_id"] == "songB"


def test_iter_ml_examples_date_prefix_filter(populated_store: EvidenceStore) -> None:
    # 2026-06-18 only → just songA records that day.
    cfg = MLDatasetConfig(date_prefix="2026-06-18")
    examples = list(iter_ml_examples(populated_store, config=cfg))
    assert len(examples) == 1
    assert examples[0].provenance["song_id"] == "songA"


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def test_features_are_json_clean(populated_store: EvidenceStore) -> None:
    examples = list(iter_ml_examples(populated_store))
    for ex in examples:
        # round-trip through json — no tuples, no exotic types.
        line = json.dumps(ex.to_dict())
        parsed = json.loads(line)
        assert isinstance(parsed["features"], dict)
        assert isinstance(parsed["labels"], dict)
        assert "label_confidence" in parsed
        assert "has_hard_label" in parsed


def test_features_include_chord_sequence_from_jam_output(
    populated_store: EvidenceStore,
) -> None:
    examples = list(iter_ml_examples(populated_store))
    by_section = {ex.provenance["section_id"]: ex for ex in examples}
    a0 = by_section["songA:0000"]
    assert a0.features["chord_sequence"] == ["C", "G"]
    assert a0.features["guidance_mode"] == "chord"
    assert a0.features["key"] == "C"
    assert a0.features["tempo_bpm"] == 120.0


def test_features_carry_extra_bucket_for_forward_compat(
    populated_store: EvidenceStore,
) -> None:
    """``extra`` is the forward-compat surface — must pass through verbatim."""
    examples = list(iter_ml_examples(populated_store))
    by_section = {ex.provenance["section_id"]: ex for ex in examples}
    assert by_section["songA:0000"].features["extra"] == {"audio_fp": "ab12"}


def test_labels_use_lists_not_tuples(populated_store: EvidenceStore) -> None:
    """chord_sequence in consensus is a tuple; ML view must list-ify it."""
    examples = list(iter_ml_examples(populated_store))
    for ex in examples:
        seq = ex.labels.get("chord_sequence")
        if seq is not None:
            assert isinstance(seq, list)


# ---------------------------------------------------------------------------
# Stats roll-up
# ---------------------------------------------------------------------------


def test_compute_dataset_stats(populated_store: EvidenceStore) -> None:
    stats = compute_dataset_stats(populated_store)
    assert stats.n_records_total == 4
    assert stats.n_supervised_examples == 2
    assert stats.n_semisupervised_examples == 1
    assert stats.n_unique_songs == 2
    # songA:0000, songA:0001, songB:0000 (songB:0001 has no consensus)
    assert stats.n_unique_sections == 3
    # guidance_mode counts span all examples (hard + semi).
    assert stats.guidance_mode_label_counts == {"chord": 1, "riff": 1, "lead": 1}
    # chord_sequence_length histogram from songA:0000 (len 4) + songB:0000 (len 3).
    assert stats.chord_sequence_length_histogram == {4: 1, 3: 1}
    # Mean over (0.95 + 0.6 + 0.92) / 3
    assert stats.mean_label_confidence == pytest.approx((0.95 + 0.6 + 0.92) / 3, rel=1e-6)


def test_stats_to_dict_is_json_clean(populated_store: EvidenceStore) -> None:
    stats = compute_dataset_stats(populated_store)
    line = json.dumps(stats.to_dict())
    parsed = json.loads(line)
    # Histogram keys must be ints in the dict (json represents them
    # as strings inside the JSON text, but the to_dict output is
    # int-keyed Python).
    assert isinstance(stats.to_dict()["chord_sequence_length_histogram"], dict)
    assert "n_records_total" in parsed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(argv: list[str], capsys) -> tuple[int, str, str]:
    rc = ml_main(argv)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_cli_validate_clean(populated_store: EvidenceStore, capsys) -> None:
    rc, out, _ = _run_cli(
        ["--store-root", str(populated_store.root), "validate"],
        capsys,
    )
    assert rc == 0
    assert "OK" in out


def test_cli_validate_json(populated_store: EvidenceStore, capsys) -> None:
    rc, out, _ = _run_cli(
        ["--store-root", str(populated_store.root), "validate", "--json"],
        capsys,
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["n_records"] == 4


def test_cli_stats_json(populated_store: EvidenceStore, capsys) -> None:
    rc, out, _ = _run_cli(
        ["--store-root", str(populated_store.root),
         "stats", "--include-semisupervised", "--json"],
        capsys,
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["n_supervised_examples"] == 2
    assert payload["n_semisupervised_examples"] == 1
    assert payload["n_unique_songs"] == 2


def test_cli_stats_human(populated_store: EvidenceStore, capsys) -> None:
    rc, out, _ = _run_cli(
        ["--store-root", str(populated_store.root), "stats"],
        capsys,
    )
    assert rc == 0
    assert "ML dataset stats" in out
    assert "supervised examples" in out


def test_cli_dump_to_stdout(populated_store: EvidenceStore, capsys) -> None:
    rc, out, _ = _run_cli(
        ["--store-root", str(populated_store.root), "dump"],
        capsys,
    )
    assert rc == 0
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 2  # two hard-label examples
    for ln in lines:
        obj = json.loads(ln)
        assert "features" in obj
        assert "labels" in obj
        assert "label_confidence" in obj


def test_cli_dump_to_file(populated_store: EvidenceStore, tmp_path: Path, capsys) -> None:
    out_path = tmp_path / "examples.jsonl"
    rc, _, err = _run_cli(
        ["--store-root", str(populated_store.root),
         "dump", "--include-semisupervised",
         "--output", str(out_path)],
        capsys,
    )
    assert rc == 0
    assert out_path.exists()
    lines = [ln for ln in out_path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 3
    assert "wrote 3 examples" in err


def test_cli_dump_song_filter(populated_store: EvidenceStore, capsys) -> None:
    rc, out, _ = _run_cli(
        ["--store-root", str(populated_store.root),
         "dump", "--song-id", "songB"],
        capsys,
    )
    assert rc == 0
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["provenance"]["song_id"] == "songB"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def test_dispatcher_routes_ml(populated_store: EvidenceStore, capsys) -> None:
    from bench.__main__ import main as bench_main
    rc = bench_main([
        "ml", "--store-root", str(populated_store.root),
        "validate", "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["n_records"] == 4


def test_dispatcher_lists_ml_in_usage(capsys) -> None:
    from bench.__main__ import main as bench_main
    rc = bench_main(["--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ml" in out
