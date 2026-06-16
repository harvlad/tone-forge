"""Tests for ``song_validation.training.corpus``.

Pin the high-confidence subset query so the future harmony-LM
training set has a stable, auditable definition.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from song_validation import Store, validate_song
from song_validation.ingestion import (
    ingest_analysis_bundle,
    ingest_tab_source,
)
from song_validation.training import (
    corpus_stats,
    iter_high_confidence_progressions,
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "sv.db")


def _bundle(
    song_id: str,
    chords: List[Dict[str, Any]],
    engine_version: str = "v1.0",
) -> Dict[str, Any]:
    return {
        "song_id": song_id,
        "chords": chords,
        "sections": [{"label": "Verse", "startSec": 0.0, "endSec": 2.0}],
        "key": "C major",
        "tempo": 120.0,
        "engine_version": engine_version,
    }


def _tab(
    song_id: str,
    progression: List[Dict[str, Any]],
    *,
    confidence: float = 0.9,
) -> Dict[str, Any]:
    return {
        "song_id": song_id,
        "source": "songsterr",
        "source_confidence": confidence,
        "progression": progression,
    }


def _seed_perfect_song(
    store: Store, song_id: str, engine_version: str = "v1.0"
) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle(song_id, chords, engine_version), store)
    ingest_tab_source(_tab(song_id, chords), store)
    validate_song(song_id, store)


def test_corpus_empty_when_no_alignments(store: Store) -> None:
    stats = corpus_stats(store)
    assert stats["matching_songs"] == 0
    assert stats["matching_analyses"] == 0
    assert stats["total_chords"] == 0
    assert stats["alignment_score_min"] is None
    assert list(iter_high_confidence_progressions(store)) == []


def test_corpus_yields_perfect_alignment(store: Store) -> None:
    _seed_perfect_song(store, "s1")
    progs = list(iter_high_confidence_progressions(store))
    assert len(progs) == 1
    p = progs[0]
    assert p["song_id"] == "s1"
    assert p["engine_version"] == "v1.0"
    assert p["key"] == "C major"
    assert p["tempo"] == 120.0
    assert p["sections"] == [
        {"label": "Verse", "startSec": 0.0, "endSec": 2.0}
    ]
    assert p["chords"] == [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    assert p["best_alignment_score"] == pytest.approx(1.0)


def test_corpus_excludes_low_alignment_score(store: Store) -> None:
    """jam=C vs tab=G across the whole song -> score 0.0, gated out."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": "G", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", jam), store)
    ingest_tab_source(_tab("s1", tab), store)
    validate_song("s1", store)

    assert list(iter_high_confidence_progressions(store)) == []
    assert corpus_stats(store)["matching_songs"] == 0


def test_corpus_excludes_low_tab_confidence(store: Store) -> None:
    """Perfect engine + tab agreement but tab source_confidence < gate."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", chords), store)
    ingest_tab_source(_tab("s1", chords, confidence=0.4), store)
    validate_song("s1", store)

    progs = list(iter_high_confidence_progressions(store))
    assert progs == []  # tab conf 0.4 < default gate 0.7

    # Lowering the gate lets it through.
    progs2 = list(
        iter_high_confidence_progressions(store, min_tab_confidence=0.3)
    )
    assert len(progs2) == 1


def test_corpus_treats_null_tab_confidence_as_pass(store: Store) -> None:
    """A tab without source_confidence is NOT excluded — null is treated
    as 'unknown -> keep'. The classifier handles that case separately."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", chords), store)
    # Tab without confidence.
    ingest_tab_source(
        {
            "song_id": "s1",
            "source": "songsterr",
            "progression": chords,
        },
        store,
    )
    validate_song("s1", store)
    progs = list(iter_high_confidence_progressions(store))
    assert len(progs) == 1


def test_corpus_keeps_best_alignment_per_analysis(store: Store) -> None:
    """Two tabs for the same song -> the analysis is yielded once, with
    the higher alignment score."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    bad_tab = [{"symbol": "G", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", chords), store)
    ingest_tab_source(_tab("s1", chords), store)              # score 1.0
    ingest_tab_source(
        {
            "song_id": "s1",
            "source": "ultimate_guitar",
            "source_confidence": 0.9,
            "progression": bad_tab,
        },
        store,
    )                                                          # score 0.0
    validate_song("s1", store)

    progs = list(iter_high_confidence_progressions(store))
    assert len(progs) == 1
    assert progs[0]["best_alignment_score"] == pytest.approx(1.0)


def test_corpus_filters_chords_by_per_chord_confidence(store: Store) -> None:
    """When min_chord_confidence is set, chord dicts with conf below
    threshold are dropped; analyses with zero surviving chords are
    skipped entirely."""
    chords = [
        {"symbol": "C", "startSec": 0.0, "endSec": 2.0, "confidence": 0.95},
        {"symbol": "Am", "startSec": 2.0, "endSec": 4.0, "confidence": 0.2},
    ]
    ingest_analysis_bundle(_bundle("s1", chords), store)
    ingest_tab_source(_tab("s1", chords), store)
    validate_song("s1", store)

    # No per-chord gate -> both chords come through.
    progs = list(iter_high_confidence_progressions(store))
    assert len(progs[0]["chords"]) == 2

    # Gate at 0.5 -> only the C survives.
    progs2 = list(
        iter_high_confidence_progressions(store, min_chord_confidence=0.5)
    )
    assert len(progs2) == 1
    assert [c["symbol"] for c in progs2[0]["chords"]] == ["C"]


def test_corpus_stats_aggregates_score_range(store: Store) -> None:
    """Two qualifying songs with different alignment scores -> stats
    captures min/max/mean."""
    # Song 1: perfect agreement.
    _seed_perfect_song(store, "s1")
    # Song 2: partial agreement (0.5).
    jam = [
        {"symbol": "C", "startSec": 0.0, "endSec": 2.0},
        {"symbol": "G", "startSec": 2.0, "endSec": 4.0},
    ]
    tab = [
        {"symbol": "C", "startSec": 0.0, "endSec": 2.0},
        {"symbol": "Am", "startSec": 2.0, "endSec": 4.0},
    ]
    ingest_analysis_bundle(_bundle("s2", jam), store)
    ingest_tab_source(_tab("s2", tab), store)
    validate_song("s2", store)

    stats = corpus_stats(store, min_alignment_score=0.4)
    assert stats["matching_songs"] == 2
    assert stats["matching_analyses"] == 2
    assert stats["alignment_score_min"] == pytest.approx(0.5)
    assert stats["alignment_score_max"] == pytest.approx(1.0)
    assert stats["alignment_score_mean"] == pytest.approx(0.75)
    # 1 chord from s1 + 2 chords from s2.
    assert stats["total_chords"] == 3
