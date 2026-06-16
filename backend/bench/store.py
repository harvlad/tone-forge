"""RunRecord serialization for the chord-detector benchmark.

Three frozen-ish dataclasses + JSON round-trip helpers:

* ``FixtureResult`` -- per-fixture metric panel + wall/RSS.
* ``CorpusResult``  -- aggregate-across-fixtures summary.
* ``RunRecord``     -- top-level record per benchmark invocation.

The serialization format is plain JSON with predictable key
ordering. Floats round-trip to whatever precision ``json.dump``
emits by default. ``Path`` and ``datetime`` are NOT used inside the
records (we hold them as ``str``) so the JSON is trivially
``json.load``-able by downstream tooling.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple


__all__ = [
    "FixtureResult",
    "CorpusResult",
    "RunRecord",
    "dump_run_record",
    "load_run_record",
]


@dataclass
class FixtureResult:
    """Per-fixture metric panel produced by one detector run."""

    wcsr_triad_relaxed: float
    wcsr_strict: float
    chord_error_rate: float
    boundary_iou_0p5: float
    region_stability_per_min: float
    expected_calibration_error: float
    wall_seconds: float
    peak_rss_mb: float


@dataclass
class CorpusResult:
    """Aggregate-across-fixtures summary for the corpus.

    Means are unweighted (one entry per fixture) so the corpus
    score doesn't drift with fixture duration. ``peak_rss_mb_max``
    is the max RSS seen across any single fixture run, which is
    the relevant bound for the M1.6 memory acceptance check.
    """

    n_fixtures: int
    wcsr_triad_relaxed_mean: float
    wcsr_strict_mean: float
    chord_error_rate_mean: float
    boundary_iou_0p5_mean: float
    region_stability_per_min_mean: float
    expected_calibration_error_mean: float
    wall_seconds_mean: float
    peak_rss_mb_max: float


@dataclass
class RunRecord:
    """Top-level record per ``bench.benchmark`` invocation."""

    run_id: str
    timestamp_utc: str
    git_sha: Optional[str]
    config: Mapping[str, object]
    corpus_dir: str
    per_fixture: Mapping[str, FixtureResult]
    corpus: CorpusResult
    wall_seconds_total: float
    rejection_reason: Optional[str] = None
    parent_baseline_run_id: Optional[str] = None
    # M2.5: corpus splits the benchmark restricted to. ``None`` means
    # "no filter" (current M1 behaviour: all fixtures). When set, all
    # benchmark runs within a sweep share the same value, so reported
    # corpus_mean comparisons are apples-to-apples.
    splits: Optional[Tuple[str, ...]] = None
    # Free-form metadata bucket (sweep id, strategy name, etc.).
    extra: Mapping[str, object] = field(default_factory=dict)


def _record_to_jsonable(record: RunRecord) -> dict:
    """Convert RunRecord -> nested plain-dict (for ``json.dump``)."""
    return {
        "run_id": record.run_id,
        "timestamp_utc": record.timestamp_utc,
        "git_sha": record.git_sha,
        "config": dict(record.config),
        "corpus_dir": record.corpus_dir,
        "per_fixture": {
            name: asdict(res) for name, res in record.per_fixture.items()
        },
        "corpus": asdict(record.corpus),
        "wall_seconds_total": record.wall_seconds_total,
        "rejection_reason": record.rejection_reason,
        "parent_baseline_run_id": record.parent_baseline_run_id,
        "splits": list(record.splits) if record.splits is not None else None,
        "extra": dict(record.extra),
    }


def dump_run_record(record: RunRecord, path: Path | str) -> Path:
    """Write ``record`` as JSON to ``path``. Creates parent dirs."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        json.dump(_record_to_jsonable(record), fh, indent=2, sort_keys=False)
        fh.write("\n")
    return target


def load_run_record(path: Path | str) -> RunRecord:
    """Read a previously-dumped RunRecord JSON back into a dataclass."""
    src = Path(path)
    with src.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    per_fixture = {
        name: FixtureResult(**fr) for name, fr in data["per_fixture"].items()
    }
    corpus = CorpusResult(**data["corpus"])
    raw_splits = data.get("splits")
    splits: Optional[Tuple[str, ...]] = (
        tuple(raw_splits) if raw_splits is not None else None
    )
    return RunRecord(
        run_id=data["run_id"],
        timestamp_utc=data["timestamp_utc"],
        git_sha=data.get("git_sha"),
        config=data.get("config", {}),
        corpus_dir=data["corpus_dir"],
        per_fixture=per_fixture,
        corpus=corpus,
        wall_seconds_total=float(data["wall_seconds_total"]),
        rejection_reason=data.get("rejection_reason"),
        parent_baseline_run_id=data.get("parent_baseline_run_id"),
        splits=splits,
        extra=data.get("extra", {}),
    )
