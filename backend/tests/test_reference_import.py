"""Phase 2 Reference Import Pipeline tests.

Covers:

    * ``RawReferenceFile`` JSON round-trip
    * ``load_reference_file`` validation (missing required field,
      non-list sections, root-not-object)
    * ``reference_file_to_records`` mapping to ``EvidenceRecord``
    * ``ingest_reference_file`` against an ``EvidenceStore`` tmp root
    * CLI subcommands ``ingest`` / ``list`` / ``template``

All tests are hermetic — they never touch the real
``backend/data/`` directories.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.evidence import EvidenceStore
from bench.reference import (
    RawReferenceFile,
    RawReferenceSection,
    dump_reference_file,
    ingest_reference_file,
    load_reference_file,
    reference_file_to_records,
)
from bench.reference.__main__ import main as ref_main


# ---------------------------------------------------------------------------
# Test fixtures (in-code).
# ---------------------------------------------------------------------------


def _make_ref(song_id: str = "abc1234567890def", n_sections: int = 2) -> RawReferenceFile:
    sections = tuple(
        RawReferenceSection(
            section_id=f"{song_id}:{i:04d}",
            labels={
                "guidance_mode": "chord" if i % 2 == 0 else "riff",
                "chord_sequence": ["Am", "G"] if i % 2 == 0 else [],
            },
        )
        for i in range(n_sections)
    )
    return RawReferenceFile(
        song_id=song_id,
        source="songsterr",
        version="rev-2026-06-15",
        fetched_at_utc="2026-06-18T00:00:00.000000Z",
        sections=sections,
        source_url="https://www.songsterr.com/a/wsa/example",
    )


# ---------------------------------------------------------------------------
# Schema round-trip + validation
# ---------------------------------------------------------------------------


def test_reference_file_round_trip(tmp_path: Path) -> None:
    ref = _make_ref()
    path = tmp_path / "songsterr_abc.json"
    dump_reference_file(ref, path)
    loaded = load_reference_file(path)
    assert loaded.song_id == ref.song_id
    assert loaded.source == ref.source
    assert loaded.version == ref.version
    assert loaded.source_url == ref.source_url
    assert len(loaded.sections) == 2
    assert loaded.sections[0].section_id == "abc1234567890def:0000"
    assert loaded.sections[0].labels["guidance_mode"] == "chord"
    assert loaded.sections[1].labels["guidance_mode"] == "riff"


def test_load_rejects_missing_required_field(tmp_path: Path) -> None:
    bad = {"song_id": "x", "source": "y"}  # missing version + fetched_at_utc
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="missing required field"):
        load_reference_file(path)


def test_load_rejects_non_list_sections(tmp_path: Path) -> None:
    bad = {
        "song_id": "x", "source": "y", "version": "v1",
        "fetched_at_utc": "2026-01-01T00:00:00Z",
        "sections": {"oops": "should be list"},
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="'sections' must be a list"):
        load_reference_file(path)


def test_load_rejects_non_object_root(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ValueError, match="must be an object"):
        load_reference_file(path)


def test_dump_preserves_unicode(tmp_path: Path) -> None:
    ref = RawReferenceFile(
        song_id="x" * 16,
        source="manual",
        version="v1",
        fetched_at_utc="2026-01-01T00:00:00.000000Z",
        sections=(RawReferenceSection(
            section_id="x" * 16 + ":0000",
            labels={"note": "café — naïve"},
        ),),
    )
    path = tmp_path / "unicode.json"
    dump_reference_file(ref, path)
    loaded = load_reference_file(path)
    assert loaded.sections[0].labels["note"] == "café — naïve"


# ---------------------------------------------------------------------------
# reference_file_to_records mapping
# ---------------------------------------------------------------------------


def test_to_records_one_per_section() -> None:
    ref = _make_ref(n_sections=3)
    records = reference_file_to_records(ref)
    assert len(records) == 3
    for i, rec in enumerate(records):
        assert rec.song_id == ref.song_id
        assert rec.section_id == f"{ref.song_id}:{i:04d}"
        # jam_output is empty on reference-only records
        assert rec.jam_output == {}
        # exactly one ReferenceSource per record
        assert len(rec.reference_sources) == 1
        rs = rec.reference_sources[0]
        assert rs.source == "songsterr"
        assert rs.version == ref.version
        assert rs.source_url == ref.source_url
        # consensus / corrections empty (Phase 3 / 7 territory)
        assert rec.consensus_output is None
        assert rec.corrections == ()


def test_to_records_shares_timestamp_across_batch() -> None:
    ref = _make_ref(n_sections=5)
    ts = "2030-01-01T00:00:00.000000Z"
    records = reference_file_to_records(ref, timestamp_utc=ts)
    assert all(r.timestamp_utc == ts for r in records)


def test_to_records_no_sections_is_empty() -> None:
    ref = RawReferenceFile(
        song_id="z" * 16, source="manual", version="v1",
        fetched_at_utc="2026-01-01T00:00:00Z",
        sections=(),
    )
    assert reference_file_to_records(ref) == []


# ---------------------------------------------------------------------------
# Store integration
# ---------------------------------------------------------------------------


def test_ingest_appends_to_store(tmp_path: Path) -> None:
    store = EvidenceStore(root=tmp_path / "ev")
    ref = _make_ref(n_sections=2)
    n = ingest_reference_file(ref, store, timestamp_utc="2026-06-18T00:00:00.000000Z")
    assert n == 2
    all_records = list(store.iter_records())
    assert len(all_records) == 2
    # Both records carry the reference source.
    for rec in all_records:
        assert len(rec.reference_sources) == 1
        assert rec.reference_sources[0].source == "songsterr"


def test_ingest_then_evidence_replay_coexist(tmp_path: Path) -> None:
    """Phase 1 jam records + Phase 2 reference records share keys."""
    from bench.evidence import EvidenceRecord
    store = EvidenceStore(root=tmp_path / "ev")
    # Phase 1 style: jam_output populated, no references.
    store.append(EvidenceRecord(
        song_id="abc1234567890def",
        section_id="abc1234567890def:0000",
        timestamp_utc="2026-06-17T00:00:00.000000Z",
        jam_output={"guidance_mode": "chord"},
    ))
    # Phase 2 style: reference populated, no jam_output.
    ref = _make_ref(n_sections=1)
    ingest_reference_file(ref, store, timestamp_utc="2026-06-18T00:00:00.000000Z")

    # Both records reachable for the same (song_id, section_id).
    matching = [
        r for r in store.iter_records()
        if r.song_id == "abc1234567890def" and r.section_id == "abc1234567890def:0000"
    ]
    assert len(matching) == 2
    # latest_for_section picks the newer reference record.
    latest = store.latest_for_section("abc1234567890def", "abc1234567890def:0000")
    assert latest is not None
    assert latest.timestamp_utc == "2026-06-18T00:00:00.000000Z"
    assert len(latest.reference_sources) == 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_ingest_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    ref_path = tmp_path / "songsterr.json"
    dump_reference_file(_make_ref(n_sections=2), ref_path)
    store_root = tmp_path / "ev"
    rc = ref_main([
        "--store-root", str(store_root),
        "ingest", str(ref_path), "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "would append 2 records" in out
    # Nothing actually written.
    assert not store_root.exists() or not list(store_root.glob("*.jsonl"))


def test_cli_ingest_writes(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    ref_path = tmp_path / "songsterr.json"
    dump_reference_file(_make_ref(n_sections=2), ref_path)
    store_root = tmp_path / "ev"
    rc = ref_main([
        "--store-root", str(store_root),
        "ingest", str(ref_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "appended 2 records" in out
    store = EvidenceStore(root=store_root)
    assert store.count() == 2


def test_cli_ingest_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = ref_main([
        "--store-root", str(tmp_path / "ev"),
        "ingest", str(tmp_path / "nope.json"),
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err


def test_cli_list_empty_dir(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    refs_root = tmp_path / "refs"
    refs_root.mkdir()
    rc = ref_main(["list", "--references-root", str(refs_root)])
    assert rc == 0
    assert "no reference files" in capsys.readouterr().out


def test_cli_list_with_files(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    refs_root = tmp_path / "refs"
    refs_root.mkdir()
    dump_reference_file(_make_ref(n_sections=3), refs_root / "songsterr_abc.json")
    rc = ref_main(["list", "--references-root", str(refs_root)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "songsterr_abc.json" in out
    assert "sections=3" in out


def test_cli_list_json_mode(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    refs_root = tmp_path / "refs"
    refs_root.mkdir()
    dump_reference_file(_make_ref(n_sections=2), refs_root / "x.json")
    rc = ref_main(["list", "--references-root", str(refs_root), "--json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert rows[0]["n_sections"] == 2


def test_cli_template_produces_valid_stub(capsys: pytest.CaptureFixture) -> None:
    rc = ref_main([
        "template",
        "--song-id", "abc1234567890def",
        "--source", "manual",
        "--n-sections", "3",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["song_id"] == "abc1234567890def"
    assert payload["source"] == "manual"
    assert len(payload["sections"]) == 3
    assert payload["sections"][0]["section_id"] == "abc1234567890def:0000"


def test_cli_template_rejects_unknown_source(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit):
        ref_main([
            "template",
            "--song-id", "abc1234567890def",
            "--source", "spotify",  # not in choices
        ])
