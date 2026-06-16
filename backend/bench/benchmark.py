"""``python -m bench.benchmark`` -- run the corpus benchmark.

Iterates the corpus, runs the production chord detector on every
fixture under a single ``DetectorConfig``, measures the six
metrics from ``bench.metrics`` per fixture plus wall-clock and
peak RSS, aggregates into a corpus summary, and persists a
``RunRecord`` JSON to ``backend/bench/runs/<run_id>.json``.

CLI surface:

    python -m bench.benchmark
        [--config <path.json>]   # optional DetectorConfig override
        [--corpus <dir>]         # alternative fixtures dir
        [--output <path>]        # default: bench/runs/<run_id>.json
        [--quiet | --json-only]

The DetectorConfig JSON file is a plain ``{field: value}`` mapping;
unknown fields raise. When no ``--config`` is given, the default
``DetectorConfig()`` is used, which by construction reproduces the
pre-M1 hardcoded constants. Production behaviour is therefore
unaffected by anything ``bench.benchmark`` does.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import resource
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

from bench.corpus import DEFAULT_FIXTURES_DIR, CorpusFixture, iter_corpus_fixtures
from bench.metrics import (
    boundary_iou,
    chord_error_rate,
    expected_calibration_error,
    region_stability,
    strict_wcsr_score,
    triad_relaxed_wcsr_score,
)
from bench.store import CorpusResult, FixtureResult, RunRecord, dump_run_record


# ---------------------------------------------------------------------------
# DetectorConfig (de)serialization
# ---------------------------------------------------------------------------


def _load_detector_config(path: Optional[Path]):
    """Load a ``DetectorConfig`` from a JSON file, or return defaults.

    Local import to keep ``bench.*`` importable without ``tone_forge``
    being on the path (useful for unit tests that exercise only the
    pure-Python helpers).
    """
    from tone_forge.analysis.detector_config import DetectorConfig

    if path is None:
        return DetectorConfig()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: DetectorConfig JSON must be an object")
    known = {f.name for f in dataclasses.fields(DetectorConfig)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(
            f"{path}: unknown DetectorConfig field(s): {sorted(unknown)}"
        )
    return DetectorConfig(**data)


def _config_as_dict(cfg) -> Mapping[str, object]:
    return dataclasses.asdict(cfg)


# ---------------------------------------------------------------------------
# Per-fixture run
# ---------------------------------------------------------------------------


def _peak_rss_mb() -> float:
    """Self-RSS high-water mark in MiB via ``resource.getrusage``.

    ``ru_maxrss`` is bytes on Darwin and kilobytes on Linux. We
    normalise to MiB by switching on platform.
    """
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        # Darwin reports bytes
        return float(rss) / (1024.0 * 1024.0)
    # Linux reports kilobytes
    return float(rss) / 1024.0


def _load_audio_for_fixture(fixture: CorpusFixture, *, sr: int = 22050):
    """Load (audio, sr, bass_audio_or_None) for one fixture.

    Local import of librosa to keep module import cheap.
    """
    import librosa
    import numpy as np  # noqa: F401  -- imported lazily on real runs

    y, sr_out = librosa.load(str(fixture.audio_path), sr=sr, mono=True)
    bass_y = None
    if fixture.bass_path is not None and fixture.bass_path.exists():
        bass_y, _ = librosa.load(str(fixture.bass_path), sr=sr_out, mono=True)
    return y, sr_out, bass_y


def _beats_for(y, sr) -> Optional["object"]:
    """Best-effort beat track; degrade gracefully on out-of-range tempo."""
    try:
        import librosa
        import numpy as np

        tempo_raw, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        tempo_val = float(np.asarray(tempo_raw).item())
        if 40 <= tempo_val <= 240 and len(beat_frames) >= 2:
            return librosa.frames_to_time(beat_frames, sr=sr)
    except Exception:
        return None
    return None


def _detect_one(fixture: CorpusFixture, config) -> tuple[list, float]:
    """Run the production detector once and return (predicted, wall_seconds).

    Local import of ``chord_detector`` to keep ``bench`` importable
    without dragging librosa/numpy into every test.
    """
    from tone_forge.analysis.chord_detector import detect_chords_from_audio

    y, sr, bass_y = _load_audio_for_fixture(fixture)
    beats_s = _beats_for(y, sr)
    start = time.perf_counter()
    predicted = detect_chords_from_audio(
        y, sr, bass_y=bass_y, beats_s=beats_s, config=config
    )
    wall = time.perf_counter() - start
    return predicted, wall


def _per_fixture_metrics(
    predicted: list,
    fixture: CorpusFixture,
    wall_seconds: float,
    peak_rss_mb: float,
) -> FixtureResult:
    ref = list(fixture.regions)
    dur = fixture.duration_s
    return FixtureResult(
        wcsr_triad_relaxed=triad_relaxed_wcsr_score(predicted, ref, dur),
        wcsr_strict=strict_wcsr_score(predicted, ref, dur),
        chord_error_rate=chord_error_rate(predicted, ref, dur),
        boundary_iou_0p5=boundary_iou(predicted, ref, dur, tol_s=0.5),
        region_stability_per_min=region_stability(predicted, dur),
        expected_calibration_error=expected_calibration_error(
            predicted, ref, dur, bins=10
        ),
        wall_seconds=wall_seconds,
        peak_rss_mb=peak_rss_mb,
    )


# ---------------------------------------------------------------------------
# Corpus aggregate
# ---------------------------------------------------------------------------


def _aggregate(per_fixture: Mapping[str, FixtureResult]) -> CorpusResult:
    n = len(per_fixture)
    if n == 0:
        # An empty corpus is a usable thing to report (e.g. dry-run
        # with require_audio=False on a machine without stems). All
        # means default to 0 to keep the JSON schema stable.
        return CorpusResult(
            n_fixtures=0,
            wcsr_triad_relaxed_mean=0.0,
            wcsr_strict_mean=0.0,
            chord_error_rate_mean=0.0,
            boundary_iou_0p5_mean=0.0,
            region_stability_per_min_mean=0.0,
            expected_calibration_error_mean=0.0,
            wall_seconds_mean=0.0,
            peak_rss_mb_max=0.0,
        )

    def _mean(getter) -> float:
        return sum(getter(r) for r in per_fixture.values()) / n

    return CorpusResult(
        n_fixtures=n,
        wcsr_triad_relaxed_mean=_mean(lambda r: r.wcsr_triad_relaxed),
        wcsr_strict_mean=_mean(lambda r: r.wcsr_strict),
        chord_error_rate_mean=_mean(lambda r: r.chord_error_rate),
        boundary_iou_0p5_mean=_mean(lambda r: r.boundary_iou_0p5),
        region_stability_per_min_mean=_mean(lambda r: r.region_stability_per_min),
        expected_calibration_error_mean=_mean(
            lambda r: r.expected_calibration_error
        ),
        wall_seconds_mean=_mean(lambda r: r.wall_seconds),
        peak_rss_mb_max=max(r.peak_rss_mb for r in per_fixture.values()),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _detect_git_sha() -> Optional[str]:
    """Return the current HEAD sha, or None if not a git checkout."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(Path(__file__).resolve().parent.parent),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def run_benchmark(
    *,
    config=None,
    corpus_dir: Optional[Path] = None,
    output_path: Optional[Path] = None,
    require_audio: bool = True,
    parent_baseline_run_id: Optional[str] = None,
    splits: Optional[tuple[str, ...]] = None,
    extra: Optional[Mapping[str, object]] = None,
) -> RunRecord:
    """Programmatic entry point used by ``bench.sweep`` and tests.

    ``config`` may be a ``DetectorConfig`` or ``None`` (uses default).
    ``splits`` (M2.5), when not ``None``, restricts the corpus to
    fixtures whose ``split`` field is in the tuple. The value is
    recorded on the ``RunRecord`` for downstream auditing.
    """
    if config is None:
        config = _load_detector_config(None)

    resolved_corpus_dir = (
        Path(corpus_dir) if corpus_dir is not None else DEFAULT_FIXTURES_DIR
    )
    fixtures = iter_corpus_fixtures(
        resolved_corpus_dir,
        require_audio=require_audio,
        splits=splits,
    )

    if splits is not None and not fixtures:
        raise ValueError(
            f"no fixtures match split filter {sorted(splits)!r} "
            f"in {resolved_corpus_dir}"
        )

    per_fixture: dict[str, FixtureResult] = {}
    total_start = time.perf_counter()
    for fix in fixtures:
        predicted, wall = _detect_one(fix, config)
        per_fixture[fix.name] = _per_fixture_metrics(
            predicted, fix, wall, _peak_rss_mb()
        )
    total_wall = time.perf_counter() - total_start

    corpus = _aggregate(per_fixture)

    run_id = uuid.uuid4().hex
    record = RunRecord(
        run_id=run_id,
        timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
        git_sha=_detect_git_sha(),
        config=_config_as_dict(config),
        corpus_dir=str(resolved_corpus_dir),
        per_fixture=per_fixture,
        corpus=corpus,
        wall_seconds_total=total_wall,
        parent_baseline_run_id=parent_baseline_run_id,
        splits=tuple(splits) if splits is not None else None,
        extra=dict(extra or {}),
    )

    if output_path is None:
        output_path = (
            Path(__file__).resolve().parent / "runs" / f"{run_id}.json"
        )
    dump_run_record(record, output_path)
    return record


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_summary(record: RunRecord) -> str:
    c = record.corpus
    lines = [
        f"run_id              : {record.run_id}",
        f"timestamp_utc       : {record.timestamp_utc}",
        f"git_sha             : {record.git_sha}",
        f"n_fixtures          : {c.n_fixtures}",
        f"wcsr_triad_relaxed  : {c.wcsr_triad_relaxed_mean:.4f}",
        f"wcsr_strict         : {c.wcsr_strict_mean:.4f}",
        f"chord_error_rate    : {c.chord_error_rate_mean:.4f}",
        f"boundary_iou_0p5    : {c.boundary_iou_0p5_mean:.4f}",
        f"region_stability/min: {c.region_stability_per_min_mean:.2f}",
        f"ece                 : {c.expected_calibration_error_mean:.4f}",
        f"wall_seconds_mean   : {c.wall_seconds_mean:.3f}",
        f"peak_rss_mb_max     : {c.peak_rss_mb_max:.1f}",
        f"wall_seconds_total  : {record.wall_seconds_total:.3f}",
        "per-fixture:",
    ]
    for name, fr in record.per_fixture.items():
        lines.append(
            f"  {name:<22} wcsr_tr={fr.wcsr_triad_relaxed:.4f} "
            f"strict={fr.wcsr_strict:.4f} "
            f"cer={fr.chord_error_rate:.4f} "
            f"wall={fr.wall_seconds:.2f}s"
        )
    return "\n".join(lines)


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m bench.benchmark",
        description="Run the chord-detector corpus benchmark.",
    )
    p.add_argument("--config", type=Path, default=None,
                   help="DetectorConfig JSON override")
    p.add_argument("--corpus", type=Path, default=None,
                   help="alternative fixtures dir")
    p.add_argument("--output", type=Path, default=None,
                   help="output path (default: bench/runs/<run_id>.json)")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--quiet", action="store_true",
                     help="suppress human-readable summary")
    grp.add_argument("--json-only", action="store_true",
                     help="emit only the RunRecord JSON path on stdout")
    p.add_argument("--no-require-audio", action="store_true",
                   help="include fixtures whose audio is not on disk "
                        "(produces empty per-fixture results for them)")
    p.add_argument("--split", action="append", default=None,
                   choices=("train", "val", "test", "holdout"),
                   help="restrict corpus to fixtures with this split "
                        "(may be repeated; default: all splits)")
    return p


def main(argv: list[str]) -> int:
    args = _build_argparser().parse_args(argv)
    config = _load_detector_config(args.config)
    record = run_benchmark(
        config=config,
        corpus_dir=args.corpus,
        output_path=args.output,
        require_audio=not args.no_require_audio,
        splits=tuple(args.split) if args.split else None,
    )
    output_path = args.output or (
        Path(__file__).resolve().parent / "runs" / f"{record.run_id}.json"
    )
    if args.json_only:
        sys.stdout.write(f"{output_path}\n")
    elif not args.quiet:
        sys.stdout.write(_format_summary(record) + "\n")
        sys.stdout.write(f"written: {output_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
