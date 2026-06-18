"""Phase 7 — User Correction Capture (domain layer).

Covers ``backend/tone_forge/learning/corrections.py``: payload
validation, evidence-store append shape, and the
``store`` / ``store_root`` argument plumbing.
"""
from __future__ import annotations

import pytest

from bench.evidence.store import EvidenceStore
from bench.learning import (
    CorrectionPayload,
    CorrectionRecordingError,
    SUPPORTED_CORRECTION_TYPES,
    record_correction,
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_missing_song_id_rejected(tmp_path):
    with pytest.raises(CorrectionRecordingError, match="song_id"):
        record_correction(
            CorrectionPayload(
                song_id="",
                section_id="abc:0000",
                correction_type="guidance_mode",
                previous_value="chord",
                corrected_value="riff",
            ),
            store_root=tmp_path,
        )


def test_missing_section_id_rejected(tmp_path):
    with pytest.raises(CorrectionRecordingError, match="section_id"):
        record_correction(
            CorrectionPayload(
                song_id="abc",
                section_id="",
                correction_type="guidance_mode",
                previous_value="chord",
                corrected_value="riff",
            ),
            store_root=tmp_path,
        )


def test_unknown_correction_type_rejected(tmp_path):
    with pytest.raises(CorrectionRecordingError, match="allowlist"):
        record_correction(
            CorrectionPayload(
                song_id="abc",
                section_id="abc:0000",
                correction_type="vibe_meter",   # not in allowlist
                previous_value=0.3,
                corrected_value=0.8,
            ),
            store_root=tmp_path,
        )


def test_both_values_null_rejected(tmp_path):
    with pytest.raises(CorrectionRecordingError, match="non-null"):
        record_correction(
            CorrectionPayload(
                song_id="abc",
                section_id="abc:0000",
                correction_type="chord",
                previous_value=None,
                corrected_value=None,
            ),
            store_root=tmp_path,
        )


def test_corrected_value_only_accepted(tmp_path):
    """previous_value can be None if corrected_value carries info."""
    record = record_correction(
        CorrectionPayload(
            song_id="abc",
            section_id="abc:0000",
            correction_type="chord",
            previous_value=None,
            corrected_value=["C", "G", "Am", "F"],
        ),
        store_root=tmp_path,
    )
    assert record.corrections[0].corrected_value == ["C", "G", "Am", "F"]
    assert record.corrections[0].previous_value is None


def test_previous_value_only_accepted(tmp_path):
    """corrected_value None means "this section has no value for X"."""
    record = record_correction(
        CorrectionPayload(
            song_id="abc",
            section_id="abc:0000",
            correction_type="chord",
            previous_value=["C", "G"],
            corrected_value=None,
        ),
        store_root=tmp_path,
    )
    assert record.corrections[0].previous_value == ["C", "G"]
    assert record.corrections[0].corrected_value is None


# ---------------------------------------------------------------------------
# Allowlist coverage
# ---------------------------------------------------------------------------


def test_supported_types_round_trip(tmp_path):
    store = EvidenceStore(root=tmp_path)
    for ct in SUPPORTED_CORRECTION_TYPES:
        record_correction(
            CorrectionPayload(
                song_id=f"song_{ct}",
                section_id=f"song_{ct}:0000",
                correction_type=ct,
                previous_value="prev",
                corrected_value="next",
            ),
            store=store,
        )
    records = list(store.iter_records())
    types_seen = {r.corrections[0].correction_type for r in records}
    assert types_seen == set(SUPPORTED_CORRECTION_TYPES)


# ---------------------------------------------------------------------------
# Append shape
# ---------------------------------------------------------------------------


def test_record_shape_one_correction_per_record(tmp_path):
    record = record_correction(
        CorrectionPayload(
            song_id="abc",
            section_id="abc:0000",
            correction_type="guidance_mode",
            previous_value="chord",
            corrected_value="riff",
            user_id="user-42",
            note="this is a riff song",
        ),
        store_root=tmp_path,
    )
    # Exactly one correction; no consensus output; no jam output.
    assert len(record.corrections) == 1
    c = record.corrections[0]
    assert c.correction_type == "guidance_mode"
    assert c.previous_value == "chord"
    assert c.corrected_value == "riff"
    assert c.user_id == "user-42"
    assert c.note == "this is a riff song"
    assert record.consensus_output is None
    assert record.jam_output == {}
    assert record.reference_sources == ()


def test_record_is_appended_not_mutating(tmp_path):
    """Two corrections produce two append-only records."""
    store = EvidenceStore(root=tmp_path)
    record_correction(
        CorrectionPayload(
            song_id="abc",
            section_id="abc:0000",
            correction_type="chord",
            previous_value=["A"],
            corrected_value=["Am"],
        ),
        store=store,
    )
    record_correction(
        CorrectionPayload(
            song_id="abc",
            section_id="abc:0000",
            correction_type="chord",
            previous_value=["Am"],
            corrected_value=["Am7"],
        ),
        store=store,
    )
    records = list(store.iter_records())
    assert len(records) == 2
    # Latest-wins read sees the most recent.
    latest = store.latest_for_section("abc", "abc:0000")
    assert latest is not None
    assert latest.corrections[0].corrected_value == ["Am7"]


def test_timestamp_stamped_when_omitted(tmp_path):
    record = record_correction(
        CorrectionPayload(
            song_id="abc",
            section_id="abc:0000",
            correction_type="key",
            previous_value="C major",
            corrected_value="A minor",
        ),
        store_root=tmp_path,
    )
    assert record.timestamp_utc.endswith("Z")
    assert "T" in record.timestamp_utc


def test_timestamp_passthrough(tmp_path):
    fixed = "2026-06-18T08:30:00.000000Z"
    record = record_correction(
        CorrectionPayload(
            song_id="abc",
            section_id="abc:0000",
            correction_type="tempo_bpm",
            previous_value=120.0,
            corrected_value=128.0,
            timestamp_utc=fixed,
        ),
        store_root=tmp_path,
    )
    assert record.timestamp_utc == fixed


# ---------------------------------------------------------------------------
# Store plumbing
# ---------------------------------------------------------------------------


def test_store_takes_precedence_over_store_root(tmp_path):
    primary = EvidenceStore(root=tmp_path / "primary")
    decoy = tmp_path / "decoy"
    record_correction(
        CorrectionPayload(
            song_id="abc",
            section_id="abc:0000",
            correction_type="chord",
            previous_value="C",
            corrected_value="G",
        ),
        store=primary,
        store_root=decoy,
    )
    # Record landed in primary, not decoy.
    assert primary.count() == 1
    assert not decoy.exists() or not any(decoy.glob("*.jsonl"))


def test_store_root_creates_directory(tmp_path):
    target = tmp_path / "fresh"
    assert not target.exists()
    record_correction(
        CorrectionPayload(
            song_id="abc",
            section_id="abc:0000",
            correction_type="section_boundary",
            previous_value=12.3,
            corrected_value=14.0,
        ),
        store_root=target,
    )
    assert target.exists()
    assert EvidenceStore(root=target).count() == 1
