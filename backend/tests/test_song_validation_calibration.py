"""Tests for ``song_validation.disagreement.calibration``.

Pin the contract of the confidence calibration report: it profiles
ONLY the UNKNOWN+LIKELY_TAB_ERROR slice (since the threshold can't
affect any other class), it projects relabel counts at each
candidate threshold without mutating the store, and the histogram
bins the source_confidence distribution at 0.1 granularity.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from song_validation import Store, validate_song
from song_validation.disagreement import (
    DEFAULT_CANDIDATE_THRESHOLDS,
    DisagreementClass,
    confidence_calibration_report,
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
    source: str = "songsterr",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "song_id": song_id,
        "source": source,
        "progression": progression,
    }
    if source_confidence is not None:
        payload["source_confidence"] = source_confidence
    return payload


def test_calibration_empty_store_returns_zeroed_shape(store: Store) -> None:
    report = confidence_calibration_report(store)
    assert report["candidate_pool"] == 0
    assert report["current_label_counts"] == {
        DisagreementClass.LIKELY_TAB_ERROR.value: 0,
        DisagreementClass.UNKNOWN.value: 0,
    }
    # Histogram pre-seeded with all bins (zeroed) + null bucket.
    assert "0.0-0.1" in report["confidence_histogram"]
    assert "0.9-1.0" in report["confidence_histogram"]
    assert "null" in report["confidence_histogram"]
    assert all(v == 0 for v in report["confidence_histogram"].values())
    # Default projections still emitted, all zero counts.
    assert len(report["projections"]) == len(DEFAULT_CANDIDATE_THRESHOLDS)
    for p in report["projections"]:
        assert p["rows_would_be_likely"] == 0
        assert p["rows_would_be_unknown"] == 0
        assert p["rows_would_gain_label"] == 0
        assert p["rows_would_lose_label"] == 0


def test_calibration_excludes_non_threshold_classes(store: Store) -> None:
    """A disagreement of class EXTENSION_COLLAPSE is independent of
    the threshold (fires earlier in rule order) and must not appear
    in the candidate pool."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", jam), store)
    ingest_tab_source(_tab("s1", tab, source_confidence=0.1), store)
    validate_song("s1", store)

    report = confidence_calibration_report(store)
    # EXTENSION_COLLAPSE rows present in the store but excluded from
    # the calibration pool — pool is empty.
    assert report["candidate_pool"] == 0


def test_calibration_pool_counts_unknown_and_likely_only(
    store: Store,
) -> None:
    """A bare disagreement (no other rule fires) with low confidence
    lands in LIKELY_TAB_ERROR; one with high confidence stays
    UNKNOWN. Both must show up in the candidate pool."""
    # s1: low-confidence tab -> LIKELY_TAB_ERROR
    ingest_analysis_bundle(
        _bundle("s1", [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]),
        store,
    )
    ingest_tab_source(
        _tab(
            "s1",
            [{"symbol": "G", "startSec": 0.0, "endSec": 2.0}],
            source_confidence=0.1,
        ),
        store,
    )
    validate_song("s1", store)

    # s2: high-confidence tab -> stays UNKNOWN at default threshold
    ingest_analysis_bundle(
        _bundle("s2", [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]),
        store,
    )
    ingest_tab_source(
        _tab(
            "s2",
            [{"symbol": "G", "startSec": 0.0, "endSec": 2.0}],
            source_confidence=0.9,
        ),
        store,
    )
    validate_song("s2", store)

    report = confidence_calibration_report(store)
    assert report["candidate_pool"] >= 2
    assert (
        report["current_label_counts"][
            DisagreementClass.LIKELY_TAB_ERROR.value
        ]
        >= 1
    )
    assert (
        report["current_label_counts"][DisagreementClass.UNKNOWN.value]
        >= 1
    )


def test_calibration_projection_at_threshold_zero_makes_all_unknown(
    store: Store,
) -> None:
    """At threshold 0.0, no row's confidence can be strictly less, so
    every row projects to UNKNOWN."""
    ingest_analysis_bundle(
        _bundle("s1", [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]),
        store,
    )
    ingest_tab_source(
        _tab(
            "s1",
            [{"symbol": "G", "startSec": 0.0, "endSec": 2.0}],
            source_confidence=0.1,
        ),
        store,
    )
    validate_song("s1", store)

    report = confidence_calibration_report(
        store, candidate_thresholds=[0.0]
    )
    p = report["projections"][0]
    assert p["threshold"] == 0.0
    assert p["rows_would_be_likely"] == 0
    assert p["rows_would_be_unknown"] == report["candidate_pool"]


def test_calibration_projection_at_high_threshold_promotes_to_likely(
    store: Store,
) -> None:
    """At threshold 1.01, every row with non-null source_confidence
    flips to LIKELY_TAB_ERROR (1.0 < 1.01 is true)."""
    ingest_analysis_bundle(
        _bundle("s1", [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]),
        store,
    )
    ingest_tab_source(
        _tab(
            "s1",
            [{"symbol": "G", "startSec": 0.0, "endSec": 2.0}],
            source_confidence=0.9,
        ),
        store,
    )
    validate_song("s1", store)

    report = confidence_calibration_report(
        store, candidate_thresholds=[1.01]
    )
    p = report["projections"][0]
    assert p["rows_would_be_likely"] == report["candidate_pool"]
    assert p["rows_would_be_unknown"] == 0


def test_calibration_null_confidence_stays_unknown(store: Store) -> None:
    """A row whose tab has NULL source_confidence must stay UNKNOWN
    at every threshold (matches the classifier's semantics)."""
    ingest_analysis_bundle(
        _bundle("s1", [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]),
        store,
    )
    # No source_confidence on the tab -> NULL in DB.
    ingest_tab_source(
        _tab(
            "s1",
            [{"symbol": "G", "startSec": 0.0, "endSec": 2.0}],
        ),
        store,
    )
    validate_song("s1", store)

    report = confidence_calibration_report(
        store, candidate_thresholds=[0.5, 1.0]
    )
    # All projections show 0 likely, all in unknown.
    for p in report["projections"]:
        assert p["rows_would_be_likely"] == 0
        assert p["rows_would_be_unknown"] == report["candidate_pool"]
    # The histogram counts this row in the "null" bucket.
    assert report["confidence_histogram"]["null"] >= 1


def test_calibration_histogram_bins_confidence_correctly(
    store: Store,
) -> None:
    """A confidence of 0.35 lands in the 0.3-0.4 bin; 1.0 lands in
    the top bin (0.9-1.0), not a phantom 1.0-1.1."""
    ingest_analysis_bundle(
        _bundle("s1", [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]),
        store,
    )
    ingest_tab_source(
        _tab(
            "s1",
            [{"symbol": "G", "startSec": 0.0, "endSec": 2.0}],
            source_confidence=0.35,
        ),
        store,
    )
    validate_song("s1", store)

    ingest_analysis_bundle(
        _bundle("s2", [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]),
        store,
    )
    ingest_tab_source(
        _tab(
            "s2",
            [{"symbol": "G", "startSec": 0.0, "endSec": 2.0}],
            source_confidence=1.0,
        ),
        store,
    )
    validate_song("s2", store)

    report = confidence_calibration_report(store)
    assert report["confidence_histogram"]["0.3-0.4"] >= 1
    assert report["confidence_histogram"]["0.9-1.0"] >= 1


def test_calibration_is_pure_diagnostic_no_mutation(
    store: Store,
) -> None:
    """Calling the report must not touch any disagreement row."""
    ingest_analysis_bundle(
        _bundle("s1", [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]),
        store,
    )
    ingest_tab_source(
        _tab(
            "s1",
            [{"symbol": "G", "startSec": 0.0, "endSec": 2.0}],
            source_confidence=0.5,
        ),
        store,
    )
    validate_song("s1", store)

    with store.connect() as conn:
        before = sorted(
            conn.execute(
                "SELECT disagreement_id, classification FROM disagreements"
            ).fetchall()
        )

    confidence_calibration_report(
        store, candidate_thresholds=[0.0, 0.5, 1.0]
    )

    with store.connect() as conn:
        after = sorted(
            conn.execute(
                "SELECT disagreement_id, classification FROM disagreements"
            ).fetchall()
        )
    assert before == after


def test_calibration_default_thresholds_span_zero_to_one(
    store: Store,
) -> None:
    report = confidence_calibration_report(store)
    thresholds = [p["threshold"] for p in report["projections"]]
    assert thresholds[0] == 0.0
    assert thresholds[-1] == 1.0
    assert len(thresholds) == 11


def test_calibration_current_threshold_in_payload(store: Store) -> None:
    report = confidence_calibration_report(
        store, current_threshold=0.42
    )
    assert report["current_threshold"] == pytest.approx(0.42)


def test_calibration_cli_subcommand_runs(
    tmp_path: Path, store: Store
) -> None:
    """CLI ``report calibrate`` returns the report JSON."""
    from song_validation import cli

    ingest_analysis_bundle(
        _bundle("s1", [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]),
        store,
    )
    ingest_tab_source(
        _tab(
            "s1",
            [{"symbol": "G", "startSec": 0.0, "endSec": 2.0}],
            source_confidence=0.2,
        ),
        store,
    )
    validate_song("s1", store)

    buf = io.StringIO()
    rc = cli.main(
        ["--db", str(store.db_path), "report", "calibrate"],
        out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert "current_threshold" in payload
    assert "candidate_pool" in payload
    assert "confidence_histogram" in payload
    assert "projections" in payload
    assert payload["candidate_pool"] >= 1


def test_calibration_cli_candidate_thresholds_flag(
    tmp_path: Path, store: Store
) -> None:
    """``--candidate-thresholds`` parses a comma-separated list."""
    from song_validation import cli

    buf = io.StringIO()
    rc = cli.main(
        [
            "--db",
            str(store.db_path),
            "report",
            "calibrate",
            "--candidate-thresholds",
            "0.3,0.5,0.7",
        ],
        out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert [p["threshold"] for p in payload["projections"]] == [
        0.3,
        0.5,
        0.7,
    ]
