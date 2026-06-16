"""Tests for ``song_validation.ingestion.tab``.

Tab-source ingestion is the second runtime-side ingestion path (the
first being analysis bundles). These tests pin the validation
contract and the round-trip through the store, so a regression in the
tab payload validator surfaces here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from song_validation import Store
from song_validation.ingestion import TabSourceError, ingest_tab_source


def _minimal_tab(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "song_id": "song-tab-1",
        "source": "songsterr",
        "progression": [
            {"symbol": "C", "startSec": 0.0, "endSec": 2.0},
            {"symbol": "G", "startSec": 2.0, "endSec": 4.0},
        ],
    }
    base.update(overrides)
    return base


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "sv.db")


def test_ingest_roundtrip_minimal_tab(store: Store) -> None:
    """A minimal valid tab: song row created, tab row stored, returned
    tab_id resolves the original progression."""
    payload = _minimal_tab()
    tab_id = ingest_tab_source(payload, store)

    assert isinstance(tab_id, str) and tab_id.startswith("tab_")

    song = store.get_song("song-tab-1")
    assert song is not None
    assert song["song_id"] == "song-tab-1"

    got = store.get_tab_source(tab_id)
    assert got is not None
    assert got["song_id"] == "song-tab-1"
    assert got["source"] == "songsterr"
    assert got["source_confidence"] is None  # default
    assert got["raw_tab"] is None  # default
    assert got["progression"] == payload["progression"]


def test_ingest_tab_persists_optional_fields(store: Store) -> None:
    """source_confidence + raw_tab are stored when supplied."""
    payload = _minimal_tab(
        source_confidence=0.85,
        raw_tab="[Verse]\nC G Am F\n",
        artist="The Foos",
        title="Bar Time",
        duration=210.5,
    )
    tab_id = ingest_tab_source(payload, store)
    got = store.get_tab_source(tab_id)
    assert got is not None
    assert got["source_confidence"] == pytest.approx(0.85)
    assert got["raw_tab"] == "[Verse]\nC G Am F\n"

    song = store.get_song("song-tab-1")
    assert song == {
        "song_id": "song-tab-1",
        "artist": "The Foos",
        "title": "Bar Time",
        "duration": 210.5,
    }


def test_ingest_tab_uses_explicit_tab_id_when_provided(store: Store) -> None:
    payload = _minimal_tab(tab_id="custom-tab-1")
    tid = ingest_tab_source(payload, store)
    assert tid == "custom-tab-1"
    assert store.get_tab_source("custom-tab-1") is not None


def test_ingest_tab_derived_id_is_stable_across_runs(tmp_path: Path) -> None:
    """Two stores, same payload -> same derived tab_id
    (content-addressed)."""
    s1 = Store(db_path=tmp_path / "a.db")
    s2 = Store(db_path=tmp_path / "b.db")
    payload = _minimal_tab()
    tid1 = ingest_tab_source(payload, s1)
    tid2 = ingest_tab_source(payload, s2)
    assert tid1 == tid2


def test_ingest_tab_different_source_yields_different_id(
    tmp_path: Path,
) -> None:
    """Same progression, different source string -> different tab_id.
    The source is part of the fingerprint so identical chord sequences
    from two sites both round-trip without colliding."""
    s = Store(db_path=tmp_path / "sv.db")
    a = ingest_tab_source(_minimal_tab(source="songsterr"), s)
    b = ingest_tab_source(_minimal_tab(source="ultimate_guitar"), s)
    assert a != b


@pytest.mark.parametrize(
    "missing_key", ["song_id", "source", "progression"],
)
def test_ingest_tab_rejects_missing_required_field(
    store: Store, missing_key: str
) -> None:
    payload = _minimal_tab()
    payload.pop(missing_key)
    with pytest.raises(TabSourceError, match="missing required field"):
        ingest_tab_source(payload, store)


def test_ingest_tab_rejects_non_string_song_id(store: Store) -> None:
    with pytest.raises(TabSourceError, match="song_id"):
        ingest_tab_source(_minimal_tab(song_id=42), store)
    with pytest.raises(TabSourceError, match="song_id"):
        ingest_tab_source(_minimal_tab(song_id=""), store)


def test_ingest_tab_rejects_non_string_source(store: Store) -> None:
    with pytest.raises(TabSourceError, match="source"):
        ingest_tab_source(_minimal_tab(source=""), store)
    with pytest.raises(TabSourceError, match="source"):
        ingest_tab_source(_minimal_tab(source=None), store)


def test_ingest_tab_rejects_non_list_progression(store: Store) -> None:
    with pytest.raises(TabSourceError, match="progression must be a list"):
        ingest_tab_source(_minimal_tab(progression="C G Am F"), store)
    with pytest.raises(TabSourceError, match="progression must be a list"):
        ingest_tab_source(_minimal_tab(progression={}), store)


def test_ingest_tab_rejects_out_of_range_source_confidence(
    store: Store,
) -> None:
    with pytest.raises(TabSourceError, match="\\[0, 1\\]"):
        ingest_tab_source(_minimal_tab(source_confidence=1.5), store)
    with pytest.raises(TabSourceError, match="\\[0, 1\\]"):
        ingest_tab_source(_minimal_tab(source_confidence=-0.1), store)


def test_ingest_tab_rejects_non_numeric_source_confidence(
    store: Store,
) -> None:
    with pytest.raises(TabSourceError, match="numeric"):
        ingest_tab_source(_minimal_tab(source_confidence="high"), store)


def test_ingest_tab_rejects_non_string_raw_tab(store: Store) -> None:
    with pytest.raises(TabSourceError, match="raw_tab"):
        ingest_tab_source(_minimal_tab(raw_tab=42), store)


def test_ingest_tab_rejects_non_mapping_payload(store: Store) -> None:
    with pytest.raises(TabSourceError, match="must be a mapping"):
        ingest_tab_source("not a dict", store)  # type: ignore[arg-type]
