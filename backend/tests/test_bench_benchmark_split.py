"""``python -m bench.benchmark --split <name>`` tests (M2.5).

Verifies the `--split` CLI flag and its programmatic `splits=` kwarg:

* No flag -> all fixtures (M1 invariant; baseline behaviour unchanged).
* `--split test` against a corpus where every fixture is split=test
  produces the same RunRecord as the default invocation.
* `--split train` against a test-only corpus raises a clear error
  (no fixtures match).
* The RunRecord persists the splits filter so downstream comparisons
  can be checked apples-to-apples.

Uses synthesised single-fixture corpora in tmpdirs (M1's
``test_bench_smoke.py`` style) so the tests run in <1s and need no
on-disk audio fixtures.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest


def _write_minimal_wav(path: Path, *, sr: int, seconds: float) -> None:
    n = int(sr * seconds)
    t = np.arange(n) / sr
    sig = (
        np.sin(2 * np.pi * 261.63 * t)
        + np.sin(2 * np.pi * 329.63 * t)
        + np.sin(2 * np.pi * 392.00 * t)
    ) / 3.0
    pcm = (np.clip(sig, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _write_fixture(
    fixtures_dir: Path,
    *,
    name: str,
    audio_path: Path,
    duration_s: float,
    split: str,
) -> None:
    payload = {
        "schema_version": 2,
        "split": split,
        "duration_s": duration_s,
        "regions": [
            {"start": 0.0, "end": duration_s, "label": "C"},
        ],
        "regression_floor_triad_relaxed": 0.0,
        "source_audio_other_stem": str(audio_path),
    }
    (fixtures_dir / f"{name}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _build_test_only_corpus(tmp_path: Path) -> Path:
    """Two-fixture corpus where both fixtures are split=test."""
    pytest.importorskip("librosa")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()

    a_wav = audio_dir / "alpha.wav"
    b_wav = audio_dir / "beta.wav"
    _write_minimal_wav(a_wav, sr=22050, seconds=1.5)
    _write_minimal_wav(b_wav, sr=22050, seconds=1.5)

    _write_fixture(
        fixtures, name="alpha", audio_path=a_wav,
        duration_s=1.5, split="test",
    )
    _write_fixture(
        fixtures, name="beta", audio_path=b_wav,
        duration_s=1.5, split="test",
    )
    return fixtures


# ---------------------------------------------------------------------------
# run_benchmark(splits=...)
# ---------------------------------------------------------------------------


def test_run_benchmark_no_split_loads_all_fixtures(tmp_path: Path) -> None:
    """No `splits=` kwarg -> M1 behaviour (load every fixture)."""
    from bench.benchmark import run_benchmark

    fixtures = _build_test_only_corpus(tmp_path)
    record = run_benchmark(
        corpus_dir=fixtures,
        output_path=tmp_path / "run.json",
        require_audio=True,
    )
    assert record.corpus.n_fixtures == 2
    assert set(record.per_fixture) == {"alpha", "beta"}
    assert record.splits is None


def test_run_benchmark_split_test_matches_all(tmp_path: Path) -> None:
    """`splits=('test',)` on a test-only corpus loads every fixture."""
    from bench.benchmark import run_benchmark

    fixtures = _build_test_only_corpus(tmp_path)
    record = run_benchmark(
        corpus_dir=fixtures,
        output_path=tmp_path / "run.json",
        require_audio=True,
        splits=("test",),
    )
    assert record.corpus.n_fixtures == 2
    assert set(record.per_fixture) == {"alpha", "beta"}
    assert record.splits == ("test",)


def test_run_benchmark_split_empty_raises(tmp_path: Path) -> None:
    """`splits=('train',)` on a test-only corpus raises a clear error."""
    from bench.benchmark import run_benchmark

    fixtures = _build_test_only_corpus(tmp_path)
    with pytest.raises(ValueError, match="no fixtures match split filter"):
        run_benchmark(
            corpus_dir=fixtures,
            output_path=tmp_path / "run.json",
            require_audio=True,
            splits=("train",),
        )


def test_run_benchmark_split_multi_aggregates(tmp_path: Path) -> None:
    """Multiple splits union together at the loader level."""
    pytest.importorskip("librosa")
    from bench.benchmark import run_benchmark

    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    for name, split in (("t1", "test"), ("v1", "val"), ("h1", "holdout")):
        wav = audio_dir / f"{name}.wav"
        _write_minimal_wav(wav, sr=22050, seconds=1.0)
        _write_fixture(
            fixtures, name=name, audio_path=wav,
            duration_s=1.0, split=split,
        )

    record = run_benchmark(
        corpus_dir=fixtures,
        output_path=tmp_path / "run.json",
        require_audio=True,
        splits=("test", "val"),
    )
    assert record.corpus.n_fixtures == 2
    assert set(record.per_fixture) == {"t1", "v1"}
    assert record.splits == ("test", "val")


def test_run_benchmark_splits_round_trip_in_json(tmp_path: Path) -> None:
    """Splits filter persists into the on-disk RunRecord JSON."""
    from bench.benchmark import run_benchmark
    from bench.store import load_run_record

    fixtures = _build_test_only_corpus(tmp_path)
    out = tmp_path / "run.json"
    record = run_benchmark(
        corpus_dir=fixtures,
        output_path=out,
        require_audio=True,
        splits=("test",),
    )

    reloaded = load_run_record(out)
    assert reloaded.splits == ("test",)
    assert reloaded.run_id == record.run_id

    # And the raw JSON also has a `splits: ["test"]` list.
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["splits"] == ["test"]


def test_run_benchmark_no_splits_json_is_null(tmp_path: Path) -> None:
    from bench.benchmark import run_benchmark

    fixtures = _build_test_only_corpus(tmp_path)
    out = tmp_path / "run.json"
    run_benchmark(
        corpus_dir=fixtures,
        output_path=out,
        require_audio=True,
    )
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["splits"] is None


# ---------------------------------------------------------------------------
# CLI (--split flag)
# ---------------------------------------------------------------------------


def test_cli_split_flag_parses() -> None:
    from bench.benchmark import _build_argparser

    ap = _build_argparser()
    args = ap.parse_args(["--split", "test"])
    assert args.split == ["test"]
    args2 = ap.parse_args(["--split", "test", "--split", "val"])
    assert args2.split == ["test", "val"]
    args3 = ap.parse_args([])
    assert args3.split is None


def test_cli_split_invalid_choice_rejected() -> None:
    from bench.benchmark import _build_argparser

    ap = _build_argparser()
    with pytest.raises(SystemExit):
        ap.parse_args(["--split", "bogus"])


def test_cli_main_writes_record_with_split(tmp_path: Path) -> None:
    from bench import benchmark as bm

    fixtures = _build_test_only_corpus(tmp_path)
    out = tmp_path / "run.json"
    rc = bm.main([
        "--corpus", str(fixtures),
        "--output", str(out),
        "--quiet",
        "--split", "test",
    ])
    assert rc == 0
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["splits"] == ["test"]
    assert raw["corpus"]["n_fixtures"] == 2


def test_cli_main_empty_split_exits_nonzero(tmp_path: Path) -> None:
    """`--split train` against a test-only corpus surfaces the
    ValueError to the caller (uncaught -> SystemExit with the trace)."""
    from bench import benchmark as bm

    fixtures = _build_test_only_corpus(tmp_path)
    out = tmp_path / "run.json"
    with pytest.raises(ValueError, match="no fixtures match split filter"):
        bm.main([
            "--corpus", str(fixtures),
            "--output", str(out),
            "--quiet",
            "--split", "train",
        ])
