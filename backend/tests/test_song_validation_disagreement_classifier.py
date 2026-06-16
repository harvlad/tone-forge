"""Tests for ``song_validation.disagreement.classifier``.

The classifier is rule-based and conservative; these tests pin every
rule's positive path plus one or two negatives so the rule order
doesn't silently shift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from song_validation import Store
from song_validation.alignment import align_grid
from song_validation.disagreement import (
    DisagreementClass,
    classify_alignment,
    classify_disagreement,
)
from song_validation.ingestion import (
    ingest_analysis_bundle,
    ingest_tab_source,
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "sv.db")


# -------------------------------------------------- single-row rules


def test_classify_boundary_error_when_jam_matches_neighbour() -> None:
    """jam_chord matches the tab's NEXT chord -> BOUNDARY_ERROR."""
    tab = [
        {"symbol": "C", "startSec": 0.0, "endSec": 2.0},
        {"symbol": "G", "startSec": 2.0, "endSec": 4.0},
    ]
    cls = classify_disagreement(
        jam_chord="G",          # the next chord
        tab_chord="C",          # the active tab chord at t=1.5
        timestamp=1.5,
        tab_progression=tab,
    )
    assert cls == DisagreementClass.BOUNDARY_ERROR


def test_classify_boundary_error_matches_previous_chord() -> None:
    tab = [
        {"symbol": "C", "startSec": 0.0, "endSec": 2.0},
        {"symbol": "G", "startSec": 2.0, "endSec": 4.0},
    ]
    cls = classify_disagreement(
        jam_chord="C",          # the previous chord
        tab_chord="G",
        timestamp=2.5,
        tab_progression=tab,
    )
    assert cls == DisagreementClass.BOUNDARY_ERROR


def test_classify_extension_collapse() -> None:
    """Same root, one side has an extension the other dropped."""
    assert classify_disagreement(
        jam_chord="C",
        tab_chord="Cmaj7",
        timestamp=0.0,
        tab_progression=[],
    ) == DisagreementClass.EXTENSION_COLLAPSE
    assert classify_disagreement(
        jam_chord="G7",
        tab_chord="G",
        timestamp=0.0,
        tab_progression=[],
    ) == DisagreementClass.EXTENSION_COLLAPSE


def test_classify_slash_chord_collapse() -> None:
    """Same root + quality, one side has a slash bass the other dropped."""
    assert classify_disagreement(
        jam_chord="C",
        tab_chord="C/G",
        timestamp=0.0,
        tab_progression=[],
    ) == DisagreementClass.SLASH_CHORD_COLLAPSE
    assert classify_disagreement(
        jam_chord="Am/E",
        tab_chord="Am",
        timestamp=0.0,
        tab_progression=[],
    ) == DisagreementClass.SLASH_CHORD_COLLAPSE


def test_classify_key_context_error_enharmonic() -> None:
    """Enharmonic root pair, same quality -> KEY_CONTEXT_ERROR."""
    assert classify_disagreement(
        jam_chord="C#m",
        tab_chord="Dbm",
        timestamp=0.0,
        tab_progression=[],
    ) == DisagreementClass.KEY_CONTEXT_ERROR
    assert classify_disagreement(
        jam_chord="F#",
        tab_chord="Gb",
        timestamp=0.0,
        tab_progression=[],
    ) == DisagreementClass.KEY_CONTEXT_ERROR


def test_classify_likely_tab_error_when_confidence_low() -> None:
    """Tab confidence below threshold + unrelated chords -> LIKELY_TAB_ERROR."""
    cls = classify_disagreement(
        jam_chord="C",
        tab_chord="Bb",        # not a neighbour, not enharmonic, etc.
        timestamp=0.0,
        tab_progression=[],
        tab_source_confidence=0.1,
    )
    assert cls == DisagreementClass.LIKELY_TAB_ERROR


def test_classify_unknown_when_nothing_matches() -> None:
    cls = classify_disagreement(
        jam_chord="C",
        tab_chord="Bb",
        timestamp=0.0,
        tab_progression=[],
        tab_source_confidence=0.9,   # high confidence -> not tab error
    )
    assert cls == DisagreementClass.UNKNOWN


def test_classify_rule_order_boundary_beats_extension() -> None:
    """If jam matches the next chord AND has an extension diff, the
    BOUNDARY rule wins (more specific signal)."""
    tab = [
        {"symbol": "C", "startSec": 0.0, "endSec": 2.0},
        {"symbol": "G7", "startSec": 2.0, "endSec": 4.0},
    ]
    cls = classify_disagreement(
        jam_chord="G7",
        tab_chord="C",       # extension-collapse-like (C vs G7), but
                             # also exactly the next-chord pattern.
        timestamp=1.5,
        tab_progression=tab,
    )
    assert cls == DisagreementClass.BOUNDARY_ERROR


# ----------------------------------------------------- batch via DB


def test_classify_alignment_batch_updates_rows(store: Store) -> None:
    """End-to-end: alignment emits UNKNOWN rows, classifier rewrites
    each row's classification column based on the rule table."""
    jam = [
        {"symbol": "C", "startSec": 0.0, "endSec": 2.0},
        {"symbol": "G", "startSec": 2.0, "endSec": 4.0},
    ]
    tab = [
        {"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0},  # ext collapse vs jam=C
        {"symbol": "G",     "startSec": 2.0, "endSec": 4.0},  # agrees
    ]
    a_id = ingest_analysis_bundle(
        {
            "song_id": "s1",
            "chords": jam,
            "sections": [],
            "key": "C major",
            "tempo": 120.0,
        },
        store,
    )
    t_id = ingest_tab_source(
        {"song_id": "s1", "source": "songsterr", "progression": tab},
        store,
    )
    al_id = align_grid(a_id, t_id, store, step_sec=0.5)

    counts = classify_alignment(al_id, store)

    # 4 disagreement rows for [0, 2.0), all EXTENSION_COLLAPSE.
    assert counts[DisagreementClass.EXTENSION_COLLAPSE.value] == 4
    assert counts[DisagreementClass.UNKNOWN.value] == 0

    rows = store.list_disagreements_for_alignment(al_id)
    assert len(rows) == 4
    for r in rows:
        assert r["classification"] == DisagreementClass.EXTENSION_COLLAPSE.value


def test_classify_alignment_uses_tab_source_confidence(store: Store) -> None:
    """Low source_confidence -> non-matching disagreements become
    LIKELY_TAB_ERROR rather than UNKNOWN."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": "Bb", "startSec": 0.0, "endSec": 2.0}]
    a_id = ingest_analysis_bundle(
        {
            "song_id": "s1",
            "chords": jam,
            "sections": [],
            "key": "C major",
            "tempo": 120.0,
        },
        store,
    )
    t_id = ingest_tab_source(
        {
            "song_id": "s1",
            "source": "manual",
            "source_confidence": 0.1,
            "progression": tab,
        },
        store,
    )
    al_id = align_grid(a_id, t_id, store, step_sec=0.5)
    counts = classify_alignment(al_id, store)
    assert counts[DisagreementClass.LIKELY_TAB_ERROR.value] == 4
    assert counts[DisagreementClass.UNKNOWN.value] == 0


def test_classify_alignment_unknown_alignment_raises(store: Store) -> None:
    with pytest.raises(ValueError, match="alignment not found"):
        classify_alignment("nope", store)
