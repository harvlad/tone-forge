"""``python -m bench.sweep`` -- run a parameter sweep.

Iterates a YAML-defined search space, runs ``bench.benchmark`` for
every candidate, applies the M1.6 acceptance gate against a
baseline run, and emits a sweep directory with:

* ``<run_id>.json``  -- one ``RunRecord`` per candidate
* ``baseline.json``  -- the baseline RunRecord
* ``index.csv``      -- per-candidate summary row
* ``accepted.json``  -- candidates that cleared the gate, sorted by
                         corpus delta

Sweep does NOT auto-promote a config to production. The output is
evidence; a human reviews ``accepted.json`` and decides whether to
change ``DetectorConfig`` defaults in a separate commit.

CLI surface::

    python -m bench.sweep <space.yaml>
        [--baseline <run_id|path>]   # comparison anchor; default: fresh baseline
        [--workers N]                 # multiprocessing.Pool size; default 1
        [--output <dir>]              # default: bench/runs/sweep_<id>/
        [--corpus <dir>]              # propagated to bench.benchmark

YAML space schema (validated at load time)::

    strategy: grid | random | coordinate_descent
    seed: <int>                        # used by random / coordinate_descent
    budget: <int>                      # max candidates for random
    acceptance:
      corpus_metric: <CorpusResult-field>
      corpus_must_strictly_improve: true | false
      max_per_fixture_drop_pp: <float>
      max_runtime_factor: <float>
      max_memory_factor: <float>
    parameters:
      <DetectorConfig-field>:
        type: float | int
        range: [<min>, <max>]
        step: <step>
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import itertools
import json
import random
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from bench.store import RunRecord, dump_run_record, load_run_record


__all__ = [
    "AcceptanceConfig",
    "SweepSpace",
    "load_space",
    "enumerate_candidates",
    "evaluate_acceptance",
    "run_sweep",
    "main",
]


# ---------------------------------------------------------------------------
# Sweep-space schema
# ---------------------------------------------------------------------------


_STRATEGIES = {"grid", "random", "coordinate_descent"}


@dataclass(frozen=True)
class AcceptanceConfig:
    """The M1.6 acceptance-gate parameters."""

    corpus_metric: str
    corpus_must_strictly_improve: bool
    max_per_fixture_drop_pp: float       # absolute percentage points
    max_runtime_factor: float
    max_memory_factor: float


@dataclass(frozen=True)
class SweepSpace:
    """Parsed YAML sweep space (strategy + acceptance + parameter ranges)."""

    strategy: str
    seed: int
    budget: int
    acceptance: AcceptanceConfig
    parameters: Mapping[str, Mapping[str, Any]]


def _validate_parameter_field(name: str, spec: Mapping[str, Any]) -> None:
    if "type" not in spec or spec["type"] not in {"float", "int"}:
        raise ValueError(f"parameter {name!r}: type must be 'float' or 'int'")
    rng = spec.get("range")
    if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
        raise ValueError(f"parameter {name!r}: range must be [min, max]")
    if "step" not in spec:
        raise ValueError(f"parameter {name!r}: step is required")


def load_space(path: Path | str) -> SweepSpace:
    """Parse + validate a YAML sweep-space file."""
    import yaml

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level must be a mapping")

    strategy = raw.get("strategy", "grid")
    if strategy not in _STRATEGIES:
        raise ValueError(
            f"{path}: strategy must be one of {sorted(_STRATEGIES)}, "
            f"got {strategy!r}"
        )

    accept_raw = raw.get("acceptance", {})
    if not isinstance(accept_raw, dict):
        raise ValueError(f"{path}: acceptance must be a mapping")
    acceptance = AcceptanceConfig(
        corpus_metric=str(accept_raw.get(
            "corpus_metric", "wcsr_triad_relaxed_mean"
        )),
        corpus_must_strictly_improve=bool(accept_raw.get(
            "corpus_must_strictly_improve", True
        )),
        max_per_fixture_drop_pp=float(accept_raw.get(
            "max_per_fixture_drop_pp", 5.0
        )),
        max_runtime_factor=float(accept_raw.get("max_runtime_factor", 2.0)),
        max_memory_factor=float(accept_raw.get("max_memory_factor", 1.5)),
    )

    parameters = raw.get("parameters", {})
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError(f"{path}: parameters must be a non-empty mapping")
    for name, spec in parameters.items():
        if not isinstance(spec, dict):
            raise ValueError(f"parameter {name!r}: spec must be a mapping")
        _validate_parameter_field(name, spec)

    return SweepSpace(
        strategy=strategy,
        seed=int(raw.get("seed", 0)),
        budget=int(raw.get("budget", 64)),
        acceptance=acceptance,
        parameters=parameters,
    )


# ---------------------------------------------------------------------------
# Candidate enumeration
# ---------------------------------------------------------------------------


def _axis_values(spec: Mapping[str, Any]) -> list[float]:
    """Inclusive range, snapped to step. Floats are kept; ints are cast."""
    lo, hi = float(spec["range"][0]), float(spec["range"][1])
    step = float(spec["step"])
    if step <= 0:
        raise ValueError(f"step must be > 0, got {step}")
    vals: list[float] = []
    v = lo
    # Pad by step/2 to absorb float drift on the upper bound.
    while v <= hi + step / 2:
        vals.append(round(v / step) * step)
        v += step
    # Drop dupes from rounding without sort instability.
    seen: set[float] = set()
    out: list[float] = []
    for x in vals:
        if x not in seen:
            seen.add(x)
            out.append(x)
    if spec["type"] == "int":
        out = [float(int(x)) for x in out]
    return out


def enumerate_candidates(space: SweepSpace) -> list[dict[str, float]]:
    """Return the ordered list of candidate configs for ``space``.

    Each entry is a ``{field: value}`` overlay applied on top of
    ``DetectorConfig()``. The detector default values for fields
    NOT mentioned in the space are inherited automatically.

    Strategies:

    * ``grid``                -- cartesian product of all axes.
    * ``random``              -- ``budget`` distinct candidates
                                  sampled uniformly from the grid
                                  (seeded by ``space.seed``).
    * ``coordinate_descent``  -- starting from each axis midpoint,
                                  vary one axis at a time. Equivalent
                                  to ``len(axes) * mean(axis_card)``
                                  candidates, far smaller than grid.
    """
    axes = {name: _axis_values(spec) for name, spec in space.parameters.items()}

    if space.strategy == "grid":
        names = list(axes)
        combos = itertools.product(*(axes[n] for n in names))
        return [dict(zip(names, c)) for c in combos]

    if space.strategy == "random":
        rng = random.Random(space.seed)
        names = list(axes)
        all_combos = list(itertools.product(*(axes[n] for n in names)))
        rng.shuffle(all_combos)
        keep = all_combos[: max(0, int(space.budget))]
        return [dict(zip(names, c)) for c in keep]

    # coordinate_descent
    names = list(axes)
    # Center anchor: axis median (deterministic).
    anchor = {n: axes[n][len(axes[n]) // 2] for n in names}
    seen: set[tuple] = set()
    out: list[dict[str, float]] = []
    for n in names:
        for v in axes[n]:
            cand = dict(anchor)
            cand[n] = v
            key = tuple(cand[k] for k in names)
            if key in seen:
                continue
            seen.add(key)
            out.append(cand)
    return out


# ---------------------------------------------------------------------------
# Acceptance gate (M1.6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcceptanceVerdict:
    accepted: bool
    rejection_reason: Optional[str]
    corpus_delta: float


def _corpus_metric(record: RunRecord, name: str) -> float:
    value = getattr(record.corpus, name, None)
    if value is None:
        raise ValueError(
            f"CorpusResult has no field {name!r} "
            f"(known: {[f.name for f in dataclasses.fields(record.corpus)]})"
        )
    return float(value)


def evaluate_acceptance(
    candidate: RunRecord,
    baseline: RunRecord,
    rules: AcceptanceConfig,
) -> AcceptanceVerdict:
    """Pure function: does ``candidate`` pass the M1.6 gate vs ``baseline``?

    Implements the four rules:

    1. ``candidate.corpus.<rules.corpus_metric> > baseline.corpus.<...>``
       (strict improvement, when ``corpus_must_strictly_improve``).
    2. For every fixture in baseline: candidate's per-fixture
       triad-relaxed WCSR is not below baseline's by more than
       ``max_per_fixture_drop_pp / 100`` (5pp -> 0.05).
    3. ``candidate.corpus.wall_seconds_mean <=
       max_runtime_factor * baseline.corpus.wall_seconds_mean``.
    4. ``candidate.corpus.peak_rss_mb_max <=
       max_memory_factor * baseline.corpus.peak_rss_mb_max``.
    """
    base_score = _corpus_metric(baseline, rules.corpus_metric)
    cand_score = _corpus_metric(candidate, rules.corpus_metric)
    delta = cand_score - base_score

    if rules.corpus_must_strictly_improve and delta <= 0:
        return AcceptanceVerdict(
            False,
            f"corpus {rules.corpus_metric} did not strictly improve "
            f"({cand_score:.4f} <= {base_score:.4f})",
            delta,
        )

    drop_threshold = rules.max_per_fixture_drop_pp / 100.0
    for name, base_fr in baseline.per_fixture.items():
        if name not in candidate.per_fixture:
            return AcceptanceVerdict(
                False,
                f"candidate is missing fixture {name!r}",
                delta,
            )
        cand_fr = candidate.per_fixture[name]
        drop = base_fr.wcsr_triad_relaxed - cand_fr.wcsr_triad_relaxed
        if drop > drop_threshold:
            return AcceptanceVerdict(
                False,
                f"fixture {name!r} dropped by {drop * 100:.2f}pp "
                f"(>{rules.max_per_fixture_drop_pp:.2f}pp)",
                delta,
            )

    if baseline.corpus.wall_seconds_mean > 0:
        runtime_cap = (
            rules.max_runtime_factor * baseline.corpus.wall_seconds_mean
        )
        if candidate.corpus.wall_seconds_mean > runtime_cap:
            return AcceptanceVerdict(
                False,
                f"wall_seconds_mean {candidate.corpus.wall_seconds_mean:.3f} "
                f"> {runtime_cap:.3f}",
                delta,
            )

    if baseline.corpus.peak_rss_mb_max > 0:
        mem_cap = rules.max_memory_factor * baseline.corpus.peak_rss_mb_max
        if candidate.corpus.peak_rss_mb_max > mem_cap:
            return AcceptanceVerdict(
                False,
                f"peak_rss_mb_max {candidate.corpus.peak_rss_mb_max:.1f} "
                f"> {mem_cap:.1f}",
                delta,
            )

    return AcceptanceVerdict(True, None, delta)


# ---------------------------------------------------------------------------
# Sweep driver
# ---------------------------------------------------------------------------


def _build_detector_config(overlay: Mapping[str, float]):
    """Construct ``DetectorConfig`` with ``overlay`` applied on defaults."""
    from tone_forge.analysis.detector_config import DetectorConfig

    known = {f.name: f for f in dataclasses.fields(DetectorConfig)}
    unknown = set(overlay) - set(known)
    if unknown:
        raise ValueError(
            f"unknown DetectorConfig field(s): {sorted(unknown)}"
        )
    coerced: dict[str, Any] = {}
    for k, v in overlay.items():
        spec = known[k]
        if spec.type in (int, "int"):
            coerced[k] = int(v)
        else:
            coerced[k] = float(v)
    return DetectorConfig(**coerced)


def _write_index_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def _resolve_baseline(
    arg: Optional[str],
    *,
    corpus_dir: Optional[Path],
    output_dir: Path,
    splits: Optional[tuple[str, ...]] = None,
) -> RunRecord:
    """Load an existing baseline JSON, or run a fresh baseline benchmark."""
    if arg is not None:
        p = Path(arg)
        if p.is_file():
            return load_run_record(p)
        # Treat the arg as a run_id and look it up under ``bench/runs/``.
        runs_dir = Path(__file__).resolve().parent / "runs"
        candidate = runs_dir / f"{arg}.json"
        if candidate.is_file():
            return load_run_record(candidate)
        raise FileNotFoundError(
            f"--baseline {arg!r} is not a file and not a known run_id"
        )
    # Fresh baseline.
    from bench.benchmark import run_benchmark
    baseline_path = output_dir / "baseline.json"
    return run_benchmark(
        config=None,
        corpus_dir=corpus_dir,
        output_path=baseline_path,
        require_audio=True,
        splits=splits,
    )


def run_sweep(
    space_path: Path | str,
    *,
    baseline: Optional[str] = None,
    workers: int = 1,
    output_dir: Optional[Path] = None,
    corpus_dir: Optional[Path] = None,
    splits: Optional[tuple[str, ...]] = None,
) -> dict[str, Any]:
    """Drive a sweep end-to-end and return a summary dict.

    ``splits`` (M2.5), when not ``None``, restricts the corpus to
    fixtures whose ``split`` field is in the tuple. The same filter
    is applied to the baseline benchmark and every candidate benchmark
    so all comparisons are apples-to-apples.
    """
    from bench.benchmark import run_benchmark

    space = load_space(space_path)
    sweep_id = uuid.uuid4().hex[:8]
    out_root = Path(output_dir) if output_dir is not None else (
        Path(__file__).resolve().parent / "runs" / f"sweep_{sweep_id}"
    )
    out_root.mkdir(parents=True, exist_ok=True)

    baseline_record = _resolve_baseline(
        baseline,
        corpus_dir=corpus_dir,
        output_dir=out_root,
        splits=splits,
    )
    candidates = enumerate_candidates(space)

    index_rows: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []

    # multiprocessing is left to a follow-up: the M1 plan permits
    # workers >=1 but the current loop runs serial. The fan-out
    # pattern is identical; ``workers`` is accepted-and-ignored for
    # now so callers don't need to change when parallelism lands.
    _ = workers

    for i, overlay in enumerate(candidates):
        cfg = _build_detector_config(overlay)
        cand_path = out_root / f"candidate_{i:04d}.json"
        record = run_benchmark(
            config=cfg,
            corpus_dir=corpus_dir,
            output_path=cand_path,
            require_audio=True,
            parent_baseline_run_id=baseline_record.run_id,
            splits=splits,
            extra={"sweep_id": sweep_id, "candidate_index": i, "overlay": overlay},
        )
        verdict = evaluate_acceptance(record, baseline_record, space.acceptance)
        # Persist the verdict by rewriting the record's rejection_reason.
        record_with_verdict = dataclasses.replace(
            record,
            rejection_reason=verdict.rejection_reason,
        )
        dump_run_record(record_with_verdict, cand_path)

        row: dict[str, Any] = {
            "candidate_index": i,
            "run_id": record.run_id,
            "accepted": verdict.accepted,
            "corpus_delta": verdict.corpus_delta,
            "rejection_reason": verdict.rejection_reason or "",
            "wcsr_triad_relaxed_mean": record.corpus.wcsr_triad_relaxed_mean,
            "wall_seconds_mean": record.corpus.wall_seconds_mean,
            "peak_rss_mb_max": record.corpus.peak_rss_mb_max,
        }
        # Flatten the overlay onto the CSV row for readability.
        for k, v in overlay.items():
            row[f"p_{k}"] = v
        index_rows.append(row)

        if verdict.accepted:
            accepted.append({
                "run_id": record.run_id,
                "corpus_delta": verdict.corpus_delta,
                "wcsr_triad_relaxed_mean": record.corpus.wcsr_triad_relaxed_mean,
                "overlay": overlay,
            })

    accepted.sort(key=lambda r: r["corpus_delta"], reverse=True)
    (out_root / "accepted.json").write_text(
        json.dumps(accepted, indent=2) + "\n", encoding="utf-8"
    )
    _write_index_csv(out_root / "index.csv", index_rows)

    return {
        "sweep_id": sweep_id,
        "output_dir": str(out_root),
        "baseline_run_id": baseline_record.run_id,
        "n_candidates": len(candidates),
        "n_accepted": len(accepted),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m bench.sweep",
        description="Run a chord-detector parameter sweep.",
    )
    p.add_argument("space", type=Path, help="YAML sweep-space definition")
    p.add_argument("--baseline", default=None,
                   help="run_id or path to an existing baseline RunRecord JSON")
    p.add_argument("--workers", type=int, default=1,
                   help="multiprocessing.Pool size (currently advisory; "
                        "the sweep runs serial)")
    p.add_argument("--output", type=Path, default=None,
                   help="output dir (default: bench/runs/sweep_<id>/)")
    p.add_argument("--corpus", type=Path, default=None,
                   help="alternative fixtures dir (propagated to benchmark)")
    p.add_argument("--split", action="append", default=None,
                   choices=("train", "val", "test", "holdout"),
                   help="restrict corpus to fixtures with this split "
                        "(may be repeated; applied uniformly to baseline "
                        "and every candidate)")
    return p


def main(argv: list[str]) -> int:
    args = _build_argparser().parse_args(argv)
    summary = run_sweep(
        args.space,
        baseline=args.baseline,
        workers=args.workers,
        output_dir=args.output,
        corpus_dir=args.corpus,
        splits=tuple(args.split) if args.split else None,
    )
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
