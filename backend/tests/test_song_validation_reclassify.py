"""Tests for ``song_validation.disagreement.reclassify``.

Pin the batch reclassifier's contract: classification labels are
updated in-place against existing alignment rows, the before/after
delta is reported, and the metrics roll-up is re-run for affected
engine_versions (unless explicitly suppressed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from song_validation import Store, validate_song
from song_validation.disagreement import (
    DisagreementClass,
    reclassify_all_alignments,
    reclassify_song,
)
from song_validation.ingestion import (
    ingest_analysis_bundle,
    ingest_tab_source,
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "sv.db")


def _bundle(
    song_id: str,
    chords: List[Dict[str, Any]],
    *,
    engine_version: str = "v1.0",
) -> Dict[str, Any]:
    return {
        "song_id": song_id,
        "chords": chords,
        "sections": [],
        "key": "C major",
        "tempo": 120.0,
        "engine_version": engine_version,
    }


def _tab(
    song_id: str,
    progression: List[Dict[str, Any]],
    *,
    source_confidence: float | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "song_id": song_id,
        "source": "songsterr",
        "progression": progression,
    }
    if source_confidence is not None:
        payload["source_confidence"] = source_confidence
    return payload


def _set_all_classifications(store: Store, value: str) -> None:
    """Stomp the classification column to ``value`` on every
    disagreements row. Simulates the 'rules changed under us, labels
    are now stale' situation the reclassifier exists to fix."""
    with store.connect() as conn:
        conn.execute(
            "UPDATE disagreements SET classification = ?", (value,)
        )


def test_reclassify_empty_store_is_noop(store: Store) -> None:
    result = reclassify_all_alignments(store)
    assert result["alignments_reclassified"] == 0
    assert result["before"] == {}
    assert result["after"] == {}
    assert result["delta"] == {}
    assert result["engine_versions_updated"] == []


def test_reclassify_relabels_stale_rows(store: Store) -> None:
    """Run the pipeline, stomp classifications to UNKNOWN, then
    reclassify -> labels come back."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", jam), store)
    ingest_tab_source(_tab("s1", tab), store)
    validate_song("s1", store)

    # Simulate stale labels.
    _set_all_classifications(store, DisagreementClass.UNKNOWN.value)

    result = reclassify_all_alignments(store)
    assert result["alignments_reclassified"] == 1
    # Before snapshot taken on the stale state.
    assert result["before"].get("UNKNOWN", 0) > 0
    # After snapshot reflects the true labels.
    assert result["after"].get("EXTENSION_COLLAPSE", 0) > 0
    assert result["after"].get("UNKNOWN", 0) == 0
    assert result["delta"]["EXTENSION_COLLAPSE"] > 0
    assert result["delta"]["UNKNOWN"] < 0


def test_reclassify_updates_engine_metrics_by_default(
    store: Store,
) -> None:
    """After reclassification the engine_metrics row reflects the
    NEW classification counts. We seed an explicitly-stale metrics
    row (stomp labels to UNKNOWN, re-aggregate so the row reflects
    that stomped state) and then assert that reclassify swings the
    row back to its true value."""
    from song_validation.metrics import aggregate_metrics

    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", jam), store)
    ingest_tab_source(_tab("s1", tab), store)
    validate_song("s1", store)

    # Stomp labels AND re-aggregate to bake the stale state in. After
    # this, extension_accuracy should be 1.0 (no EXTENSION_COLLAPSE
    # rows visible because they're all relabeled UNKNOWN).
    _set_all_classifications(store, DisagreementClass.UNKNOWN.value)
    aggregate_metrics("v1.0", store)
    stale = store.get_engine_metrics("v1.0")
    assert stale is not None
    assert stale["extension_accuracy"] == pytest.approx(1.0)

    result = reclassify_all_alignments(store)
    assert result["engine_versions_updated"] == ["v1.0"]
    fresh = store.get_engine_metrics("v1.0")
    assert fresh is not None
    # Now EXTENSION_COLLAPSE rows are visible -> extension_accuracy
    # drops back to 0.0 (every grid point is a disagreement of that
    # class).
    assert fresh["extension_accuracy"] == pytest.approx(0.0)


def test_reclassify_no_reaggregate_skips_metrics(store: Store) -> None:
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", jam), store)
    ingest_tab_source(_tab("s1", tab), store)
    validate_song("s1", store)

    result = reclassify_all_alignments(store, reaggregate_metrics=False)
    assert result["engine_versions_updated"] == []


def test_reclassify_song_scope_only_touches_that_song(
    store: Store,
) -> None:
    """Two songs with stale labels; reclassifying one leaves the other
    alone."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0}]
    for sid in ("s1", "s2"):
        ingest_analysis_bundle(_bundle(sid, jam), store)
        ingest_tab_source(_tab(sid, tab), store)
        validate_song(sid, store)

    _set_all_classifications(store, DisagreementClass.UNKNOWN.value)

    result = reclassify_song("s1", store)
    assert result["alignments_reclassified"] == 1

    # s1 should now carry EXTENSION_COLLAPSE rows; s2 should still be
    # all UNKNOWN.
    with store.connect() as conn:
        s1_classes = {
            cls for (cls,) in conn.execute(
                "SELECT DISTINCT classification FROM disagreements "
                "WHERE song_id = ?",
                ("s1",),
            )
        }
        s2_classes = {
            cls for (cls,) in conn.execute(
                "SELECT DISTINCT classification FROM disagreements "
                "WHERE song_id = ?",
                ("s2",),
            )
        }
    assert "EXTENSION_COLLAPSE" in s1_classes
    assert s2_classes == {"UNKNOWN"}


def test_reclassify_likely_tab_threshold_override_shifts_labels(
    store: Store,
) -> None:
    """A bare-disagreement row (no boundary/extension/slash/enharmonic
    rule fires) gets labeled LIKELY_TAB_ERROR if tab confidence is
    below threshold. Raising the threshold should let more rows hit
    that rule."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": "G", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", jam), store)
    # Set tab confidence to 0.5 — above the 0.4 default cutoff.
    ingest_tab_source(_tab("s1", tab, source_confidence=0.5), store)
    validate_song("s1", store)

    # Default threshold (0.4) doesn't catch this -> rows stay UNKNOWN
    # (no other rule applies for C vs G).
    baseline = reclassify_all_alignments(store)
    assert baseline["after"].get("LIKELY_TAB_ERROR", 0) == 0
    assert baseline["after"].get("UNKNOWN", 0) > 0

    # Raise threshold above 0.5 -> the same rows now hit LIKELY_TAB_ERROR.
    tuned = reclassify_all_alignments(
        store, likely_tab_error_threshold=0.6
    )
    assert tuned["after"].get("LIKELY_TAB_ERROR", 0) > 0
    assert tuned["after"].get("UNKNOWN", 0) == 0
    # Delta captures the swing.
    assert tuned["delta"]["LIKELY_TAB_ERROR"] > 0
    assert tuned["delta"]["UNKNOWN"] < 0


def test_reclassify_cli_subcommand_runs_pass(
    tmp_path: Path, store: Store
) -> None:
    """The CLI ``reclassify`` subcommand returns the reclassifier
    summary as JSON."""
    import io
    import json
    from song_validation import cli

    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", jam), store)
    ingest_tab_source(_tab("s1", tab), store)
    validate_song("s1", store)
    _set_all_classifications(store, DisagreementClass.UNKNOWN.value)

    buf = io.StringIO()
    rc = cli.main(
        ["--db", str(store.db_path), "reclassify"], out=buf
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["alignments_reclassified"] == 1
    assert payload["engine_versions_updated"] == ["v1.0"]
    assert payload["after"].get("EXTENSION_COLLAPSE", 0) > 0


def test_reclassify_cli_song_id_scope_passes_through(
    tmp_path: Path, store: Store
) -> None:
    """`--song-id` flag dispatches to ``reclassify_song``."""
    import io
    import json
    from song_validation import cli

    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0}]
    for sid in ("s1", "s2"):
        ingest_analysis_bundle(_bundle(sid, jam), store)
        ingest_tab_source(_tab(sid, tab), store)
        validate_song(sid, store)

    buf = io.StringIO()
    rc = cli.main(
        [
            "--db",
            str(store.db_path),
            "reclassify",
            "--song-id",
            "s1",
        ],
        out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["alignments_reclassified"] == 1  # only s1
