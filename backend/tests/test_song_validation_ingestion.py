"""Tests for ``song_validation.ingestion.bundle``.

The ingestion entry point is the only thing the runtime side will
need to call (via a worker queue, in a future commit). These tests
pin the bundle validation contract and the round-trip through the
store, so a regression in the validator surfaces here rather than
in production.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from song_validation import Store
from song_validation.ingestion import AnalysisBundleError, ingest_analysis_bundle


def _minimal_bundle(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "song_id": "song-123",
        "chords": [
            {"symbol": "C", "startSec": 0.0, "endSec": 2.0},
            {"symbol": "Am", "startSec": 2.0, "endSec": 4.0},
        ],
        "sections": [{"label": "Verse", "startSec": 0.0, "endSec": 4.0}],
        "key": "C major",
        "tempo": 120.0,
    }
    base.update(overrides)
    return base


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "sv.db")


def test_ingest_roundtrip_minimal_bundle(store: Store) -> None:
    """A minimal valid bundle: song row created, analysis row stored,
    returned analysis_id resolves the original chords + sections."""
    bundle = _minimal_bundle()
    analysis_id = ingest_analysis_bundle(bundle, store)

    assert isinstance(analysis_id, str) and analysis_id.startswith("an_")

    song = store.get_song("song-123")
    assert song is not None
    assert song["song_id"] == "song-123"

    got = store.get_analysis_result(analysis_id)
    assert got is not None
    assert got["song_id"] == "song-123"
    assert got["engine_version"] == "unknown"  # default
    assert got["chords"] == bundle["chords"]
    assert got["sections"] == bundle["sections"]
    assert got["tempo"] == 120.0
    assert got["key"] == "C major"


def test_ingest_fills_song_metadata_when_provided(store: Store) -> None:
    """artist/title/duration on the bundle propagate to the songs row."""
    bundle = _minimal_bundle(
        artist="The Foos",
        title="Bar Time",
        duration=210.5,
        engine_version="v2.0",
    )
    ingest_analysis_bundle(bundle, store)
    song = store.get_song("song-123")
    assert song == {
        "song_id": "song-123",
        "artist": "The Foos",
        "title": "Bar Time",
        "duration": 210.5,
    }


def test_ingest_uses_explicit_analysis_id_when_provided(store: Store) -> None:
    bundle = _minimal_bundle(analysis_id="custom-aid-1")
    aid = ingest_analysis_bundle(bundle, store)
    assert aid == "custom-aid-1"
    assert store.get_analysis_result("custom-aid-1") is not None


def test_ingest_derived_id_is_stable_across_runs(tmp_path: Path) -> None:
    """Two stores, same bundle → same derived analysis_id (content-
    addressed). Re-running ingestion should be a deterministic
    fingerprint of the payload."""
    s1 = Store(db_path=tmp_path / "a.db")
    s2 = Store(db_path=tmp_path / "b.db")
    bundle = _minimal_bundle()
    aid1 = ingest_analysis_bundle(bundle, s1)
    aid2 = ingest_analysis_bundle(bundle, s2)
    assert aid1 == aid2


@pytest.mark.parametrize(
    "missing_key",
    ["song_id", "chords", "sections", "key", "tempo"],
)
def test_ingest_rejects_missing_required_field(
    store: Store, missing_key: str
) -> None:
    bundle = _minimal_bundle()
    bundle.pop(missing_key)
    with pytest.raises(AnalysisBundleError, match="missing required field"):
        ingest_analysis_bundle(bundle, store)


def test_ingest_rejects_non_string_song_id(store: Store) -> None:
    with pytest.raises(AnalysisBundleError, match="song_id"):
        ingest_analysis_bundle(_minimal_bundle(song_id=42), store)
    with pytest.raises(AnalysisBundleError, match="song_id"):
        ingest_analysis_bundle(_minimal_bundle(song_id=""), store)


def test_ingest_rejects_non_list_chords_or_sections(store: Store) -> None:
    with pytest.raises(AnalysisBundleError, match="chords must be a list"):
        ingest_analysis_bundle(_minimal_bundle(chords="not-a-list"), store)
    with pytest.raises(AnalysisBundleError, match="sections must be a list"):
        ingest_analysis_bundle(_minimal_bundle(sections={}), store)


def test_ingest_rejects_non_numeric_tempo(store: Store) -> None:
    with pytest.raises(AnalysisBundleError, match="tempo must be numeric"):
        ingest_analysis_bundle(_minimal_bundle(tempo="fast"), store)


def test_ingest_rejects_non_mapping_payload(store: Store) -> None:
    with pytest.raises(AnalysisBundleError, match="must be a mapping"):
        ingest_analysis_bundle("not a dict", store)  # type: ignore[arg-type]
