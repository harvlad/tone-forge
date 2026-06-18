"""Phase 8 — Disagreement-Driven Roadmap report.

Covers ``bench/roadmap/ranker.py``: failure + correction
aggregation, area mapping, score formula, ranking, JSON
round-trip, and the CLI dispatcher entry point.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bench.evidence.schema import (
    ConsensusOutput,
    Correction,
    EvidenceRecord,
)
from bench.evidence.store import EvidenceStore
from bench.roadmap import (
    RoadmapConfig,
    RoadmapItem,
    RoadmapReport,
    build_roadmap,
    dump_roadmap,
    load_roadmap,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _consensus(
    *,
    guidance_mode: str | None = "chord",
    chord_sequence: tuple[str, ...] | None = ("C", "G", "Am", "F"),
    confidence: float = 0.9,
) -> ConsensusOutput:
    return ConsensusOutput(
        guidance_mode=guidance_mode,
        chord_sequence=chord_sequence,
        confidence=confidence,
        agreement={"guidance_mode": 1.0, "chord_sequence": 1.0},
    )


def _append_jam_vs_consensus(
    store: EvidenceStore,
    *,
    song_id: str,
    section_idx: int,
    jam_guidance: str,
    jam_chord_seq: list[str],
    cons_guidance: str,
    cons_chord_seq: tuple[str, ...],
    confidence: float = 0.9,
    timestamp_utc: str = "2026-06-18T10:00:00.000000Z",
) -> str:
    """Append two records (one jam, one consensus) for one section.

    Returns the ``section_id``. The two records share the
    ``(song_id, section_id)`` key but have different
    ``timestamp_utc`` so the latest-wins selectors in the failure
    miner pick the freshest of each kind.
    """
    section_id = f"{song_id}:{section_idx:04d}"
    store.append(EvidenceRecord(
        song_id=song_id,
        section_id=section_id,
        timestamp_utc=timestamp_utc,
        jam_output={
            "guidance_mode": jam_guidance,
            "chords_in_section": [{"symbol": s} for s in jam_chord_seq],
        },
    ))
    store.append(EvidenceRecord(
        song_id=song_id,
        section_id=section_id,
        timestamp_utc="2026-06-18T11:00:00.000000Z",
        consensus_output=_consensus(
            guidance_mode=cons_guidance,
            chord_sequence=cons_chord_seq,
            confidence=confidence,
        ),
    ))
    return section_id


def _append_correction(
    store: EvidenceStore,
    *,
    song_id: str,
    section_idx: int,
    correction_type: str,
    previous_value=None,
    corrected_value=None,
    timestamp_utc: str = "2026-06-18T12:00:00.000000Z",
) -> str:
    section_id = f"{song_id}:{section_idx:04d}"
    store.append(EvidenceRecord(
        song_id=song_id,
        section_id=section_id,
        timestamp_utc=timestamp_utc,
        corrections=(Correction(
            correction_type=correction_type,
            previous_value=previous_value,
            corrected_value=corrected_value,
        ),),
    ))
    return section_id


# ---------------------------------------------------------------------------
# Empty store
# ---------------------------------------------------------------------------


def test_empty_store_returns_empty_report(tmp_path):
    store = EvidenceStore(root=tmp_path)
    report = build_roadmap(store)
    assert report.n_areas_total == 0
    assert report.n_consensus_failures_total == 0
    assert report.n_user_corrections_total == 0
    assert report.items == ()


# ---------------------------------------------------------------------------
# Area mapping + signal fusion
# ---------------------------------------------------------------------------


def test_consensus_failure_creates_area_item(tmp_path):
    store = EvidenceStore(root=tmp_path)
    _append_jam_vs_consensus(
        store,
        song_id="songA",
        section_idx=0,
        jam_guidance="chord",
        jam_chord_seq=["C", "G", "Am", "F"],
        cons_guidance="riff",                       # mismatch
        cons_chord_seq=("C", "G", "Am", "F"),
        confidence=0.9,
    )
    report = build_roadmap(store)
    assert report.n_consensus_failures_total == 1
    assert report.n_user_corrections_total == 0
    assert len(report.items) == 1
    item = report.items[0]
    assert item.area == "guidance_mode_classifier"
    assert item.n_consensus_failures == 1
    assert item.n_user_corrections == 0
    assert "guidance_mode_mismatch" in item.failure_types
    assert item.score == pytest.approx(0.9)


def test_user_correction_creates_area_item(tmp_path):
    store = EvidenceStore(root=tmp_path)
    _append_correction(
        store,
        song_id="songA",
        section_idx=0,
        correction_type="chord",
        previous_value="C",
        corrected_value="Cmaj7",
    )
    report = build_roadmap(store)
    assert report.n_user_corrections_total == 1
    assert len(report.items) == 1
    item = report.items[0]
    assert item.area == "chord_detector"
    assert item.n_user_corrections == 1
    assert item.n_consensus_failures == 0
    assert "chord" in item.correction_types
    assert item.score == pytest.approx(1.0)


def test_correction_and_failure_fuse_into_same_area(tmp_path):
    store = EvidenceStore(root=tmp_path)
    _append_jam_vs_consensus(
        store,
        song_id="songA",
        section_idx=0,
        jam_guidance="chord",
        jam_chord_seq=["C", "G", "Am", "F"],
        cons_guidance="chord",
        cons_chord_seq=("C", "G", "F", "F"),         # chord_seq mismatch
        confidence=0.85,
    )
    _append_correction(
        store,
        song_id="songA",
        section_idx=1,
        correction_type="chord_sequence",
        corrected_value=["D", "A", "Bm", "G"],
    )
    report = build_roadmap(store)
    assert len(report.items) == 1
    item = report.items[0]
    assert item.area == "chord_detector"
    assert item.n_consensus_failures == 1
    assert item.n_user_corrections == 1
    # score = 1.0 * 0.85 + 1.0 * 1 == 1.85
    assert item.score == pytest.approx(1.85)


def test_unknown_correction_type_excluded_from_areas(tmp_path):
    """Allowlist drift: an unknown correction_type lands in
    evidence but doesn't map to a roadmap area."""
    store = EvidenceStore(root=tmp_path)
    store.append(EvidenceRecord(
        song_id="songA",
        section_id="songA:0000",
        timestamp_utc="2026-06-18T10:00:00.000000Z",
        corrections=(Correction(
            correction_type="vibe_meter",
            previous_value=0.3,
            corrected_value=0.8,
        ),),
    ))
    report = build_roadmap(store)
    # Counted in the raw total (it's still evidence) but not mapped
    # to any area, so no item is emitted.
    assert report.n_user_corrections_total == 1
    assert report.items == ()


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_items_sorted_by_score_desc(tmp_path):
    store = EvidenceStore(root=tmp_path)
    # chord_detector: 1 failure (conf 0.85) + 1 correction = 1.85
    _append_jam_vs_consensus(
        store, song_id="songA", section_idx=0,
        jam_guidance="chord", jam_chord_seq=["C"],
        cons_guidance="chord", cons_chord_seq=("D",),
        confidence=0.85,
    )
    _append_correction(
        store, song_id="songA", section_idx=1,
        correction_type="chord", previous_value="C", corrected_value="D",
    )
    # guidance_mode_classifier: 2 failures (conf 0.9 each) = 1.8
    _append_jam_vs_consensus(
        store, song_id="songB", section_idx=0,
        jam_guidance="chord", jam_chord_seq=["C"],
        cons_guidance="riff", cons_chord_seq=("C",),
        confidence=0.9,
    )
    _append_jam_vs_consensus(
        store, song_id="songC", section_idx=0,
        jam_guidance="chord", jam_chord_seq=["C"],
        cons_guidance="lead", cons_chord_seq=("C",),
        confidence=0.9,
    )
    # key_detector: 1 correction = 1.0
    _append_correction(
        store, song_id="songA", section_idx=2,
        correction_type="key", corrected_value="A minor",
    )
    report = build_roadmap(store)
    areas = [it.area for it in report.items]
    assert areas == [
        "chord_detector",            # 1.85
        "guidance_mode_classifier",  # 1.80
        "key_detector",              # 1.00
    ]


def test_top_n_caps_item_count(tmp_path):
    store = EvidenceStore(root=tmp_path)
    _append_correction(
        store, song_id="songA", section_idx=0,
        correction_type="chord", corrected_value="D",
    )
    _append_correction(
        store, song_id="songA", section_idx=1,
        correction_type="guidance_mode",
        previous_value="chord", corrected_value="riff",
    )
    _append_correction(
        store, song_id="songA", section_idx=2,
        correction_type="key", corrected_value="A minor",
    )
    report = build_roadmap(store, config=RoadmapConfig(top_n=2))
    assert len(report.items) == 2
    assert report.n_areas_total == 3  # totals still reflect everything


def test_weights_change_ranking(tmp_path):
    store = EvidenceStore(root=tmp_path)
    # A: 1 high-conf failure (conf 0.95)
    _append_jam_vs_consensus(
        store, song_id="songA", section_idx=0,
        jam_guidance="chord", jam_chord_seq=["C"],
        cons_guidance="riff", cons_chord_seq=("C",),
        confidence=0.95,
    )
    # B: 1 correction
    _append_correction(
        store, song_id="songA", section_idx=1,
        correction_type="chord", corrected_value="D",
    )

    # Default weights (1.0 / 1.0): correction (1.0) > failure (0.95)
    # → chord_detector ranks first.
    report_default = build_roadmap(store)
    assert report_default.items[0].area == "chord_detector"

    # Boost consensus weight → failure overwhelms the correction.
    report_boost = build_roadmap(
        store, config=RoadmapConfig(consensus_weight=10.0),
    )
    assert report_boost.items[0].area == "guidance_mode_classifier"


# ---------------------------------------------------------------------------
# Confidence gate
# ---------------------------------------------------------------------------


def test_low_confidence_consensus_skipped(tmp_path):
    store = EvidenceStore(root=tmp_path)
    _append_jam_vs_consensus(
        store, song_id="songA", section_idx=0,
        jam_guidance="chord", jam_chord_seq=["C"],
        cons_guidance="riff", cons_chord_seq=("C",),
        confidence=0.5,    # below default 0.8 floor
    )
    report = build_roadmap(store)
    assert report.n_consensus_failures_total == 0
    assert report.items == ()


def test_low_confidence_admitted_when_floor_lowered(tmp_path):
    store = EvidenceStore(root=tmp_path)
    _append_jam_vs_consensus(
        store, song_id="songA", section_idx=0,
        jam_guidance="chord", jam_chord_seq=["C"],
        cons_guidance="riff", cons_chord_seq=("C",),
        confidence=0.5,
    )
    report = build_roadmap(
        store, config=RoadmapConfig(min_consensus_confidence=0.4),
    )
    assert report.n_consensus_failures_total == 1
    assert len(report.items) == 1


# ---------------------------------------------------------------------------
# Song filter
# ---------------------------------------------------------------------------


def test_song_filter_scopes_report(tmp_path):
    store = EvidenceStore(root=tmp_path)
    _append_jam_vs_consensus(
        store, song_id="songA", section_idx=0,
        jam_guidance="chord", jam_chord_seq=["C"],
        cons_guidance="riff", cons_chord_seq=("C",),
        confidence=0.9,
    )
    _append_correction(
        store, song_id="songB", section_idx=0,
        correction_type="chord", corrected_value="D",
    )
    full = build_roadmap(store)
    assert full.n_consensus_failures_total == 1
    assert full.n_user_corrections_total == 1
    assert len(full.items) == 2

    a_only = build_roadmap(store, config=RoadmapConfig(song_id="songA"))
    assert a_only.n_consensus_failures_total == 1
    assert a_only.n_user_corrections_total == 0
    assert [it.area for it in a_only.items] == ["guidance_mode_classifier"]

    b_only = build_roadmap(store, config=RoadmapConfig(song_id="songB"))
    assert b_only.n_consensus_failures_total == 0
    assert b_only.n_user_corrections_total == 1
    assert [it.area for it in b_only.items] == ["chord_detector"]


# ---------------------------------------------------------------------------
# Per-item detail capture
# ---------------------------------------------------------------------------


def test_examples_per_item_caps_breadcrumbs(tmp_path):
    store = EvidenceStore(root=tmp_path)
    for i in range(7):
        _append_correction(
            store, song_id="songA", section_idx=i,
            correction_type="chord",
            corrected_value=f"C{i}",
            timestamp_utc=f"2026-06-18T10:00:0{i}.000000Z",
        )
    report = build_roadmap(
        store, config=RoadmapConfig(examples_per_item=3),
    )
    assert len(report.items) == 1
    assert len(report.items[0].example_sections) == 3
    for song_id, section_id, kind in report.items[0].example_sections:
        assert song_id == "songA"
        assert section_id.startswith("songA:")
        assert kind == "correction"


def test_representative_diffs_one_per_failure_type(tmp_path):
    store = EvidenceStore(root=tmp_path)
    # Two guidance_mode failures + one chord_sequence failure.
    _append_jam_vs_consensus(
        store, song_id="songA", section_idx=0,
        jam_guidance="chord", jam_chord_seq=["C"],
        cons_guidance="riff", cons_chord_seq=("C",),
        confidence=0.9,
    )
    _append_jam_vs_consensus(
        store, song_id="songB", section_idx=0,
        jam_guidance="chord", jam_chord_seq=["C"],
        cons_guidance="lead", cons_chord_seq=("C",),
        confidence=0.9,
    )
    _append_jam_vs_consensus(
        store, song_id="songC", section_idx=0,
        jam_guidance="chord", jam_chord_seq=["C"],
        cons_guidance="chord", cons_chord_seq=("D",),
        confidence=0.9,
    )
    report = build_roadmap(store)
    by_area = {it.area: it for it in report.items}
    gmc = by_area["guidance_mode_classifier"]
    # Two failures of the same type — only one representative diff.
    assert len(gmc.representative_diffs) == 1
    assert gmc.representative_diffs[0]["failure_type"] == (
        "guidance_mode_mismatch"
    )


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


def test_dump_then_load_roundtrip(tmp_path):
    store = EvidenceStore(root=tmp_path)
    _append_jam_vs_consensus(
        store, song_id="songA", section_idx=0,
        jam_guidance="chord", jam_chord_seq=["C", "G"],
        cons_guidance="riff", cons_chord_seq=("C", "G"),
        confidence=0.9,
    )
    _append_correction(
        store, song_id="songA", section_idx=1,
        correction_type="chord", corrected_value="Am",
    )

    original = build_roadmap(store)
    data = dump_roadmap(original)
    # Must be JSON-serialisable.
    text = json.dumps(data)
    reloaded = load_roadmap(json.loads(text))

    assert reloaded.n_areas_total == original.n_areas_total
    assert reloaded.n_consensus_failures_total == (
        original.n_consensus_failures_total
    )
    assert reloaded.n_user_corrections_total == (
        original.n_user_corrections_total
    )
    assert len(reloaded.items) == len(original.items)
    for a, b in zip(reloaded.items, original.items):
        assert a.area == b.area
        assert a.score == pytest.approx(b.score)
        assert a.n_consensus_failures == b.n_consensus_failures
        assert a.n_user_corrections == b.n_user_corrections
        assert set(a.failure_types) == set(b.failure_types)
        assert set(a.correction_types) == set(b.correction_types)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    backend = Path(__file__).resolve().parent.parent
    return subprocess.run(
        [sys.executable, "-m", "bench.roadmap", *args],
        capture_output=True, text=True,
        cwd=cwd if cwd is not None else backend,
    )


def _run_dispatcher(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    backend = Path(__file__).resolve().parent.parent
    return subprocess.run(
        [sys.executable, "-m", "bench", *args],
        capture_output=True, text=True,
        cwd=cwd if cwd is not None else backend,
    )


def test_cli_build_text_output(tmp_path):
    store = EvidenceStore(root=tmp_path)
    _append_correction(
        store, song_id="songA", section_idx=0,
        correction_type="chord", corrected_value="D",
    )
    proc = _run_cli(
        "--store-root", str(tmp_path),
        "build",
    )
    assert proc.returncode == 0, proc.stderr
    assert "Disagreement-Driven Roadmap" in proc.stdout
    assert "chord_detector" in proc.stdout


def test_cli_build_writes_json_output(tmp_path):
    store = EvidenceStore(root=tmp_path)
    _append_correction(
        store, song_id="songA", section_idx=0,
        correction_type="chord", corrected_value="D",
    )
    out_path = tmp_path / "roadmap.json"
    proc = _run_cli(
        "--store-root", str(tmp_path),
        "build", "--output", str(out_path),
    )
    assert proc.returncode == 0, proc.stderr
    assert out_path.exists()
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["n_areas_total"] == 1
    assert data["items"][0]["area"] == "chord_detector"


def test_cli_show_round_trips(tmp_path):
    store = EvidenceStore(root=tmp_path)
    _append_correction(
        store, song_id="songA", section_idx=0,
        correction_type="guidance_mode",
        previous_value="chord", corrected_value="riff",
    )
    out_path = tmp_path / "roadmap.json"
    build = _run_cli(
        "--store-root", str(tmp_path),
        "build", "--output", str(out_path),
    )
    assert build.returncode == 0, build.stderr

    show = _run_cli("show", str(out_path))
    assert show.returncode == 0, show.stderr
    assert "guidance_mode_classifier" in show.stdout


def test_dispatcher_routes_roadmap(tmp_path):
    """`python -m bench roadmap` should reach our CLI."""
    store = EvidenceStore(root=tmp_path)
    _append_correction(
        store, song_id="songA", section_idx=0,
        correction_type="key", corrected_value="C major",
    )
    proc = _run_dispatcher(
        "roadmap",
        "--store-root", str(tmp_path),
        "build",
    )
    assert proc.returncode == 0, proc.stderr
    assert "key_detector" in proc.stdout
